"""Japan servicehub bootstrap and TLS MQTT command completion.

Only authenticated bootstrap data from the fixed Japan servicehub is accepted.
No requests or MQTT connections occur until prepare() is called. Vendor messages,
identifiers, credentials, and payloads are never logged by this helper.
"""

from __future__ import annotations

import json
import re
import ssl
import threading
from collections import OrderedDict, deque
from collections.abc import Callable
from contextlib import suppress
from copy import deepcopy
from types import TracebackType
from typing import Any, Literal, Self, cast, overload

import paho.mqtt.client as mqtt
import requests

from .ApiImpl import ApiImplSession
from .const import ORDER_STATUS
from .exceptions import (
    APIError,
    AuthenticationError,
    InvalidAPIResponseError,
    RateLimitingError,
    RequestTimeoutError,
    ServiceTemporaryUnavailable,
)
from .mqtt_client import MqttTransport
from .mqtt_service_hub import (
    ServiceHubProviderMixin,
    ServiceHubPushMessage,
    build_service_hub_status_topics,
    parse_service_hub_message,
)
from .Token import Token
from .Vehicle import Vehicle


class JapanMqttTransportPool:
    """API-scoped ownership of one connection per authenticated broker client."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._connections: dict[tuple[str | int | None, ...], _JapanConnection] = {}

    def acquire(
        self, helper: JapanMqttSession, host: str, port: int, client_id: str
    ) -> _JapanConnection:
        key = (
            helper._token.access_token,
            helper._token.device_id,
            helper._registration_uuid,
            host,
            port,
            client_id,
        )
        with self._lock:
            connection = self._connections.get(key)
            if connection is None or connection.failed:
                credentials = helper.get_push_credentials(helper._token)
                if credentials is None:
                    raise AuthenticationError("Japan MQTT credentials are missing")
                connection = _JapanConnection(
                    self, key, host, port, client_id, credentials
                )
                self._connections[key] = connection
            connection.helpers.add(helper)
            return connection

    def release(self, connection: _JapanConnection, helper: JapanMqttSession) -> None:
        with self._lock:
            connection.helpers.discard(helper)
            if connection.helpers:
                return
            if self._connections.get(connection.key) is connection:
                del self._connections[connection.key]
            connection.failed = True
        connection.transport.disconnect(stop_loop_first=False)


class _JapanConnection:
    def __init__(
        self,
        pool: JapanMqttTransportPool,
        key: tuple[str | int | None, ...],
        host: str,
        port: int,
        client_id: str,
        credentials: tuple[str, str],
    ) -> None:
        self.pool, self.key = pool, key
        self.helpers: set[JapanMqttSession] = set()
        self.failed = False
        self.started = False
        self.transport = MqttTransport(
            on_connect=self._on_connect,
            on_disconnect=self._on_disconnect,
            on_message_details=self._on_message,
            on_loop_start_failure=self._on_loop_failure,
        )
        self.transport.configure(
            host,
            port,
            client_id,
            credentials[0],
            credentials[1],
            reconnect_on_failure=False,
            tls_context=ssl.create_default_context(),
        )

    def start(self, helper: JapanMqttSession) -> None:
        with self.pool._lock:
            if self.failed or helper not in self.helpers:
                raise APIError("Japan MQTT connection is closed")
            start = not self.started
            self.started = True
            subscribe = not start and self.transport.is_connected
        if start:
            try:
                self.transport.connect()
            except Exception:
                self._on_disconnect("connect failed")
                raise
        elif subscribe:
            helper._on_connect()

    def _snapshot(self) -> list[JapanMqttSession]:
        with self.pool._lock:
            return list(self.helpers)

    def _on_connect(self) -> None:
        for helper in self._snapshot():
            helper._on_connect()

    def _on_disconnect(self, reason: str) -> None:
        with self.pool._lock:
            self.failed = True
        for helper in self._snapshot():
            helper._on_disconnect(reason)

    def _on_loop_failure(self) -> None:
        with self.pool._lock:
            self.failed = True
        for helper in self._snapshot():
            helper._fail("Japan MQTT network loop could not start")

    def _on_message(self, message: mqtt.MQTTMessage) -> None:
        for helper in self._snapshot():
            if message.topic in helper._topics:
                helper._on_message(None, None, message)


class JapanMqttSession(ServiceHubProviderMixin):
    """One vehicle's bounded command-completion session; caller must close it."""

    SERVICEHUB_URL = "https://egw-svchub-ccs-h-jp.ap-northeast.hmgmobility.com:31010"
    BROKER_SUFFIX = ".ap-northeast.hmgmobility.com"
    TOPIC_PREFIX = "service/phone/_/"
    CONNECT_TIMEOUT = 30
    MAX_RESULTS = 64
    MAX_STATE_UPDATES = 64
    MAX_MESSAGE_BYTES = 2 * 1024 * 1024
    SERVICE_HUB_IDENTITY_FROM_METADATA = True
    SERVICE_HUB_METADATA_OPTIONAL = True

    def __init__(
        self,
        token: Token,
        vehicle_id: str,
        registration_uuid: str,
        headers_factory: Callable[[], dict[str, str]],
        *,
        transport_pool: JapanMqttTransportPool | None = None,
        vehicle: Vehicle | None = None,
    ) -> None:
        if not isinstance(vehicle_id, str) or not re.fullmatch(
            r"[A-Za-z0-9_-]+", vehicle_id
        ):
            raise InvalidAPIResponseError("Japan MQTT vehicle identifier is invalid")
        if not isinstance(registration_uuid, str) or not re.fullmatch(
            r"[A-Za-z0-9_-]{1,128}", registration_uuid
        ):
            raise InvalidAPIResponseError("Japan MQTT registration identity is missing")
        if (
            not isinstance(token.access_token, str)
            or not token.access_token.startswith("Bearer ")
            or not token.access_token[7:]
        ):
            raise AuthenticationError("Japan MQTT access token is missing")
        self._token = token
        self._vehicle_id = vehicle_id
        self._vehicle = vehicle if vehicle is not None else Vehicle(id=vehicle_id)
        self._service_hub_tid: str | None = None
        self._service_hub_session: requests.Session | None = None
        self._registered_client_id: str | None = None
        self._registration_uuid = registration_uuid
        self._headers_factory = headers_factory
        self._http_session = ApiImplSession()
        self._transport: MqttTransport | None = None
        self._transport_pool = transport_pool or JapanMqttTransportPool()
        self._connection: _JapanConnection | None = None
        self._status_callback: Callable[[str, str], None] | None = None
        self._connection_callback: Callable[[bool], None] | None = None
        self._reported_connection: bool | None = None
        self._consumer_leases: set[str] = set()
        self._condition = threading.Condition()
        self._prepare_lock = threading.Lock()
        self._ready = False
        self._subscription_requested = False
        self._closed = False
        self._error: str | None = None
        self._results: OrderedDict[str, ORDER_STATUS] = OrderedDict()
        self._tracked: set[str] = set()
        self._latest_state: dict[str, Any] | None = None
        self._state_updates: deque[dict[str, Any]] = deque(
            maxlen=self.MAX_STATE_UPDATES
        )
        self._topics: tuple[str, ...] = ()
        self._pending_context: dict[str, str] = {}
        self._request_context: dict[str, str] = {}
        self._diagnostics = dict.fromkeys(
            (
                "messages_received",
                "retained_messages",
                "retained_vss_messages",
                "retained_result_messages",
                "retained_connection_messages",
                "retained_other_topic_messages",
                "oversized_messages",
                "malformed_messages",
                "vss_messages",
                "result_messages",
                "connection_messages",
                "other_topic_messages",
                "missing_sid_messages",
                "unknown_result_messages",
                "matched_results",
                "unmatched_result_messages",
                "commands_tracked",
            ),
            0,
        )
        self._last_status = "not_started"

    @property
    def _service_hub_brand(self) -> str:
        return "H"

    def _service_hub_registration_uuid(self, token: Token) -> str:
        return self._registration_uuid + "_BLU"

    def _service_hub_registered_client_id(self, token: Token) -> str:
        return self._registered_client_id or ""

    def _service_hub_request(
        self,
        token: Token,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        allow_not_found: bool = False,
    ) -> dict[str, Any] | None:
        kwargs: dict[str, Any] = {}
        if params is not None:
            kwargs["params"] = params
        if json is not None:
            kwargs["json"] = json
        if allow_not_found:
            return self._http(
                method, path, headers=headers, allow_not_found=True, **kwargs
            )
        return cast(dict[str, Any], self._http(method, path, headers=headers, **kwargs))

    def _service_hub_broker_info(self, data: dict[str, Any]) -> dict[str, Any]:
        host, port = self._broker(data)
        tid = data.get("tid")
        if (
            not isinstance(tid, str)
            or not tid.strip()
            or len(tid) > 1024
            or any(ord(character) < 32 or ord(character) > 126 for character in tid)
        ):
            raise InvalidAPIResponseError("Japan MQTT transaction identity is missing")
        self._service_hub_tid = tid
        return {"mqtt_host": host, "mqtt_port": port}

    def _service_hub_client_id(self, data: dict[str, Any]) -> str:
        value = data.get("clientId")
        if not isinstance(value, str) or not re.fullmatch(
            r"[A-Za-z0-9_.:-]{1,256}", value
        ):
            raise InvalidAPIResponseError("Japan MQTT client identity is invalid")
        self._registered_client_id = value
        return value

    def _service_hub_apply_metadata(
        self, data: dict[str, Any], vehicle: Vehicle
    ) -> None:
        vehicle.mqtt_vehicle_id = self._metadata_vehicle_id(data)

    def _service_hub_vehicle_id_request(self, vehicle: Vehicle) -> dict[str, Any]:
        return {"json": self._service_hub_vehicle_params(vehicle)}

    def _service_hub_validate_vehicle_identity(self, vehicle: Vehicle) -> None:
        if not vehicle.mqtt_vehicle_id:
            raise InvalidAPIResponseError("Japan MQTT vehicle metadata is missing")

    def get_push_topics(self, token: Token, vehicle: Vehicle) -> list[str]:
        if not vehicle.mqtt_vehicle_id or not self._registered_client_id:
            return []
        return build_service_hub_status_topics(
            vehicle.mqtt_vehicle_id, result_client_id=self._registered_client_id
        )

    def parse_push_message(
        self, topic: str, payload: bytes
    ) -> ServiceHubPushMessage | None:
        if self._topics and topic not in self._topics:
            return None
        return parse_service_hub_message(topic, payload, include_result=True)

    @property
    def is_connected(self) -> bool:
        with self._condition:
            return self._ready and self._error is None and not self._closed

    def set_status_callback(self, callback: Callable[[str, str], None] | None) -> None:
        with self._condition:
            self._status_callback = callback

    def set_connection_change_callback(
        self, callback: Callable[[bool], None] | None
    ) -> None:
        with self._condition:
            self._connection_callback = callback
            self._reported_connection = None

    def start_push(
        self,
        status_callback: Callable[[str, str], None] | None = None,
        connection_callback: Callable[[bool], None] | None = None,
    ) -> bool:
        self.set_status_callback(status_callback)
        self.set_connection_change_callback(connection_callback)
        self._prepare_consumer("push")
        self._report_connection(True)
        return self.is_connected

    def stop_push(self) -> None:
        """Release push ownership, retaining only an existing command consumer."""
        with self._condition:
            self._consumer_leases.discard("push")
            self._status_callback = None
            self._connection_callback = None
            self._reported_connection = None
            close = not self._consumer_leases
            if close:
                self._closed = True
        if close:
            self.close()

    def _report_connection(self, connected: bool) -> None:
        with self._condition:
            callback = self._connection_callback
            if callback is None or self._reported_connection is connected:
                return
            self._reported_connection = connected
        with suppress(Exception):
            callback(connected)

    @overload
    def _http(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        allow_not_found: Literal[True],
        **kwargs: Any,
    ) -> dict[str, Any] | None: ...

    @overload
    def _http(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        allow_not_found: Literal[False] = False,
        **kwargs: Any,
    ) -> dict[str, Any]: ...

    def _http(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        allow_not_found: bool = False,
        **kwargs: Any,
    ) -> dict[str, Any] | None:
        with self._condition:
            self._raise_if_closed()
        stage = {
            "/api/v3/servicehub/device/host": "host discovery",
            "/api/v3/servicehub/device/register": "client registration",
            "/api/v3/servicehub/vehicles/metadatalist": "vehicle metadata",
            "/api/v3/servicehub/vehicleId": "vehicle registration",
            "/api/v3/servicehub/device/protocol": "protocol registration",
        }.get(path, "bootstrap")
        request_headers = self._headers_factory()
        if headers:
            request_headers.update(headers)
        try:
            response = self._http_session.request(
                method,
                self.SERVICEHUB_URL + path,
                headers=request_headers,
                allow_redirects=False,
                **kwargs,
            )
            with self._condition:
                self._raise_if_closed()
            if response.status_code == 404 and allow_not_found:
                return None
            if response.status_code in (401, 403):
                raise AuthenticationError("Japan MQTT authentication was rejected")
            if response.status_code == 429:
                raise RateLimitingError("Japan MQTT service rate limit reached")
            if response.status_code >= 500:
                raise ServiceTemporaryUnavailable("Japan MQTT service is unavailable")
            if not 200 <= response.status_code < 300:
                raise APIError("Japan MQTT bootstrap request failed")
            # The app maps this response to an empty acknowledgement object
            # without parsing ResponseBody (widget/b.java, case 21). A 204 or
            # empty/text 200 is valid here; discovery/identity responses still
            # require JSON and validated fields before MQTT can connect.
            if method == "POST" and path == "/api/v3/servicehub/device/protocol":
                return {}
            result = response.json()
        except (requests.exceptions.Timeout, RequestTimeoutError):
            raise RequestTimeoutError("Japan MQTT bootstrap timed out") from None
        except ValueError:
            raise InvalidAPIResponseError(
                f"Japan MQTT bootstrap returned invalid JSON "
                f"({stage}; HTTP {response.status_code})"
            ) from None
        except requests.exceptions.RequestException:
            raise APIError("Japan MQTT bootstrap connection failed") from None
        if not isinstance(result, dict):
            raise InvalidAPIResponseError(
                f"Japan MQTT bootstrap returned an invalid object "
                f"({stage}; HTTP {response.status_code})"
            )
        if path == "/api/v3/servicehub/device/host":
            # The app copies the transaction ID from the HTTP header into
            # MQTTHostResponse; it is not necessarily present in the JSON body.
            header_tid = response.headers.get("tid")
            if header_tid is not None:
                result = {**result, "tid": header_tid}
        return result

    @classmethod
    def _broker(cls, payload: dict[str, Any]) -> tuple[str, int]:
        broker = payload.get("mqtt")
        if not isinstance(broker, dict):
            raise InvalidAPIResponseError("Japan MQTT broker configuration is missing")
        host, port = broker.get("host"), broker.get("port")
        if (
            not isinstance(host, str)
            or host != host.lower()
            or not host.endswith(cls.BROKER_SUFFIX)
            or not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host)
            or any(
                not label
                or len(label) > 63
                or label.startswith("-")
                or label.endswith("-")
                for label in host.split(".")
            )
            or len(host) > 253
            or type(port) is not int
            or not 1 <= port <= 65535
            or broker.get("ssl") is not True
        ):
            raise InvalidAPIResponseError(
                "Japan MQTT broker configuration is not trusted"
            )
        return host, port

    @staticmethod
    def _metadata_vehicle_id(metadata: dict[str, Any] | None) -> str | None:
        if metadata is None:
            return None
        vehicles = metadata.get("vehicles")
        if not isinstance(vehicles, list):
            raise InvalidAPIResponseError("Japan MQTT vehicle metadata is invalid")
        for unit in ("ccu", "tmu"):
            candidates = [
                v.get("vehicleId")
                for v in vehicles
                if isinstance(v, dict) and v.get("unit") == unit and v.get("vehicleId")
            ]
            if candidates:
                vehicle_id = candidates[-1]
                if not isinstance(vehicle_id, str) or not re.fullmatch(
                    r"[A-Za-z0-9_-]+", vehicle_id
                ):
                    raise InvalidAPIResponseError(
                        "Japan MQTT topic identity is invalid"
                    )
                return vehicle_id
        return None

    def prepare(self) -> None:
        """Register and await acknowledged subscriptions before submitting commands."""
        self._prepare_consumer("commands")

    def _prepare_consumer(self, consumer: str) -> None:
        with self._condition:
            self._raise_if_closed()
            self._consumer_leases.add(consumer)
        # One bootstrap may create a client. close() deliberately does not take
        # this lock: it must interrupt a waiting bootstrap rather than wait for it.
        with self._prepare_lock:
            self._prepare()

    def _prepare(self) -> None:
        with self._condition:
            self._raise_if_closed()
            if self._ready and self._error is None:
                return
        try:
            with self._condition:
                self._raise_error()
            broker = self.get_push_broker_info(self._token)
            if broker is None:
                raise InvalidAPIResponseError(
                    "Japan MQTT broker configuration is missing"
                )
            host, port = broker["mqtt_host"], broker["mqtt_port"]
            registration = self.register_push_client(self._token)
            if registration is None:
                raise InvalidAPIResponseError("Japan MQTT client identity is invalid")
            client_id = registration["client_id"]
            self.get_push_vehicle_identity(self._token, self._vehicle)
            broker_vehicle_id = cast(str, self._vehicle.mqtt_vehicle_id)
            self.register_push_vehicle(self._token, self._vehicle)
            self._topics = tuple(self.get_push_topics(self._token, self._vehicle))
            self._pending_context = {
                "clientId": client_id,
                "vehicleId": broker_vehicle_id,
            }
            with self._condition:
                # Recheck after HTTP bootstrap; unload may have closed the
                # session while a response was in flight. Publish/start the
                # client under the same lock used by close().
                self._raise_if_closed()
                connection = self._transport_pool.acquire(self, host, port, client_id)
                self._connection = connection
                self._transport = connection.transport
                connection.start(self)
                if not self._condition.wait_for(
                    lambda: self._ready or self._error is not None or self._closed,
                    self.CONNECT_TIMEOUT,
                ):
                    raise RequestTimeoutError("Japan MQTT subscription timed out")
                self._raise_if_closed()
                self._raise_error()
        except Exception as err:
            self.close()
            if isinstance(
                err,
                (
                    APIError,
                    AuthenticationError,
                    InvalidAPIResponseError,
                    RateLimitingError,
                    RequestTimeoutError,
                    ServiceTemporaryUnavailable,
                ),
            ):
                raise
            raise APIError("Japan MQTT connection setup failed") from None

    def _raise_error(self) -> None:
        if self._error is not None:
            raise APIError(self._error)

    def _raise_if_closed(self) -> None:
        if self._closed:
            raise APIError("Japan MQTT session is closed")

    def _fail(self, message: str) -> None:
        with self._condition:
            if self._closed:
                return
            self._error = message
            self._last_status = "connection_error"
            self._condition.notify_all()

    def _on_connect(self) -> None:
        with self._condition:
            if self._closed or self._subscription_requested:
                return
            if self._transport is not None:
                self._subscription_requested = True
                self._transport.subscribe(
                    list(self._topics),
                    qos=1,
                    on_success=self._on_subscribe,
                    on_failure=self._on_subscribe_failure,
                )

    def _on_disconnect(self, reason: str) -> None:
        with self._condition:
            if not self._closed:
                self._error = (
                    "Japan MQTT connection was rejected"
                    if reason.startswith("broker refused")
                    else "Japan MQTT connection failed"
                    if reason.startswith("connect failed")
                    else "Japan MQTT connection was lost"
                )
                self._last_status = "connection_error"
            self._ready = False
            self._condition.notify_all()
        self._report_connection(False)

    def _on_subscribe(self) -> None:
        with self._condition:
            if self._closed:
                return
            self._request_context = dict(self._pending_context)
            self._ready = True
            self._condition.notify_all()
        self._report_connection(True)

    def _on_subscribe_failure(self, reason: str) -> None:
        self._fail(
            {
                "subscription failed": "Japan MQTT subscription failed",
                "subscription rejected": "Japan MQTT subscription was rejected",
            }.get(reason, "Japan MQTT subscriptions were not granted")
        )

    def _on_message(
        self, client: object, userdata: object, message: mqtt.MQTTMessage
    ) -> None:
        status_received = False
        with self._condition:
            self._diagnostics["messages_received"] += 1
            if message.retain:
                self._diagnostics["retained_messages"] += 1
                # Keep historical snapshots unable to complete a new command,
                # while identifying their stream without exposing topic IDs.
                retained_counter = "retained_other_topic_messages"
                if self._topics:
                    for topic, counter in zip(
                        self._topics,
                        (
                            "retained_vss_messages",
                            "retained_result_messages",
                            "retained_connection_messages",
                        ),
                        strict=True,
                    ):
                        if message.topic == topic:
                            retained_counter = counter
                            break
                self._diagnostics[retained_counter] += 1
                return
            if len(message.payload) > self.MAX_MESSAGE_BYTES:
                self._diagnostics["oversized_messages"] += 1
                return
        try:
            payload = json.loads(message.payload)
        except (ValueError, UnicodeDecodeError, RecursionError):
            with self._condition:
                self._diagnostics["malformed_messages"] += 1
            return
        if not isinstance(payload, dict):
            with self._condition:
                self._diagnostics["malformed_messages"] += 1
            return
        with self._condition:
            if self._closed or not self._topics:
                return
            if message.topic == self._topics[0]:
                self._diagnostics["vss_messages"] += 1
                state = (
                    payload.get("state", {}).get("Vehicle")
                    if isinstance(payload.get("state"), dict)
                    else None
                )
                if isinstance(state, dict):
                    self._latest_state = deepcopy(state)
                    self._state_updates.append(deepcopy(state))
                    status_received = True
            elif message.topic == self._topics[1]:
                self._diagnostics["result_messages"] += 1
                sid = payload.get("sid")
                if not isinstance(sid, str) or not sid:
                    self._diagnostics["missing_sid_messages"] += 1
                    return
                if payload.get("res-msg") not in ("S", "F"):
                    self._diagnostics["unknown_result_messages"] += 1
                if sid not in self._tracked:
                    self._diagnostics["unmatched_result_messages"] += 1
                result = (
                    ORDER_STATUS.SUCCESS
                    if payload.get("res-msg") == "S"
                    else ORDER_STATUS.FAILED
                )
                if sid in self._tracked and sid not in self._results:
                    self._diagnostics["matched_results"] += 1
                    self._last_status = (
                        "success" if result == ORDER_STATUS.SUCCESS else "failed"
                    )
                self._results.setdefault(sid, result)
                self._results.move_to_end(sid)
                while len(self._results) > self.MAX_RESULTS:
                    untracked = next(
                        (key for key in self._results if key not in self._tracked), None
                    )
                    if untracked is None:
                        break
                    del self._results[untracked]
            elif message.topic == self._topics[2]:
                self._diagnostics["connection_messages"] += 1
            else:
                self._diagnostics["other_topic_messages"] += 1
            self._condition.notify_all()
            callback = self._status_callback
        if status_received and callback is not None:
            with suppress(Exception):
                callback(self._vehicle_id, "Status")

    def track(self, action_id: str) -> None:
        """Track the HTTP acknowledgement; already received results are retained."""
        if not isinstance(action_id, str) or not action_id:
            raise InvalidAPIResponseError("Japan command acknowledgement is missing")
        with self._condition:
            self._raise_error()
            if not self._ready or self._closed:
                raise APIError("Japan MQTT session is not subscribed")
            if (
                action_id not in self._tracked
                and len(self._tracked) >= self.MAX_RESULTS
            ):
                raise APIError("Japan MQTT session command limit reached")
            if action_id not in self._tracked:
                self._diagnostics["commands_tracked"] += 1
                if action_id in self._results:
                    self._diagnostics["matched_results"] += 1
            self._tracked.add(action_id)
            result = self._results.get(action_id)
            self._last_status = (
                "pending"
                if result is None
                else "success"
                if result == ORDER_STATUS.SUCCESS
                else "failed"
            )

    def status(self, action_id: str, timeout: float = 0) -> ORDER_STATUS:
        """Return correlated completion; bounded waits never assume success."""
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not 0 <= timeout <= 120
        ):
            raise APIError("Japan MQTT wait timeout is invalid")
        with self._condition:
            if action_id not in self._tracked:
                raise APIError("Japan MQTT action is not tracked")
            self._raise_error()
            if action_id in self._results:
                return self._results[action_id]
            if timeout == 0:
                return ORDER_STATUS.PENDING
            ready = self._condition.wait_for(
                lambda: (
                    action_id in self._results
                    or self._error is not None
                    or self._closed
                ),
                timeout,
            )
            self._raise_error()
            if self._closed:
                raise APIError("Japan MQTT session is closed")
            if not ready:
                self._last_status = "timeout"
            return self._results[action_id] if ready else ORDER_STATUS.TIMEOUT

    @property
    def diagnostics(self) -> dict[str, int | str | bool]:
        """Return identifier-free counters, retained after session close."""
        with self._condition:
            return {
                **self._diagnostics,
                "last_status": self._last_status,
                "ready": self._ready,
                "closed": self._closed,
                "pending_state_updates": len(self._state_updates),
            }

    @property
    def request_context(self) -> dict[str, str]:
        """Copy validated API header identities after successful subscription.

        The app retains these identities beyond broker disconnection. Keeping
        them after close supports subsequent stored-image requests without
        retaining any MQTT authentication material in the returned mapping.
        """
        with self._condition:
            return dict(self._request_context)

    @property
    def closed(self) -> bool:
        """Whether the caller must create a replacement session."""
        with self._condition:
            return self._closed

    @property
    def failed(self) -> bool:
        """Whether a connection error requires a replacement session."""
        with self._condition:
            return self._error is not None

    @property
    def latest_state(self) -> dict[str, Any] | None:
        """Copy the latest received vehicle state; never expose mutable callback data."""
        with self._condition:
            return deepcopy(self._latest_state)

    def drain_state_updates(self) -> list[dict[str, Any]]:
        """Copy and consume queued passive updates without making a network request."""
        with self._condition:
            updates = deepcopy(list(self._state_updates))
            self._state_updates.clear()
            return updates

    def close(self, *, notify_push: bool = False) -> None:
        """Stop network activity and release all session data; safe to repeat."""
        with self._condition:
            callback = (
                self._connection_callback
                if notify_push and self._reported_connection is not False
                else None
            )
            self._closed = True
            self._consumer_leases.clear()
            self._ready = False
            self._results.clear()
            self._tracked.clear()
            self._latest_state = None
            self._state_updates.clear()
            self._pending_context.clear()
            connection, self._connection = self._connection, None
            self._transport = None
            self._status_callback = None
            self._connection_callback = None
            self._reported_connection = False
            self._condition.notify_all()
        if connection is not None:
            with suppress(Exception):
                self._transport_pool.release(connection, self)
        self._http_session.close()
        if callback is not None:
            with suppress(Exception):
                callback(False)

    def __enter__(self) -> Self:
        self.prepare()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()
