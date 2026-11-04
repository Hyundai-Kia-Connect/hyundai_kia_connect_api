"""Japan servicehub bootstrap and TLS MQTT command completion.

Only authenticated bootstrap data from the fixed Japan servicehub is accepted.
No requests or MQTT connections occur until prepare() is called. Vendor messages,
identifiers, credentials, and payloads are never logged by this helper.
"""

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
from .Token import Token


class JapanMqttSession:
    """One vehicle's bounded command-completion session; caller must close it."""

    SERVICEHUB_URL = "https://egw-svchub-ccs-h-jp.ap-northeast.hmgmobility.com:31010"
    BROKER_SUFFIX = ".ap-northeast.hmgmobility.com"
    TOPIC_PREFIX = "service/phone/_/"
    CONNECT_TIMEOUT = 30
    MAX_RESULTS = 64
    MAX_STATE_UPDATES = 64
    MAX_MESSAGE_BYTES = 2 * 1024 * 1024

    def __init__(
        self,
        token: Token,
        vehicle_id: str,
        registration_uuid: str,
        headers_factory: Callable[[], dict[str, str]],
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
        self._registration_uuid = registration_uuid
        self._headers_factory = headers_factory
        self._http_session = ApiImplSession()
        self._transport: MqttTransport | None = None
        self._condition = threading.Condition()
        self._prepare_lock = threading.Lock()
        self._ready = False
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
            host_info = self._http("GET", "/api/v3/servicehub/device/host")
            host, port = self._broker(host_info)
            tid = host_info.get("tid")
            if (
                not isinstance(tid, str)
                or not tid.strip()
                or len(tid) > 1024
                or any(ord(character) < 32 or ord(character) > 126 for character in tid)
            ):
                raise InvalidAPIResponseError(
                    "Japan MQTT transaction identity is missing"
                )
            registration = self._http(
                "POST",
                "/api/v3/servicehub/device/register",
                headers={"tid": tid},
                json={"unit": "mobile", "uuid": self._registration_uuid + "_BLU"},
            )
            client_id = registration.get("clientId")
            if not isinstance(client_id, str) or not re.fullmatch(
                r"[A-Za-z0-9_.:-]{1,256}", client_id
            ):
                raise InvalidAPIResponseError("Japan MQTT client identity is invalid")
            headers = {"client-id": client_id, "tid": tid}
            params = {"carId": self._vehicle_id, "brand": "H"}
            metadata = self._http(
                "GET",
                "/api/v3/servicehub/vehicles/metadatalist",
                headers=headers,
                params=params,
                allow_not_found=True,
            )
            broker_vehicle_id = self._metadata_vehicle_id(metadata)
            if broker_vehicle_id is None:
                self._http(
                    "POST", "/api/v3/servicehub/vehicleId", headers=headers, json=params
                )
                metadata = self._http(
                    "GET",
                    "/api/v3/servicehub/vehicles/metadatalist",
                    headers=headers,
                    params=params,
                )
                broker_vehicle_id = self._metadata_vehicle_id(metadata)
            if broker_vehicle_id is None:
                raise InvalidAPIResponseError("Japan MQTT vehicle metadata is missing")
            self._http(
                "POST",
                "/api/v3/servicehub/device/protocol",
                headers=headers,
                json={
                    **params,
                    "protocolId": "statesync.vehicle.ccu.update",
                    "protocols": [
                        "service.phone.vss",
                        "service.phone.connection",
                        "service.phone.res",
                    ],
                },
            )
            self._topics = (
                self.TOPIC_PREFIX + "vss/" + broker_vehicle_id,
                self.TOPIC_PREFIX + "res/" + client_id,
                self.TOPIC_PREFIX + "connection/" + broker_vehicle_id,
            )
            self._pending_context = {
                "clientId": client_id,
                "vehicleId": broker_vehicle_id,
            }
            with self._condition:
                # Recheck after HTTP bootstrap; unload may have closed the
                # session while a response was in flight. Publish/start the
                # client under the same lock used by close().
                self._raise_if_closed()
                transport = MqttTransport(
                    on_connect=self._on_connect,
                    on_disconnect=self._on_disconnect,
                    on_message_details=lambda message: self._on_message(
                        None, None, message
                    ),
                    on_subscribe_success=self._on_subscribe,
                    on_subscribe_failure=self._on_subscribe_failure,
                    on_loop_start_failure=lambda: self._fail(
                        "Japan MQTT network loop could not start"
                    ),
                )
                self._transport = transport
                transport.configure(
                    host,
                    port,
                    client_id,
                    client_id,
                    cast(str, self._token.access_token)[7:],
                    reconnect_on_failure=False,
                    tls_context=ssl.create_default_context(),
                )
                transport.connect()
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
            if self._closed:
                return
            if self._transport is not None:
                self._transport.subscribe(list(self._topics), qos=1)

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

    def _on_subscribe(self) -> None:
        with self._condition:
            if self._closed:
                return
            self._request_context = dict(self._pending_context)
            self._ready = True
            self._condition.notify_all()

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

    def close(self) -> None:
        """Stop network activity and release all session data; safe to repeat."""
        with self._condition:
            self._closed = True
            self._ready = False
            self._results.clear()
            self._tracked.clear()
            self._latest_state = None
            self._state_updates.clear()
            self._pending_context.clear()
            transport, self._transport = self._transport, None
            self._condition.notify_all()
        if transport is not None:
            with suppress(Exception):
                transport.disconnect(stop_loop_first=False)
        self._http_session.close()

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
