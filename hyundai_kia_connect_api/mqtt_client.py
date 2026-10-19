# pylint:disable=logging-fstring-interpolation
"""mqtt_client.py — schema-neutral MQTT transport for push deliveries.

Uses paho-mqtt 2.x (matches HA 2026.6+). Receive-only: commands are sent
via the HTTP GSPA layer, never through this client. Carries no topic
schema knowledge — topics and message parsing belong to the push
provider (mqtt_service_hub_eu.py for the CCI/EU lineage).

Thread safety: paho-mqtt runs its own background network thread and
dispatches callbacks on it. Callbacks must be thread-safe.
"""

import logging
import uuid
from collections.abc import Callable
from typing import Any

import paho.mqtt.client as mqtt

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class MqttTransport:
    """Receive-only MQTT transport around paho-mqtt 2.x."""

    def __init__(
        self,
        on_message: Callable[[str, bytes], None] | None = None,
        on_connect: Callable[[], None] | None = None,
        on_disconnect: Callable[[str], None] | None = None,
    ) -> None:
        self._client: mqtt.Client | None = None
        self._on_message = on_message
        self._on_connect = on_connect
        self._on_disconnect = on_disconnect
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

    def connect(self) -> None:
        """Connect to the MQTT broker (configure() first)."""
        if not self._broker_host:
            raise ValueError("Broker host not configured. Call configure() first.")

        self._client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,  # type: ignore[attr-defined]
            client_id=self._client_id,
            protocol=mqtt.MQTTv311,
            clean_session=True,
        )

        if self._broker_username:
            self._client.username_pw_set(
                self._broker_username, self._broker_password or ""
            )

        self._client.tls_set()  # Default CA certs
        self._client.tls_insecure_set(False)

        # App-confirmed client settings: connectionTimeout=10,
        # keepAliveInterval=15.
        self._client.reconnect_delay_set(min_delay=1, max_delay=30)
        self._client.connect_timeout = 10

        self._client.on_connect = self._on_mqtt_connect
        self._client.on_connect_fail = self._on_mqtt_connect_fail
        self._client.on_disconnect = self._on_mqtt_disconnect
        self._client.on_message = self._on_mqtt_message
        self._client.on_subscribe = self._on_mqtt_subscribe

        _LOGGER.debug(
            f"{DOMAIN} - Push connecting to {self._broker_host}:{self._broker_port}"
        )
        self._client.connect_async(self._broker_host, self._broker_port, keepalive=15)
        self._client.loop_start()

    def disconnect(self) -> None:
        """Disconnect from the broker and stop the network loop."""
        if self._client:
            self._client.loop_stop()
            self._client.disconnect()
            self._client = None
        self._connected = False
        self._subscribed_topics = []

    def subscribe(self, topics: list[str]) -> list[str]:
        """Batch-subscribe the given topics (QoS 0, one SUBSCRIBE).

        Broker rules (app-confirmed): QoS 0 only — the broker rejects
        QoS 1 SUBSCRIBEs outright; one batch SUBSCRIBE for the whole
        list. Topic selection is the provider's business.
        Returns the subscribed topic list.
        """
        if not self._client or not self._connected:
            _LOGGER.warning(f"{DOMAIN} - Push not connected, cannot subscribe")
            return []
        if not topics:
            return []
        result, mid = self._client.subscribe(list(zip(topics, [0] * len(topics))))
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
        if not reason_codes:
            return
        qos_values = [rc.value for rc in reason_codes]
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
