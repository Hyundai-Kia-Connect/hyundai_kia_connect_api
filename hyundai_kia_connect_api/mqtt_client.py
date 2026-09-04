"""MQTT client for receiving real-time vehicle status and command results.

Uses paho-mqtt v2.x (matching HA 2026.6+ which ships paho-mqtt 2.1.0).
MQTT is receive-only — commands are still sent via HTTP GSPA REST API.

For backwards compatibility with paho-mqtt v1.x, a compatibility layer
detects the version and adapts callback signatures accordingly.

Install with: pip install hyundai_kia_connect_api[mqtt]
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

_LOGGER = logging.getLogger(__name__)

try:
    import paho.mqtt.client as mqtt

    _PAHO_AVAILABLE = True
except ImportError:
    _PAHO_AVAILABLE = False

# Detect paho-mqtt v2 vs v1 for callback compatibility
_PAHO_V2 = _PAHO_AVAILABLE and hasattr(mqtt, "CallbackAPIVersion")

DOMAIN = "hyundai_kia_connect_api"

# ---------------------------------------------------------------------------
# MQTT topic constants (resolved from the official EU app, v1.1.4)
# ---------------------------------------------------------------------------

# Topic prefixes (prefix + infix + postfix = full topic)
TOPIC_PREFIX_VEHICLE = "vehicle/"  # CarRemote, CarCloseRemote, CarOta
TOPIC_PREFIX_DEVICE = "device/"  # DeviceRemote, DeviceCloseRemote
TOPIC_PREFIX_VSS = "service/phone/_/vss/"  # CarStatus.Status
TOPIC_PREFIX_CONNECTION = "service/phone/_/connection/"  # CarStatus.Connect
TOPIC_PREFIX_RES = "service/phone/_/res/"  # CarStatus.Res
TOPIC_PREFIX_LOCATION = "service/phone/_/location/"  # CarStatus.Location
TOPIC_PREFIX_DEVICE_RES = "$/device/res/"  # DeviceRemote.Res (separate prefix)

# Topic postfixes
POSTFIX_REMOTE_COMMAND = "/remotecontroller/command"
POSTFIX_REMOTE_CONNECT = "/remotecontroller/connect"
POSTFIX_REMOTE_CONNECTCHECK = "/remotecontroller/connectcheck"
POSTFIX_REMOTE_MOBILECLOSE = "/remotecontroller/mobileclose"
POSTFIX_REMOTE_VEHICLECLOSE = "/remotecontroller/vehicleclose"
POSTFIX_CLOSE_CONNECTIONSTATUS_REQ = "/closeremote/connectionstatus/req"
POSTFIX_CLOSE_PRECONDITION_REQ = "/closeremote/precondition/req"
POSTFIX_CLOSE_REMOTE_REQ = "/closeremote/remote/req"
POSTFIX_CLOSE_VEHICLESTATUS_REQ = "/closeremote/vehiclestatus/req"
POSTFIX_CLOSE_CONNECTIONSTATUS_RES = "/closeremote/connectionstatus/res"
POSTFIX_CLOSE_MEDIA_VEHICLESTATUS = "/closeremote/media/vehiclestatus"
POSTFIX_CLOSE_PRECONDITION = "/closeremote/precondition"
POSTFIX_CLOSE_REMOTE_RES = "/closeremote/remote/res"
POSTFIX_CLOSE_VEHICLESTATUS = "/closeremote/vehiclestatus"
POSTFIX_OTA_PROGRESS = "/_/ota/otaprogress"
POSTFIX_OTA_SCHEDULEUPDATE = "/_/ota/scheduleupdate"


# MQTT connection state values
class MqttConnectionState(str, Enum):
    ONLINE = "ONLINE"
    OFFLINE = "OFFLINE"
    UNKNOWN = "UNKNOWN"


# MQTT RC command types (from MQTTRCCommand enum)
class MqttRCCommandType(int, Enum):
    CONNECT = -1
    CONNECT_CHECK = -2
    CONNECTING_CANCEL = -3
    DISCONNECT = 0
    MODE_CHANGE = 1
    VOLUME_UP = 2
    VOLUME_DOWN = 3
    MUTE = 4
    SEEK_UP = 5
    SEEK_DOWN = 6
    AV_ON_OFF = 7


# MQTT HVAC command values (from MQTTHVACCommand)
class MqttHVACCommand(str, Enum):
    PLAY = "PLAY"
    STOP = "STOP"
    PAUSE = "PAUSE"
    PREV = "PREV"
    NEXT = "NEXT"
    VOL_UP = "VOL_UP"
    VOL_DOWN = "VOL_DOWN"
    MUTE = "MUTE"
    UNMUTE = "UNMUTE"


# Service Hub URL patterns (from the official EU app, v1.1.4)
# Production URLs are used for real connections; staging for testing.
# Both use port 31010 (HTTPS for REST, same host serves MQTT on port 443/8883).
SERVICE_HUB_PRODUCTION_BASES = {
    "H_EU": "egw-svchub-ccs-h-eu.eu-central.hmgmobility.com:31010",
    "H_KR": "egw-svchub-ccs-h-kr.ap-northeast.hmgmobility.com:31010",
    "H_CA": "egw-svchub-ccs-h-ca.us-west.hmgmobility.com:31010",
    "H_JP": "egw-svchub-ccs-h-jp.ap-northeast.hmgmobility.com:31010",
    "H_US": "egw-svchub-ccs-h-us.us-west.hmgmobility.com:31010",
    "H_SA": "egw-svchub-ccs-h-sa.sa-east.hmgmobility.com:31010",
    "K_EU": "egw-svchub-ccs-k-eu.eu-central.hmgmobility.com:31010",
    "K_KR": "egw-svchub-ccs-k-kr.ap-northeast.hmgmobility.com:31010",
    "K_CA": "egw-svchub-ccs-k-ca.us-west.hmgmobility.com:31010",
    "K_JP": "egw-svchub-ccs-k-jp.ap-northeast.hmgmobility.com:31010",
    "K_US": "egw-svchub-ccs-k-us.us-west.hmgmobility.com:31010",
    "K_SA": "egw-svchub-ccs-k-sa.sa-east.hmgmobility.com:31010",
    "G_EU": "egw-svchub-ccs-g-eu.eu-central.hmgmobility.com:31010",
    "G_KR": "egw-svchub-ccs-g-kr.ap-northeast.hmgmobility.com:31010",
    "G_CA": "egw-svchub-ccs-g-ca.us-west.hmgmobility.com:31010",
    "G_US": "egw-svchub-ccs-g-us.us-west.hmgmobility.com:31010",
    "G_SA": "egw-svchub-ccs-g-sa.sa-central.hmgmobility.com:31010",
}

SERVICE_HUB_STAGING_BASES = {
    "H_EU": "stg-egw-svchub-ccs-H-eu.eu-central.hmgmobility.com:31010",
    "H_KR": "stg-egw-svchub-ccs-H-kr.ap-northeast.hmgmobility.com:31010",
    "H_CA": "stg-egw-svchub-ccs-H-ca.us-west.hmgmobility.com:31010",
    "H_JP": "stg-egw-svchub-ccs-H-jp.ap-northeast.hmgmobility.com:31010",
    "H_US": "stg-egw-svchub-ccs-H-us.us-west.hmgmobility.com:31010",
    "H_SA": "stg-egw-svchub-ccs-H-sa.sa-east.hmgmobility.com:31010",
    "K_EU": "stg-egw-svchub-ccs-K-eu.eu-central.hmgmobility.com:31010",
    "K_KR": "stg-egw-svchub-ccs-K-kr.ap-northeast.hmgmobility.com:31010",
    "K_CA": "stg-egw-svchub-ccs-K-ca.us-west.hmgmobility.com:31010",
    "K_JP": "stg-egw-svchub-ccs-K-jp.ap-northeast.hmgmobility.com:31010",
    "K_SA": "stg-egw-svchub-ccs-K-sa.sa-east.hmgmobility.com:31010",
    "G_EU": "stg-egw-svchub-ccs-G-eu.eu-central.hmgmobility.com:31010",
    "G_KR": "stg-egw-svchub-ccs-G-kr.ap-northeast.hmgmobility.com:31010",
    "G_CA": "stg-egw-svchub-ccs-G-ca.us-west.hmgmobility.com:31010",
}

# Protocol IDs — sent as plain strings in Service Hub and MQTT messages
# Base protocols (always included): service.phone.vss, service.phone.connection,
# service.phone.res; RC connect: vehicle.remotecontroller.connect
#   m39749(1540415511) mode=7 → vehicle.remotecontroller.command
#   m39740(1808586424) mode=0 → vehicle.remotecontroller.connectcheck
#   m39739(1250028738) mode=2 → vehicle.remotecontroller.mobileclose
#   m39739(1250028938) mode=2 → vehicle.remotecontroller.vehicleclose (closefromcar was wrong)
#   m39750(1676614053) mode=5 → application/json (content type, NOT a protocol)
#   m1335(1674116117) mode=4  → statesync.vehicle.ccu.update (protocolId field)
MQTT_PROTOCOL_ID_VSS = "service.phone.vss"
MQTT_PROTOCOL_ID_CONNECTION = "service.phone.connection"
MQTT_PROTOCOL_ID_RES = "service.phone.res"
# Vehicle RC protocols (CarRemote topics use vehicle/ prefix)
MQTT_PROTOCOL_ID_RC_CONNECT = "vehicle.remotecontroller.connect"
MQTT_PROTOCOL_ID_RC_COMMAND = "vehicle.remotecontroller.command"
MQTT_PROTOCOL_ID_RC_CONNECTCHECK = "vehicle.remotecontroller.connectcheck"
MQTT_PROTOCOL_ID_RC_CLOSE_MOBILE = "vehicle.remotecontroller.mobileclose"
MQTT_PROTOCOL_ID_RC_CLOSE_CAR = "vehicle.remotecontroller.vehicleclose"
# Device RC protocols (DeviceRemote topics use device/ prefix)
# The app's protocol registration includes BOTH device.* AND vehicle.* variants
MQTT_PROTOCOL_ID_DEVICE_RC_CONNECT = "device.remotecontroller.connect"
MQTT_PROTOCOL_ID_DEVICE_RC_COMMAND = "device.remotecontroller.command"
MQTT_PROTOCOL_ID_DEVICE_RC_CONNECTCHECK = "device.remotecontroller.connectcheck"
MQTT_PROTOCOL_ID_DEVICE_RC_CLOSE_MOBILE = "device.remotecontroller.mobileclose"
MQTT_PROTOCOL_ID_DEVICE_RC_CLOSE_CAR = "device.remotecontroller.vehicleclose"
MQTT_CONTENT_TYPE_RC = "application/json"
MQTT_PROTOCOL_ID_CCU_UPDATE = "statesync.vehicle.ccu.update"


# ---------------------------------------------------------------------------
# Message data classes
# ---------------------------------------------------------------------------


@dataclass
class MqttMessage:
    """Parsed MQTT message delivered to the callback."""

    topic_group: str  # "CarStatus", "DeviceRemote", "CarRemote", etc.
    topic_type: str  # "Status", "Res", "Connect", "Command", etc.
    vehicle_id: str  # Vehicle ID from topic infix
    payload: dict  # Parsed JSON (header + body)


@dataclass
class MqttRCHeader:
    """RC message header."""

    authorization: str | None = None
    content_type: str | None = None
    client_id: str | None = None
    device_id: str | None = None
    protocol_id: str | None = None
    tid: str | None = None


@dataclass
class MqttHVACHeader:
    """HVAC message header."""

    tid: str | None = None
    client_id: str | None = None
    protocol_id: str | None = None
    scenario: int | None = None
    air_conditioning: int | None = None
    media: int | None = None


@dataclass
class MqttCacheCapabilities:
    """Capabilities from MQTTCacheResponse (topic subscription logic)."""

    has_hvac_close_remote: bool = False
    has_media_close_remote: bool = False
    is_support_speed_event: bool = False
    is_support_ota_progress: bool = False
    is_support_schedule_update: bool = False
    ccu_client_id: str | None = None
    hu_client_id: str | None = None
    vehicle_id: str | None = None
    client_id: str | None = None


# ---------------------------------------------------------------------------
# Topic builder
# ---------------------------------------------------------------------------


def build_topic(prefix: str, infix: str, postfix: str = "") -> str:
    """Build an MQTT topic string from prefix + infix + postfix."""
    return prefix + infix + postfix


def build_car_status_topics(
    vehicle_id: str, capabilities: MqttCacheCapabilities
) -> list[str]:
    """Build CarStatus topic list for a vehicle.

    NOTE: The `service/phone/_/res/{vehicle_id}` topic (TOPIC_PREFIX_RES) is
    NOT included because the Hyundai CCI broker rejects subscriptions to it
    with rc=128 disconnect. Only VSS and Connection topics are authorized for
    phone clients. The `res` topic may only be available to head-unit clients.
    """
    topics = [
        build_topic(TOPIC_PREFIX_VSS, vehicle_id),
        build_topic(TOPIC_PREFIX_CONNECTION, vehicle_id),
        # res topic excluded — broker rejects with rc=128
        # build_topic(TOPIC_PREFIX_RES, vehicle_id),
    ]
    if capabilities.is_support_speed_event:
        topics.append(build_topic(TOPIC_PREFIX_LOCATION, vehicle_id))
    return topics


def build_device_remote_topics(client_id: str) -> list[str]:
    """Build DeviceRemote topic list for a device."""
    return [
        build_topic(TOPIC_PREFIX_DEVICE, client_id, POSTFIX_REMOTE_COMMAND),
        build_topic(TOPIC_PREFIX_DEVICE, client_id, POSTFIX_REMOTE_CONNECT),
        build_topic(TOPIC_PREFIX_DEVICE, client_id, POSTFIX_REMOTE_CONNECTCHECK),
        build_topic(TOPIC_PREFIX_DEVICE, client_id, POSTFIX_REMOTE_MOBILECLOSE),
        build_topic(TOPIC_PREFIX_DEVICE, client_id, POSTFIX_REMOTE_VEHICLECLOSE),
        build_topic(TOPIC_PREFIX_DEVICE_RES, client_id),
    ]


def build_car_remote_topics(hu_client_id: str) -> list[str]:
    """Build CarRemote topic list (infix = huClientId)."""
    return [
        build_topic(TOPIC_PREFIX_VEHICLE, hu_client_id, POSTFIX_REMOTE_COMMAND),
        build_topic(TOPIC_PREFIX_VEHICLE, hu_client_id, POSTFIX_REMOTE_CONNECT),
        build_topic(TOPIC_PREFIX_VEHICLE, hu_client_id, POSTFIX_REMOTE_CONNECTCHECK),
        build_topic(TOPIC_PREFIX_VEHICLE, hu_client_id, POSTFIX_REMOTE_MOBILECLOSE),
        build_topic(TOPIC_PREFIX_VEHICLE, hu_client_id, POSTFIX_REMOTE_VEHICLECLOSE),
    ]


def build_car_close_remote_topics(hu_client_id: str) -> list[str]:
    """Build CarCloseRemote topic list."""
    return [
        build_topic(
            TOPIC_PREFIX_VEHICLE, hu_client_id, POSTFIX_CLOSE_CONNECTIONSTATUS_REQ
        ),
        build_topic(TOPIC_PREFIX_VEHICLE, hu_client_id, POSTFIX_CLOSE_PRECONDITION_REQ),
        build_topic(TOPIC_PREFIX_VEHICLE, hu_client_id, POSTFIX_CLOSE_REMOTE_REQ),
        build_topic(
            TOPIC_PREFIX_VEHICLE, hu_client_id, POSTFIX_CLOSE_VEHICLESTATUS_REQ
        ),
    ]


def build_device_close_remote_topics(
    client_id: str, capabilities: MqttCacheCapabilities
) -> list[str]:
    """Build DeviceCloseRemote topic list (conditional on capabilities)."""
    topics = [
        build_topic(TOPIC_PREFIX_DEVICE, client_id, POSTFIX_CLOSE_REMOTE_RES),
    ]
    if capabilities.has_hvac_close_remote:
        topics.append(
            build_topic(TOPIC_PREFIX_DEVICE, client_id, POSTFIX_CLOSE_VEHICLESTATUS)
        )
    if capabilities.has_media_close_remote:
        topics.append(
            build_topic(
                TOPIC_PREFIX_DEVICE, client_id, POSTFIX_CLOSE_MEDIA_VEHICLESTATUS
            )
        )
    topics.append(
        build_topic(TOPIC_PREFIX_DEVICE, client_id, POSTFIX_CLOSE_CONNECTIONSTATUS_RES)
    )
    topics.append(
        build_topic(TOPIC_PREFIX_DEVICE, client_id, POSTFIX_CLOSE_PRECONDITION)
    )
    return topics


def build_ota_topics(vehicle_id: str, capabilities: MqttCacheCapabilities) -> list[str]:
    """Build OTA topic list (conditional on capabilities)."""
    topics = []
    if capabilities.is_support_ota_progress:
        topics.append(
            build_topic(TOPIC_PREFIX_VEHICLE, vehicle_id, POSTFIX_OTA_PROGRESS)
        )
    if capabilities.is_support_schedule_update:
        topics.append(
            build_topic(TOPIC_PREFIX_VEHICLE, vehicle_id, POSTFIX_OTA_SCHEDULEUPDATE)
        )
    return topics


# ---------------------------------------------------------------------------
# Message parser
# ---------------------------------------------------------------------------


def parse_mqtt_message(topic: str, payload: bytes) -> MqttMessage:
    """Parse an MQTT message into a structured MqttMessage.

    Determines topic_group and topic_type from the topic string,
    parses the JSON payload, and extracts vehicle_id from the topic.
    """
    # Determine topic group and type from the topic string
    topic_group, topic_type, vehicle_id = _classify_topic(topic)

    try:
        data = json.loads(payload.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        data = {"raw": payload.hex()}

    return MqttMessage(
        topic_group=topic_group,
        topic_type=topic_type,
        vehicle_id=vehicle_id,
        payload=data,
    )


def _classify_topic(topic: str) -> tuple[str, str, str]:
    """Classify a topic string into (group, type, vehicle_id)."""
    # CarStatus topics (no /remotecontroller/ or /closeremote/ prefix)
    if topic.startswith(TOPIC_PREFIX_VSS):
        return ("CarStatus", "Status", _extract_infix(topic, TOPIC_PREFIX_VSS))
    if topic.startswith(TOPIC_PREFIX_CONNECTION):
        return ("CarStatus", "Connect", _extract_infix(topic, TOPIC_PREFIX_CONNECTION))
    if topic.startswith(TOPIC_PREFIX_RES):
        return ("CarStatus", "Res", _extract_infix(topic, TOPIC_PREFIX_RES))
    if topic.startswith(TOPIC_PREFIX_LOCATION):
        return ("CarStatus", "Location", _extract_infix(topic, TOPIC_PREFIX_LOCATION))

    # DeviceRemote.Res (special prefix)
    if topic.startswith(TOPIC_PREFIX_DEVICE_RES):
        infix = _extract_infix(topic, TOPIC_PREFIX_DEVICE_RES)
        return ("DeviceRemote", "Res", infix)

    # Vehicle-prefixed topics (CarRemote, CarCloseRemote, CarOta)
    if topic.startswith(TOPIC_PREFIX_VEHICLE):
        infix = _extract_infix(topic, TOPIC_PREFIX_VEHICLE)
        suffix = topic[len(TOPIC_PREFIX_VEHICLE) + len(infix) :]

        if POSTFIX_REMOTE_COMMAND in suffix:
            return ("CarRemote", "Command", infix)
        if POSTFIX_REMOTE_CONNECT in suffix:
            return ("CarRemote", "Connect", infix)
        if POSTFIX_REMOTE_CONNECTCHECK in suffix:
            return ("CarRemote", "ConnectCheck", infix)
        if POSTFIX_REMOTE_MOBILECLOSE in suffix:
            return ("CarRemote", "MobileClose", infix)
        if POSTFIX_REMOTE_VEHICLECLOSE in suffix:
            return ("CarRemote", "CarClose", infix)
        if POSTFIX_CLOSE_CONNECTIONSTATUS_REQ in suffix:
            return ("CarCloseRemote", "ConnectionStatus", infix)
        if POSTFIX_CLOSE_PRECONDITION_REQ in suffix:
            return ("CarCloseRemote", "PreCondition", infix)
        if POSTFIX_CLOSE_REMOTE_REQ in suffix:
            return ("CarCloseRemote", "Remote", infix)
        if POSTFIX_CLOSE_VEHICLESTATUS_REQ in suffix:
            return ("CarCloseRemote", "VehicleStatus", infix)
        if POSTFIX_OTA_PROGRESS in suffix:
            return ("CarOta", "Progress", infix)
        if POSTFIX_OTA_SCHEDULEUPDATE in suffix:
            return ("CarOta", "ScheduleUpdate", infix)
        return ("CarRemote", "Unknown", infix)

    # Device-prefixed topics (DeviceRemote, DeviceCloseRemote)
    if topic.startswith(TOPIC_PREFIX_DEVICE):
        infix = _extract_infix(topic, TOPIC_PREFIX_DEVICE)
        suffix = topic[len(TOPIC_PREFIX_DEVICE) + len(infix) :]

        if POSTFIX_REMOTE_COMMAND in suffix:
            return ("DeviceRemote", "Command", infix)
        if POSTFIX_REMOTE_CONNECT in suffix:
            return ("DeviceRemote", "Connect", infix)
        if POSTFIX_REMOTE_CONNECTCHECK in suffix:
            return ("DeviceRemote", "ConnectCheck", infix)
        if POSTFIX_REMOTE_MOBILECLOSE in suffix:
            return ("DeviceRemote", "MobileClose", infix)
        if POSTFIX_REMOTE_VEHICLECLOSE in suffix:
            return ("DeviceRemote", "CarClose", infix)
        if POSTFIX_CLOSE_CONNECTIONSTATUS_RES in suffix:
            return ("DeviceCloseRemote", "ConnectionStatus", infix)
        if POSTFIX_CLOSE_MEDIA_VEHICLESTATUS in suffix:
            return ("DeviceCloseRemote", "MediaVehicleStatus", infix)
        if POSTFIX_CLOSE_PRECONDITION in suffix:
            return ("DeviceCloseRemote", "PreCondition", infix)
        if POSTFIX_CLOSE_REMOTE_RES in suffix:
            return ("DeviceCloseRemote", "Remote", infix)
        if POSTFIX_CLOSE_VEHICLESTATUS in suffix:
            return ("DeviceCloseRemote", "VehicleStatus", infix)
        return ("DeviceRemote", "Unknown", infix)

    return ("Unknown", "Unknown", "")


def _extract_infix(topic: str, prefix: str) -> str:
    """Extract the vehicle/client ID infix from a topic after the prefix.

    The infix is the segment between the prefix and the next '/' or end of string.
    """
    after_prefix = topic[len(prefix) :]
    slash_pos = after_prefix.find("/")
    if slash_pos > 0:
        return after_prefix[:slash_pos]
    # Trailing slash topics (e.g., service/phone/_/vss/{vehicleId}/)
    if after_prefix.endswith("/"):
        return after_prefix[:-1]
    return after_prefix


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

    Requires paho-mqtt>=2.1.0 (v1.x supported as fallback). Install with:
        pip install hyundai_kia_connect_api[mqtt]
    """

    def __init__(
        self,
        on_message: Callable[[MqttMessage], None] | None = None,
        on_connect: Callable[[], None] | None = None,
        on_disconnect: Callable[[str], None] | None = None,
    ) -> None:
        if not _PAHO_AVAILABLE:
            raise ImportError(
                "paho-mqtt is required for MQTT support. "
                "Install with: pip install hyundai_kia_connect_api[mqtt]"
            )

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

        if _PAHO_V2:
            self._client = mqtt.Client(
                callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                client_id=self._client_id,
                protocol=mqtt.MQTTv311,
                clean_session=True,
            )
        else:
            self._client = mqtt.Client(
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
        if hasattr(self._client, "connect_timeout"):
            # paho-mqtt v2: property, default 5.0
            self._client.connect_timeout = 10
        elif hasattr(self._client, "_connect_timeout"):
            # paho-mqtt v1: internal attribute, default 5.0
            self._client._connect_timeout = 10

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
        """Handle MQTT connection callback (v2 signature with v1 compat)."""
        # v2: reason_code is a ReasonCode object, supports == comparison with int
        # v1: rc is a plain int (passed as reason_code when properties=None)
        rc = reason_code if isinstance(reason_code, int) else reason_code.value
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
        self, client, userdata, flags=None, reason_code=None, properties=None
    ) -> None:
        """Handle MQTT disconnection callback (v2 signature with v1 compat).

        v1: (client, userdata, rc)
        v2: (client, userdata, flags, reason_code, properties)
        """
        self._connected = False
        if self._on_connection_change:
            self._on_connection_change(False)
        # v1 passes rc as 3rd arg; v2 passes flags as 3rd, reason_code as 4th
        if isinstance(flags, int) and reason_code is None:
            # v1 callback: (client, userdata, rc)
            rc = flags
        elif reason_code is not None:
            # v2 callback: (client, userdata, flags, reason_code, properties)
            rc = reason_code if isinstance(reason_code, int) else reason_code.value
        else:
            rc = -1
        reason = "clean disconnect" if rc == 0 else f"unexpected (rc={rc})"
        _LOGGER.warning(f"{DOMAIN} - MQTT DISCONNECTED: {reason}")
        if self._on_disconnect:
            self._on_disconnect(reason)

    def _on_mqtt_message(self, client, userdata, msg) -> None:
        """Handle incoming MQTT message (unchanged between v1 and v2)."""
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
        """Handle subscription confirmation (v2 signature with v1 compat).

        v1: (client, userdata, mid, granted_qos) — granted_qos is list of ints
        v2: (client, userdata, mid, reason_codes, properties) — reason_codes
            is list of ReasonCode objects

        In MQTT 3.1.1, SUBACK granted QoS 0x80 (128) means subscription
        failure — the broker rejected that topic. This is critical for
        diagnosing rc=128 disconnects.
        """
        raw = self._pending_subs.pop(mid, f"unknown(mid={mid})")
        # Batch subscribe: raw is comma-joined topic list
        topics = raw.split(",") if "," in raw else [raw]

        # Normalize reason_codes to list of ints
        if reason_codes is None:
            qos_values = []
        elif isinstance(reason_codes, list):
            qos_values = [
                rc.value if hasattr(rc, "value") else int(rc) for rc in reason_codes
            ]
        else:
            # paho v1: granted_qos is already a list of ints
            qos_values = (
                list(reason_codes)
                if hasattr(reason_codes, "__iter__")
                else [int(reason_codes)]
            )

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
