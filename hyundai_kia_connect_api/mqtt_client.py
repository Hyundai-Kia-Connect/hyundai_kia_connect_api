# pylint:disable=logging-fstring-interpolation
"""mqtt_client.py — schema-neutral MQTT transport for push deliveries.

Uses paho-mqtt 2.x (matches HA 2026.6+). Receive-only: commands are sent
through regional HTTP APIs. Topics and message parsing belong to providers.

Thread safety: paho-mqtt runs its own background network thread and
dispatches callbacks on it. Callbacks must be thread-safe.
"""

import logging
import ssl
import threading
import uuid
from collections.abc import Callable
from typing import Any

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class MqttTransport:
    """Receive-only MQTT transport around paho-mqtt 2.x."""

    def __init__(
        self,
        on_message: Callable[[str, bytes], None] | None = None,
        on_connect: Callable[[], None] | None = None,
        on_disconnect: Callable[[str], None] | None = None,
        *,
        on_message_details: Callable[[mqtt.MQTTMessage], None] | None = None,
        on_subscribe_success: Callable[[], None] | None = None,
        on_subscribe_failure: Callable[[str], None] | None = None,
        on_loop_start_failure: Callable[[], None] | None = None,
    ) -> None:
        self._client: mqtt.Client | None = None
        self._on_message = on_message
        self._on_connect = on_connect
        self._on_disconnect = on_disconnect
        self._on_message_details = on_message_details
        self._on_subscribe_success = on_subscribe_success
        self._on_subscribe_failure = on_subscribe_failure
        self._on_loop_start_failure = on_loop_start_failure
        self._subscription_lock = threading.RLock()
        self._pending_subscriptions: dict[
            int,
            tuple[int, int, Callable[[], None] | None, Callable[[str], None] | None],
        ] = {}
        self._reconnect_on_failure = False
        self._tls_context: ssl.SSLContext | None = None
        self._broker_host: str | None = None
        self._broker_port: int = 8883
        self._client_id: str | None = None
        self._broker_username: str | None = None
        self._broker_password: str | None = None
        self._connected: bool = False
        self._subscribed_topics: list[str] = []
        self._on_connection_change: Callable[[bool], None] | None = None

    @property
    def is_connected(self) -> bool:
        """Whether the MQTT client is connected to the broker."""
        return self._connected

    @property
    def subscribed_topics(self) -> list[str]:
        """Snapshot of the currently subscribed topics."""
        return list(self._subscribed_topics)

    def set_on_connection_change(self, callback: Callable[[bool], None] | None) -> None:
        """Register a callback for connection state changes (True/False)."""
        self._on_connection_change = callback

    def configure(
        self,
        broker_host: str,
        broker_port: int = 8883,
        client_id: str | None = None,
        broker_username: str | None = None,
        broker_password: str | None = None,
        *,
        reconnect_on_failure: bool = False,
        tls_context: ssl.SSLContext | None = None,
    ) -> None:
        """Set broker connection parameters (call before connect()).

        Broker credentials are the device/register response triple, not
        the account Token. TLS is unconditional — the broker endpoints
        are TLS-only in practice.
        """
        self._broker_host = broker_host
        self._broker_port = broker_port
        self._client_id = client_id or f"{DOMAIN}_{uuid.uuid4().hex[:12]}"
        self._broker_username = broker_username
        self._broker_password = broker_password
        self._reconnect_on_failure = reconnect_on_failure
        self._tls_context = tls_context

    def connect(self) -> None:
        """Connect to the MQTT broker (configure() first)."""
        if not self._broker_host:
            raise ValueError("Broker host not configured. Call configure() first.")

        self._client = mqtt.Client(
            callback_api_version=CallbackAPIVersion.VERSION2,
            client_id=self._client_id,
            protocol=mqtt.MQTTv311,
            clean_session=True,
            reconnect_on_failure=self._reconnect_on_failure,
        )

        if self._broker_username:
            self._client.username_pw_set(
                self._broker_username, self._broker_password or ""
            )

        if self._tls_context is None:
            self._client.tls_set(tls_version=ssl.PROTOCOL_TLS_CLIENT)
            self._client.tls_insecure_set(False)
        else:
            self._client.tls_set_context(self._tls_context)

        # App-confirmed client settings: connectionTimeout=10,
        # keepAliveInterval=15.
        if self._reconnect_on_failure:
            self._client.reconnect_delay_set(min_delay=1, max_delay=30)
        self._client.connect_timeout = 10
        # App-confirmed: MaxInflight=100 (the paho default is 10).
        self._client.max_inflight_messages_set(100)

        self._client.on_connect = self._on_mqtt_connect
        self._client.on_connect_fail = self._on_mqtt_connect_fail
        self._client.on_disconnect = self._on_mqtt_disconnect
        self._client.on_message = self._on_mqtt_message
        self._client.on_subscribe = self._on_mqtt_subscribe

        _LOGGER.debug(
            f"{DOMAIN} - Push connecting to {self._broker_host}:{self._broker_port}"
        )
        self._client.connect_async(self._broker_host, self._broker_port, keepalive=15)
        result = self._client.loop_start()
        if result != mqtt.MQTT_ERR_SUCCESS and self._on_loop_start_failure:
            self._on_loop_start_failure()

    def disconnect(self, *, stop_loop_first: bool = True) -> None:
        """Disconnect from the broker and stop the network loop."""
        if self._client:
            if stop_loop_first:
                self._client.loop_stop()
                self._client.disconnect()
            else:
                try:
                    self._client.disconnect()
                finally:
                    try:
                        self._client.loop_stop()
                    finally:
                        self._client = None
                        self._connected = False
                        self._subscribed_topics = []
                        with self._subscription_lock:
                            self._pending_subscriptions.clear()
            self._client = None
        self._connected = False
        self._subscribed_topics = []
        with self._subscription_lock:
            self._pending_subscriptions.clear()

    def subscribe(
        self,
        topics: list[str],
        *,
        qos: int = 0,
        on_success: Callable[[], None] | None = None,
        on_failure: Callable[[str], None] | None = None,
    ) -> list[str]:
        """Batch-subscribe the given topics (one SUBSCRIBE).

        QoS defaults to the EU provider's required zero. Other providers can
        request a different grant and opt into confirmed SUBACK callbacks.
        Returns the subscribed topic list.
        """
        if not self._client or not self._connected:
            _LOGGER.warning(f"{DOMAIN} - Push not connected, cannot subscribe")
            return []
        if not topics:
            return []
        if type(qos) is not int or qos not in (0, 1, 2):
            raise ValueError("Invalid MQTT subscription QoS")
        success = on_success or self._on_subscribe_success
        failure = on_failure or self._on_subscribe_failure
        with self._subscription_lock:
            try:
                result, mid = self._client.subscribe([(topic, qos) for topic in topics])
            except Exception:
                if failure is None:
                    raise
                failure("subscription failed")
                return []
            if result == mqtt.MQTT_ERR_SUCCESS and mid is not None:
                self._pending_subscriptions[mid] = (len(topics), qos, success, failure)
            elif failure:
                failure("subscription rejected")
        _LOGGER.debug(f"{DOMAIN} - Push batch subscribe mid={mid}, rc={result}")
        self._subscribed_topics = list(topics)
        return list(topics)

    # -- paho-mqtt callbacks (called on the background thread) --

    def _on_mqtt_connect(
        self,
        client: Any,
        userdata: Any,
        flags: Any,
        reason_code: Any,
        properties: Any = None,
    ) -> None:
        """Handle MQTT connection (paho v2 signature)."""
        rc = reason_code.value if not isinstance(reason_code, int) else reason_code
        if rc == 0:
            _LOGGER.debug(f"{DOMAIN} - Push CONNECTED")
            self._connected = True
            if self._on_connect:
                self._on_connect()
            if self._on_connection_change:
                self._on_connection_change(True)
        else:
            # paho re-raises callback exceptions on its network thread, so
            # a raise here kills the loop unseen — notify the glue's
            # retry path instead.
            _LOGGER.warning(f"{DOMAIN} - Push broker refused connection (rc={rc})")
            if self._on_disconnect:
                self._on_disconnect(f"broker refused (rc={rc})")
            if self._on_connection_change:
                self._on_connection_change(False)

    def _on_mqtt_connect_fail(self, client: Any, userdata: Any, **kwargs: Any) -> None:
        """Handle a failed socket-level connect (DNS, reachability) — routed
        through the disconnect callback like any refusal."""
        _LOGGER.warning(
            f"{DOMAIN} - Push TCP connect failed (unreachable or TLS error)"
        )
        if self._on_disconnect:
            self._on_disconnect("connect failed (unreachable)")
        if self._on_connection_change:
            self._on_connection_change(False)

    def _on_mqtt_disconnect(
        self,
        client: Any,
        userdata: Any,
        flags: Any,
        reason_code: Any,
        properties: Any = None,
    ) -> None:
        """Handle MQTT disconnection (paho v2 signature)."""
        self._connected = False
        with self._subscription_lock:
            self._pending_subscriptions.clear()
        if self._on_connection_change:
            self._on_connection_change(False)
        rc = reason_code.value if not isinstance(reason_code, int) else reason_code
        reason = "clean disconnect" if rc == 0 else f"unexpected (rc={rc})"
        _LOGGER.warning(f"{DOMAIN} - Push DISCONNECTED: {reason}")
        if self._on_disconnect:
            self._on_disconnect(reason)

    def _on_mqtt_message(self, client: Any, userdata: Any, msg: Any) -> None:
        """Forward an incoming delivery as raw (topic, payload bytes)."""
        if self._on_message:
            self._on_message(msg.topic, msg.payload)
        if self._on_message_details:
            self._on_message_details(msg)

    def _on_mqtt_subscribe(
        self,
        client: Any,
        userdata: Any,
        mid: Any,
        reason_codes: Any,
        properties: Any = None,
    ) -> None:
        """Log the SUBACK result; a granted 0x80 means the broker
        rejected that topic (diagnostic for rc=128 disconnects)."""
        qos_values = [
            rc if isinstance(rc, int) else rc.value for rc in reason_codes or []
        ]
        with self._subscription_lock:
            pending = self._pending_subscriptions.pop(mid, None)
        if pending is not None:
            count, qos, success, failure = pending
            if len(qos_values) == count and all(
                0 <= value <= qos for value in qos_values
            ):
                if success:
                    success()
            elif failure:
                failure("subscriptions not granted requested QoS")
        if not qos_values:
            return
        rejected = [q for q in qos_values if q >= 0x80]
        if rejected:
            _LOGGER.warning(
                f"{DOMAIN} - Push SUBACK (mid={mid}): broker rejected "
                f"{len(rejected)}/{len(qos_values)} topics, granted_qos={qos_values}"
            )
        else:
            _LOGGER.debug(
                f"{DOMAIN} - Push SUBACK (mid={mid}): {len(qos_values)} topics granted"
            )
