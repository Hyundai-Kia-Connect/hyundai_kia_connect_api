"""MQTT client for receiving real-time vehicle status and command results.

Uses paho-mqtt v2.x (matching HA 2026.6+ which ships paho-mqtt 2.1.0).
MQTT is receive-only — commands are still sent via HTTP GSPA REST API.

paho-mqtt is a hard dependency (requirements.txt, >=2,<3).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from enum import Enum

import paho.mqtt.client as mqtt

from .mqtt_service_hub import (  # noqa: F401 — transitional re-exports (removed with the VM rewrite)
    MQTT_CONTENT_TYPE_RC,
    MQTT_PROTOCOL_ID_CCU_UPDATE,
    MQTT_PROTOCOL_ID_CONNECTION,
    MQTT_PROTOCOL_ID_DEVICE_RC_CLOSE_CAR,
    MQTT_PROTOCOL_ID_DEVICE_RC_CLOSE_MOBILE,
    MQTT_PROTOCOL_ID_DEVICE_RC_COMMAND,
    MQTT_PROTOCOL_ID_DEVICE_RC_CONNECT,
    MQTT_PROTOCOL_ID_DEVICE_RC_CONNECTCHECK,
    MQTT_PROTOCOL_ID_RC_CLOSE_CAR,
    MQTT_PROTOCOL_ID_RC_CLOSE_MOBILE,
    MQTT_PROTOCOL_ID_RC_COMMAND,
    MQTT_PROTOCOL_ID_RC_CONNECT,
    MQTT_PROTOCOL_ID_RC_CONNECTCHECK,
    MQTT_PROTOCOL_ID_RES,
    POSTFIX_CLOSE_CONNECTIONSTATUS_REQ,
    POSTFIX_CLOSE_CONNECTIONSTATUS_RES,
    POSTFIX_CLOSE_MEDIA_VEHICLESTATUS,
    POSTFIX_CLOSE_PRECONDITION,
    POSTFIX_CLOSE_PRECONDITION_REQ,
    POSTFIX_CLOSE_REMOTE_REQ,
    POSTFIX_CLOSE_REMOTE_RES,
    POSTFIX_CLOSE_VEHICLESTATUS,
    POSTFIX_CLOSE_VEHICLESTATUS_REQ,
    POSTFIX_OTA_PROGRESS,
    POSTFIX_OTA_SCHEDULEUPDATE,
    POSTFIX_REMOTE_COMMAND,
    POSTFIX_REMOTE_CONNECT,
    POSTFIX_REMOTE_CONNECTCHECK,
    POSTFIX_REMOTE_MOBILECLOSE,
    POSTFIX_REMOTE_VEHICLECLOSE,
    SERVICE_HUB_PRODUCTION_BASES,
    SERVICE_HUB_STAGING_BASES,
    TOPIC_PREFIX_CONNECTION,
    TOPIC_PREFIX_DEVICE,
    TOPIC_PREFIX_DEVICE_RES,
    TOPIC_PREFIX_LOCATION,
    TOPIC_PREFIX_RES,
    TOPIC_PREFIX_VEHICLE,
    TOPIC_PREFIX_VSS,
    MqttCacheCapabilities,
    MqttHVACCommand,
    MqttHVACHeader,
    MqttMessage,
    MqttRCCommandType,
    MqttRCHeader,
    _classify_topic,
    _extract_infix,
    build_car_close_remote_topics,
    build_car_remote_topics,
    build_car_status_topics,
    build_device_close_remote_topics,
    build_device_remote_topics,
    build_ota_topics,
    build_topic,
    parse_mqtt_message,
)

_LOGGER = logging.getLogger(__name__)

DOMAIN = "hyundai_kia_connect_api"


# MQTT connection state values
class MqttConnectionState(str, Enum):
    ONLINE = "ONLINE"
    OFFLINE = "OFFLINE"
    UNKNOWN = "UNKNOWN"



# ---------------------------------------------------------------------------
# MQTT Client
# ---------------------------------------------------------------------------


class HyundaiMqttClient:
    """MQTT client for receiving real-time vehicle status and command results.

    Supports paho-mqtt v2.x (HA 2026.6+) and v1.x for backwards compatibility.
    MQTT is receive-only for status/results. Commands are sent via HTTP GSPA.

    Thread safety: paho-mqtt manages its own background thread for network I/O.
    Callbacks are dispatched on that thread. Callers must dispatch to their
    own event loop if needed (e.g., asyncio.run_coroutine_threadsafe).

    Requires paho-mqtt>=2,<3 (hard dependency).
    """

    def __init__(
        self,
        on_message: Callable[[MqttMessage], None] | None = None,
        on_connect: Callable[[], None] | None = None,
        on_disconnect: Callable[[str], None] | None = None,
    ) -> None:
        self._client: mqtt.Client | None = None
        self._on_message = on_message
        self._on_connect = on_connect
        self._on_disconnect = on_disconnect
        self._broker_host: str | None = None
        self._broker_port: int = 8883
        self._use_ssl: bool = True
        self._client_id: str | None = None
        self._username: str | None = None
        self._password: str | None = None
        self._connected: bool = False
        self._subscribed_topics: list[str] = []
        self._pending_subs: dict[int, str] = {}  # mid → topic for SUBACK tracking
        self._on_connection_change: Callable[[bool], None] | None = None

    @property
    def is_connected(self) -> bool:
        """Whether the MQTT client is connected to the broker."""
        return self._connected

    @property
    def subscribed_topics(self) -> list[str]:
        """List of currently subscribed topics."""
        return list(self._subscribed_topics)

    def set_on_connection_change(self, callback: Callable[[bool], None] | None) -> None:
        """Register a callback for MQTT connection state changes."""
        self._on_connection_change = callback

    def configure(
        self,
        broker_host: str,
        broker_port: int = 8883,
        use_ssl: bool = True,
        client_id: str | None = None,
        username: str | None = None,
        password: str | None = None,
    ) -> None:
        """Configure MQTT broker connection parameters.

        Call this after getting broker details from the Service Hub.
        """
        self._broker_host = broker_host
        self._broker_port = broker_port
        self._use_ssl = use_ssl
        self._client_id = client_id or f"hyundai_kia_{uuid.uuid4().hex[:12]}"
        self._username = username
        self._password = password

    def connect(self) -> None:
        """Connect to the MQTT broker.

        Must call configure() first with broker details from Service Hub.
        """
        if not self._broker_host:
            raise ValueError("Broker host not configured. Call configure() first.")

        self._client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=self._client_id,
            protocol=mqtt.MQTTv311,
            clean_session=True,
        )

        if self._username:
            self._client.username_pw_set(self._username, self._password or "")

        if self._use_ssl:
            self._client.tls_set()  # Default CA certs
            self._client.tls_insecure_set(False)

        # Match the app's MQTT client: connectionTimeout=10, keepAliveInterval=15
        self._client.reconnect_delay_set(min_delay=1, max_delay=30)
        self._client.connect_timeout = 10

        self._client.on_connect = self._on_mqtt_connect
        self._client.on_disconnect = self._on_mqtt_disconnect
        self._client.on_message = self._on_mqtt_message
        self._client.on_subscribe = self._on_mqtt_subscribe
        self._client.on_log = self._on_mqtt_log

        _LOGGER.info(
            f"{DOMAIN} - MQTT connecting to {self._broker_host}:{self._broker_port} "
            f"(ssl={self._use_ssl}, client_id={self._client_id}, "
            f"auth={'user=' + self._username[:4] + '...' if self._username else 'none'})"
        )
        self._client.connect_async(self._broker_host, self._broker_port, keepalive=15)
        self._client.loop_start()

    def disconnect(self) -> None:
        """Disconnect from the MQTT broker and stop the network loop."""
        if self._client:
            self._client.loop_stop()
            self._client.disconnect()
            self._client = None
        self._connected = False
        self._subscribed_topics = []
        self._pending_subs.clear()

    def subscribe_topics(
        self,
        vehicle_id: str,
        client_id: str,
        hu_client_id: str | None = None,
        capabilities: MqttCacheCapabilities | None = None,
    ) -> list[str]:
        """Subscribe to MQTT topic groups based on vehicle capabilities.

        Returns the list of subscribed topic strings.
        """
        if not self._client or not self._connected:
            _LOGGER.warning(f"{DOMAIN} - MQTT not connected, cannot subscribe")
            return []

        caps = capabilities or MqttCacheCapabilities()
        all_topics: list[str] = []

        # 1. Always subscribe: CarStatus (VSS, Connect — Res excluded, broker rejects)
        all_topics.extend(build_car_status_topics(vehicle_id, caps))

        # 2. Always subscribe: DeviceRemote
        all_topics.extend(build_device_remote_topics(client_id))

        # 3. CarRemote and CarCloseRemote EXCLUDED — broker rejects vehicle/{huClientId}/...
        # topics with rc=128. Phone clients can only subscribe to device/{clientId}/...
        # topics for remote control. CarRemote topics (vehicle/{huClientId}/...) are only
        # authorized for the head unit itself, not for phone clients.

        # 4. DeviceCloseRemote EXCLUDED — broker rejects with rc=128.
        # The app does NOT register any "closeremote" protocols in device/protocol,
        # so the broker does not authorize subscriptions to
        # device/{clientId}/closeremote/* topics.
        # all_topics.extend(build_device_close_remote_topics(client_id, caps))

        # 5. Conditional: OTA
        all_topics.extend(build_ota_topics(vehicle_id, caps))

        # Subscribe with QoS 0 (at most once)
        # The Hyundai CCI broker does NOT authorize QoS 1 subscriptions — it
        # disconnects with rc=128 immediately after receiving a QoS 1 SUBSCRIBE.
        # QoS 0 subscriptions are accepted with SUBACK and remain stable.
        # The app requests QoS 1 but the broker downgrades to 0
        # for native Android clients. For paho-mqtt clients the broker rejects QoS 1
        # outright, so we must request QoS 0.
        # The app sends all topics in a single batch SUBSCRIBE (like paho-mqtt subscribe()
        # with topic list), not one-by-one. This matches the broker's expectations.
        if all_topics:
            qos_list = [0] * len(all_topics)
            _LOGGER.info(
                f"{DOMAIN} - MQTT batch-subscribing to {len(all_topics)} topics (QoS 0)"
            )
            result, mid = self._client.subscribe(list(zip(all_topics, qos_list)))
            # Store mid→"batch" for SUBACK handler
            self._pending_subs[mid] = ",".join(all_topics)
            _LOGGER.debug(f"{DOMAIN} - MQTT batch subscribe mid={mid}, rc={result}")

        self._subscribed_topics = all_topics
        return all_topics

    # -- paho-mqtt callbacks (called on background thread) --

    def _on_mqtt_connect(
        self, client, userdata, flags, reason_code, properties=None
    ) -> None:
        """Handle MQTT connection callback (paho v2 signature)."""
        rc = reason_code.value if not isinstance(reason_code, int) else reason_code
        if rc == 0:
            _LOGGER.info(f"{DOMAIN} - MQTT CONNECTED OK")
            self._connected = True
            if self._on_connect:
                self._on_connect()
            if self._on_connection_change:
                self._on_connection_change(True)
        else:
            _LOGGER.warning(f"{DOMAIN} - MQTT connect FAILED: rc={rc}")

    def _on_mqtt_disconnect(
        self, client, userdata, flags, reason_code, properties=None
    ) -> None:
        """Handle MQTT disconnection callback (paho v2 signature)."""
        self._connected = False
        if self._on_connection_change:
            self._on_connection_change(False)
        rc = reason_code.value if not isinstance(reason_code, int) else reason_code
        reason = "clean disconnect" if rc == 0 else f"unexpected (rc={rc})"
        _LOGGER.warning(f"{DOMAIN} - MQTT DISCONNECTED: {reason}")
        if self._on_disconnect:
            self._on_disconnect(reason)

    def _on_mqtt_message(self, client, userdata, msg) -> None:
        """Handle incoming MQTT message."""
        topic = msg.topic
        payload = msg.payload

        try:
            parsed = parse_mqtt_message(topic, payload)
            _LOGGER.debug(
                f"{DOMAIN} - MQTT message: {parsed.topic_group}/"
                f"{parsed.topic_type} for {parsed.vehicle_id}"
            )
            # Log first 3 messages at WARNING for operational visibility
            if not hasattr(self, "_msg_count"):
                self._msg_count = 0
            self._msg_count += 1
            if self._msg_count <= 3:
                _LOGGER.warning(
                    f"{DOMAIN} - MQTT msg #{self._msg_count}: "
                    f"{parsed.topic_group}/{parsed.topic_type}"
                )
            if self._on_message:
                self._on_message(parsed)
        except Exception as e:
            _LOGGER.error(f"{DOMAIN} - MQTT message parse error: {e}")

    def _on_mqtt_subscribe(
        self, client, userdata, mid, reason_codes, properties=None
    ) -> None:
        """Handle subscription confirmation (paho v2 signature).

        reason_codes is a list of ReasonCode objects. In MQTT 3.1.1, SUBACK granted QoS 0x80 (128) means subscription
        failure — the broker rejected that topic. This is critical for
        diagnosing rc=128 disconnects.
        """
        raw = self._pending_subs.pop(mid, f"unknown(mid={mid})")
        # Batch subscribe: raw is comma-joined topic list
        topics = raw.split(",") if "," in raw else [raw]

        # Normalize reason_codes to a list of ints (paho v2: ReasonCode objects)
        if reason_codes is None:
            qos_values = []
        else:
            qos_values = [rc.value for rc in reason_codes]

        # Log per-topic result when topic count matches QoS count
        if len(topics) == len(qos_values):
            for i, (t, q) in enumerate(zip(topics, qos_values)):
                if q >= 0x80:
                    _LOGGER.warning(
                        f"{DOMAIN} - MQTT SUBACK REJECTED '{t}': "
                        f"granted_qos=0x{q:02x} — broker rejected subscription"
                    )
                else:
                    _LOGGER.debug(f"{DOMAIN} - MQTT SUBACK OK '{t}': granted_qos={q}")
        else:
            # Fallback: log raw batch
            failed = [q for q in qos_values if q >= 0x80]
            if failed:
                _LOGGER.warning(
                    f"{DOMAIN} - MQTT SUBACK FAILED (mid={mid}): "
                    f"granted_qos={qos_values} — broker rejected {len(failed)}/{len(qos_values)} topics"
                )
            else:
                _LOGGER.info(
                    f"{DOMAIN} - MQTT SUBACK OK (mid={mid}): "
                    f"all {len(qos_values)} topics granted, qos={qos_values}"
                )

    def _on_mqtt_log(self, client, userdata, level, buf) -> None:
        _LOGGER.debug(f"{DOMAIN} - MQTT: {buf}")
