# pylint:disable=invalid-name,missing-function-docstring,missing-class-docstring,broad-exception-caught,logging-fstring-interpolation
"""mqtt_service_hub_eu.py — push provider implementation for the CCI/EU lineage.

``MqttServiceHubMixin`` implements the ``ApiImpl`` push contract for the
EU CCI/GPGA brand classes (``HyundaiCciApiEU`` and ``KiaCciApiEU`` both
mix it in). It carries the Service Hub HTTP methods that configure and
register the receive-only MQTT client, plus the Service Hub topic
schema (constants, builders, parser).

Endpoint wire shapes are live-probed; parsing is tolerant and shapes
that could not be confirmed leave the corresponding ``Vehicle`` fields
unknown (``None``).
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import requests

from . import const
from .const import (
    BRAND_GENESIS,
    BRAND_KIA,
    BRANDS,
    DOMAIN,
)
from .GspaApiEU import USER_AGENT_OK_HTTP
from .mqtt_client import MqttTransport
from .mqtt_service_hub import (
    ServiceHubProviderMixin,
    build_service_hub_status_topics,
    classify_service_hub_status_topic,
)
from .Token import Token
from .Vehicle import Vehicle

_LOGGER = logging.getLogger(__name__)


class MqttServiceHubMixin(ServiceHubProviderMixin):
    """Push contract implementation (Service Hub) for the CCI/EU lineage.

    Mixed into both ``HyundaiCciApiEU`` and ``KiaCciApiEU``; all
    brand-specific values (SERVICE_HUB_BASE, CCSP_CLIENT_SERVICE_ID,
    PUSH_PROVIDER_ID) and the session state
    (_service_hub_session, _service_hub_tid) live on the host class.
    """

    if TYPE_CHECKING:
        # Host-provided state (GspaApiEU.__init__) and brand attrs
        # (HyundaiCciApiEU / KiaCciApiEU) that this mixin relies on.
        _service_hub_session: requests.Session | None
        _service_hub_tid: str | None
        brand: int
        LANGUAGE: str
        SERVICE_HUB_BASE: str
        CCSP_CLIENT_SERVICE_ID: str
        PUSH_PROVIDER_ID: str

    def _service_hub_protocols(self, vehicle: Vehicle) -> list[str]:
        protocols = super()._service_hub_protocols(vehicle)
        protocols.extend(
            [
                MQTT_PROTOCOL_ID_DEVICE_RC_CONNECT,
                MQTT_PROTOCOL_ID_DEVICE_RC_COMMAND,
                MQTT_PROTOCOL_ID_DEVICE_RC_CONNECTCHECK,
                MQTT_PROTOCOL_ID_DEVICE_RC_CLOSE_MOBILE,
                MQTT_PROTOCOL_ID_DEVICE_RC_CLOSE_CAR,
                MQTT_PROTOCOL_ID_RC_CONNECT,
                MQTT_PROTOCOL_ID_RC_COMMAND,
                MQTT_PROTOCOL_ID_RC_CONNECTCHECK,
                MQTT_PROTOCOL_ID_RC_CLOSE_MOBILE,
                MQTT_PROTOCOL_ID_RC_CLOSE_CAR,
            ]
        )
        if vehicle.supports.ota_progress is True:
            protocols.append("vehicle.ota.otaprogress")
        if vehicle.supports.schedule_update is True:
            protocols.append("vehicle.ota.scheduleupdate")
        return protocols

    def _service_hub_apply_metadata(
        self, data: dict[str, Any], vehicle: Vehicle
    ) -> None:
        entries = data.get("vehicles", []) or []
        for entry in entries:
            if (
                isinstance(entry, dict)
                and entry.get("unit") == "hu"
                and entry.get("clientId")
            ):
                vehicle.hu_client_id = entry["clientId"]
        _apply_vehicle_capabilities(entries, vehicle)

    # ------------------------------------------------------------------
    # Push contract (ApiImpl) implementation
    # ------------------------------------------------------------------

    def get_push_topics(self, token: Token, vehicle: Vehicle) -> list[str]:
        """Topics to subscribe for a vehicle (Service Hub schema).

        Excluded on purpose (broker rejects with rc=128): the car status
        res topic, the vehicle/{huClientId}/... CarRemote group and the
        device closeremote topics. QoS 0 only.

        Conditional topics (location, OTA) are only added when the
        metadata capabilities report an explicit True.
        """
        client_id = token.mqtt_client_id
        vid = vehicle.mqtt_vehicle_id
        if not vid or not client_id:
            _LOGGER.info(
                f"{DOMAIN} - Push topics unavailable: missing "
                f"{'mqtt vehicle id' if not vid else 'client id'}"
            )
            return []
        topics: list[str] = build_car_status_topics(vid)
        topics.extend(build_device_remote_topics(client_id))
        topics.extend(build_ota_topics(vehicle))
        return topics

    def parse_push_message(self, topic: str, payload: bytes) -> CciPushMessage | None:
        """Parse a raw Service Hub MQTT delivery (push-contract method).

        The transport delivers raw (topic, payload bytes); JSON decoding
        and the envelope's raw-hex fallback belong to this schema. Returns
        None for unknown topic schemas.
        """
        msg = parse_mqtt_message(topic, payload)
        if msg.topic_group == "Unknown":
            return None
        return CciPushMessage(
            topic_group=msg.topic_group,
            topic_type=msg.topic_type,
            vehicle_id=msg.vehicle_id,
            payload=msg.payload,
            is_status=msg.topic_group == "CarStatus",
        )

    # ------------------------------------------------------------------
    # Shared Service Hub helpers
    # ------------------------------------------------------------------

    @property
    def _service_hub_brand(self) -> str:
        """Single-letter brand code the Service Hub API expects (H, K, G).

        The Service Hub body/params use one-letter brand codes, unlike
        the full brand names used in HTTP headers.
        """
        if BRANDS[self.brand] == BRAND_KIA:
            return "K"
        if BRANDS[self.brand] == BRAND_GENESIS:
            return "G"
        return "H"

    def _get_service_hub_url(self) -> str:
        """Service Hub REST base URL (host class SERVICE_HUB_BASE)."""
        base = getattr(self, "SERVICE_HUB_BASE", "")
        if not base:
            _LOGGER.error(
                f"{DOMAIN} - Pull base URL missing on {type(self).__name__} "
                "(mqtt_push requires SERVICE_HUB_BASE)"
            )
            return ""
        return f"https://{base}"

    def _get_service_hub_headers(self, token: Token) -> dict[str, str]:
        """Headers for the Service Hub endpoints (app-confirmed header set).

        Authorization carries the CCS (exchangeable) access token, plus
        client-os-code, locale, Accept-Language, X-Application-Id
        (push provider), EpitVersion, X-Service-Id and a per-request UUID
        in X-Request-Id. The exchangeable/non-ccs tokens are attached when
        the token carries them.
        """
        ccs_access = (token.access_token or "").removeprefix("Bearer ").strip()
        headers = {
            "Authorization": f"Bearer {ccs_access}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT_OK_HTTP,
            "client-os-code": "AOS",
            "locale": self.LANGUAGE,
            "Accept-Language": self.LANGUAGE,
            "X-Application-Id": self.PUSH_PROVIDER_ID,
            "EpitVersion": "EPITV2",
            "X-Service-Id": self.CCSP_CLIENT_SERVICE_ID,
            "X-Request-Id": str(uuid.uuid4()),
        }
        exchangeable = getattr(token, "exchangeable_token", None) or ""
        if exchangeable:
            headers["exchangeable-token"] = exchangeable
        non_ccs = getattr(token, "non_ccs_token", None) or ""
        if non_ccs:
            headers["non-ccs-token"] = non_ccs
        return headers

    # ------------------------------------------------------------------
    # Service Hub read endpoints (metadata + vehicle id)
    # ------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Metadata capabilities — Vehicle field mapping
# ---------------------------------------------------------------------------

# cap key → vehicle fields (app-confirmed metadata entry keys).
# Wire key names here carry the camelCase form used by the metadata
# entries; an unrecognized shape leaves the fields None (unknown) with
# a debug log of the keys seen in the response — live-probed shape to
# confirm on the first vehicle-equipped account.
#
# NOTE: entries missing any key leave that flag unset.
#
# vehicle field name → candidate wire keys
# Metadata capability mapping (live-probed on the production Hyundai EU
# Service Hub, 2026-10-12): the metadatalist response carries no
# capabilities dict — it lists per-unit protocol membership
# ({"vehicles": [{unit, clientId, vehicleId, protocols}, ...]}). A
# capability equals the presence of the matching protocol; a missing
# protocol means the vehicle does not support that feature. The hu
# entry carries the remote-control and closeremote protocol sets.
_CAPS_PROTOCOL_KEYS: dict[str, tuple[str, ...]] = {
    "hvac_close_remote": ("device.closeremote.precondition",),
    "media_close_remote": ("device.closeremote.media.vehiclestatus",),
    "ota_progress": ("vehicle.ota.otaprogress",),
    "schedule_update": ("vehicle.ota.scheduleupdate",),
}

# The location topic has no confirmed metadata protocol (re-add on evidence).


def _apply_vehicle_capabilities(entries: Any, vehicle: Vehicle) -> None:
    """Populate Vehicle capability flags from metadata entries.

    The metadata response carries one entry per unit (hu, tmu, ccu —
    live-probed on Hyundai; Kia returns tmu only). The hu entry carries
    the remote-control and closeremote protocol sets, so it is the
    preferred capability source; an entry without a hu falls back to
    the first one carrying a protocol list. True/False/None semantics
    are documented on the Vehicle capability fields.
    """
    protocols: set[str] | None = None
    hu_present = False
    candidates = list(entries or [])
    for entry in candidates:
        if isinstance(entry, dict) and entry.get("unit") == "hu":
            raw = entry.get("protocols")
            if isinstance(raw, list):
                protocols = {str(p) for p in raw}
                hu_present = True
                break
    if protocols is None:
        # tmu-only (live-confirmed Kia shape): mqtt_push stays unknown.
        for entry in candidates:
            if not isinstance(entry, dict):
                continue
            raw = entry.get("protocols")
            if isinstance(raw, list):
                protocols = {str(p) for p in raw}
                break
    if protocols is None:
        _LOGGER.debug(
            f"{DOMAIN} - Push metadata: no protocol list in response; "
            "capability flags stay unknown"
        )
        return
    for name, keys in _CAPS_PROTOCOL_KEYS.items():
        setattr(vehicle.supports, name, any(key in protocols for key in keys))
    if hu_present:
        vehicle.supports.mqtt_push = any(
            "remotecontroller." in key for key in protocols
        )


# ---------------------------------------------------------------------------
# Service Hub topic schema (constants, builders, parser) — CCI/EU wire
# format, distinct from the schema-neutral transport in mqtt_client.py.
# ---------------------------------------------------------------------------

# Topic prefixes (prefix + infix + postfix = full topic)
TOPIC_PREFIX_VEHICLE = "vehicle/"  # CarOta (mqtt vehicle id infix)
TOPIC_PREFIX_DEVICE = "device/"  # DeviceRemote (client id infix)
TOPIC_PREFIX_VSS = "service/phone/_/vss/"  # CarStatus.Status
TOPIC_PREFIX_CONNECTION = "service/phone/_/connection/"  # CarStatus.Connect
TOPIC_PREFIX_LOCATION = "service/phone/_/location/"  # CarStatus.Location
TOPIC_PREFIX_DEVICE_RES = "$/device/res/"  # DeviceRemote.Res

# Topic postfixes
POSTFIX_REMOTE_COMMAND = "/remotecontroller/command"
POSTFIX_REMOTE_CONNECT = "/remotecontroller/connect"
POSTFIX_REMOTE_CONNECTCHECK = "/remotecontroller/connectcheck"
POSTFIX_REMOTE_MOBILECLOSE = "/remotecontroller/mobileclose"
POSTFIX_REMOTE_VEHICLECLOSE = "/remotecontroller/vehicleclose"
POSTFIX_OTA_PROGRESS = "/_/ota/otaprogress"
POSTFIX_OTA_SCHEDULEUPDATE = "/_/ota/scheduleupdate"

# Protocol IDs — sent as plain strings in the Service Hub body
MQTT_PROTOCOL_ID_VSS = "service.phone.vss"
MQTT_PROTOCOL_ID_CONNECTION = "service.phone.connection"
MQTT_PROTOCOL_ID_RES = "service.phone.res"
# Vehicle RC protocols (CarRemote topics use the vehicle/ prefix)
MQTT_PROTOCOL_ID_RC_CONNECT = "vehicle.remotecontroller.connect"
MQTT_PROTOCOL_ID_RC_COMMAND = "vehicle.remotecontroller.command"
MQTT_PROTOCOL_ID_RC_CONNECTCHECK = "vehicle.remotecontroller.connectcheck"
MQTT_PROTOCOL_ID_RC_CLOSE_MOBILE = "vehicle.remotecontroller.mobileclose"
MQTT_PROTOCOL_ID_RC_CLOSE_CAR = "vehicle.remotecontroller.vehicleclose"
# Device RC protocols (DeviceRemote topics use the device/ prefix)
MQTT_PROTOCOL_ID_DEVICE_RC_CONNECT = "device.remotecontroller.connect"
MQTT_PROTOCOL_ID_DEVICE_RC_COMMAND = "device.remotecontroller.command"
MQTT_PROTOCOL_ID_DEVICE_RC_CONNECTCHECK = "device.remotecontroller.connectcheck"
MQTT_PROTOCOL_ID_DEVICE_RC_CLOSE_MOBILE = "device.remotecontroller.mobileclose"
MQTT_PROTOCOL_ID_DEVICE_RC_CLOSE_CAR = "device.remotecontroller.vehicleclose"
MQTT_PROTOCOL_ID_CCU_UPDATE = "statesync.vehicle.ccu.update"


@dataclass
class MqttMessage:
    """Parsed MQTT message delivered to the callback."""

    topic_group: str  # "CarStatus", "DeviceRemote", "CarOta", ...
    topic_type: str  # "Status", "Connect", "Res", ...
    vehicle_id: str  # Vehicle ID from topic infix
    payload: dict[str, Any]  # Parsed JSON (header + body)


@dataclass
class CciPushMessage:
    """Parsed Service Hub MQTT message — the push-message contract shape."""

    topic_group: str  # "CarStatus", "DeviceRemote", "CarOta", ...
    topic_type: str  # "Status", "Connect", "Res", ...
    vehicle_id: str  # push-level vehicle id (topic infix)
    payload: dict[str, Any]  # parsed JSON (header/body as delivered)
    is_status: bool = False


# ---------------------------------------------------------------------------
# Topic builders
# ---------------------------------------------------------------------------


def build_car_status_topics(vehicle_id: str) -> list[str]:
    """Build CarStatus topics for a vehicle.

    The res topic is excluded — the broker rejects phone-client
    subscriptions to it with rc=128.
    """
    return build_service_hub_status_topics(vehicle_id)


def build_device_remote_topics(client_id: str) -> list[str]:
    """Build DeviceRemote topics for a client (infix = device/client id)."""
    return [
        TOPIC_PREFIX_DEVICE + client_id + POSTFIX_REMOTE_COMMAND,
        TOPIC_PREFIX_DEVICE + client_id + POSTFIX_REMOTE_CONNECT,
        TOPIC_PREFIX_DEVICE + client_id + POSTFIX_REMOTE_CONNECTCHECK,
        TOPIC_PREFIX_DEVICE + client_id + POSTFIX_REMOTE_MOBILECLOSE,
        TOPIC_PREFIX_DEVICE + client_id + POSTFIX_REMOTE_VEHICLECLOSE,
        TOPIC_PREFIX_DEVICE_RES + client_id,
    ]


def build_ota_topics(vehicle: Vehicle) -> list[str]:
    """Build OTA topics for a vehicle (conditional on capabilities)."""
    if not vehicle.mqtt_vehicle_id:
        return []
    topics = []
    if vehicle.supports.ota_progress is True:
        topics.append(
            TOPIC_PREFIX_VEHICLE + vehicle.mqtt_vehicle_id + POSTFIX_OTA_PROGRESS
        )
    if vehicle.supports.schedule_update is True:
        topics.append(
            TOPIC_PREFIX_VEHICLE + vehicle.mqtt_vehicle_id + POSTFIX_OTA_SCHEDULEUPDATE
        )
    return topics


# ---------------------------------------------------------------------------
# Message parser
# ---------------------------------------------------------------------------


def parse_mqtt_message(topic: str, payload: bytes) -> MqttMessage:
    """Parse an MQTT message into a structured MqttMessage.

    Classifies the topic (group, type, infix), parses the JSON payload
    (raw hex fallback for non-JSON payloads).
    """
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
    """Classify a topic string into (group, type, infix)."""
    common = classify_service_hub_status_topic(topic)
    if common is not None:
        return common

    # DeviceRemote.Res (special $/device/res/ prefix)
    if topic.startswith(TOPIC_PREFIX_DEVICE_RES):
        infix = _extract_infix(topic, TOPIC_PREFIX_DEVICE_RES)
        return ("DeviceRemote", "Res", infix)

    # Device-prefixed topics (DeviceRemote remotecontroller/*)
    if topic.startswith(TOPIC_PREFIX_DEVICE):
        infix = _extract_infix(topic, TOPIC_PREFIX_DEVICE)
        suffix = topic[len(TOPIC_PREFIX_DEVICE) + len(infix) :]

        # connectcheck must be tested before connect — "/remotecontroller/
        # connect" is a substring of ".../connectcheck".
        if POSTFIX_REMOTE_COMMAND in suffix:
            return ("DeviceRemote", "Command", infix)
        if POSTFIX_REMOTE_CONNECTCHECK in suffix:
            return ("DeviceRemote", "ConnectCheck", infix)
        if POSTFIX_REMOTE_CONNECT in suffix:
            return ("DeviceRemote", "Connect", infix)
        if POSTFIX_REMOTE_MOBILECLOSE in suffix:
            return ("DeviceRemote", "MobileClose", infix)
        if POSTFIX_REMOTE_VEHICLECLOSE in suffix:
            return ("DeviceRemote", "CarClose", infix)
        return ("DeviceRemote", "Unknown", infix)

    # Vehicle-prefixed topics (CarOta — mqtt vehicle id infix)
    if topic.startswith(TOPIC_PREFIX_VEHICLE):
        infix = _extract_infix(topic, TOPIC_PREFIX_VEHICLE)
        suffix = topic[len(TOPIC_PREFIX_VEHICLE) + len(infix) :]
        if POSTFIX_OTA_PROGRESS in suffix:
            return ("CarOta", "Progress", infix)
        if POSTFIX_OTA_SCHEDULEUPDATE in suffix:
            return ("CarOta", "ScheduleUpdate", infix)
        return ("CarOta", "Unknown", infix)

    return ("Unknown", "Unknown", "")


def _extract_infix(topic: str, prefix: str) -> str:
    """Extract the vehicle/client id infix from a topic after the prefix.

    The infix is the segment between the prefix and the next '/' (or the
    whole remainder for a trailing-slash topic).
    """
    after_prefix = topic[len(prefix) :]
    slash_pos = after_prefix.find("/")
    if slash_pos > 0:
        return after_prefix[:slash_pos]
    if after_prefix.endswith("/"):
        return after_prefix[:-1]
    return after_prefix


class MqttPushSession:
    """Transport lifecycle for one VehicleManager's push fleet.

    Holds the receive-only MQTT transport, the started vehicle ids, the
    registered callbacks and the reconnect loop. ``VehicleManager`` stays
    the pass-through surface; all lifecycle logic lives here. Dependencies
    arrive from the manager: the region API (this module's mixin), the
    token accessor, the per-vehicle lookups and the fleet map.
    """

    def __init__(
        self,
        api: Any,
        token_fn: Callable[[], Token],
        refresh_fn: Callable[[], None],
        get_vehicle: Callable[[str], Vehicle],
        vehicles: Callable[[], dict[str, Vehicle]],
        after_close_fn: Callable[[], None] | None = None,
    ) -> None:
        self._api = api
        self._get_token = token_fn
        self._refresh_fn = refresh_fn
        self._get_vehicle = get_vehicle
        self._vehicles = vehicles
        self._after_close_fn = after_close_fn
        self._client: Any = None
        self._vehicle_ids: set[str] = set()
        self._status_callback: Callable[[str, str], None] | None = None
        self._connection_change_callback: Callable[[bool], None] | None = None
        self._reconnect_cancel: threading.Event | None = None
        self._reconnect_lock = threading.Lock()
        self._provider_lock = threading.RLock()
        self._providers: dict[str, Any] = {}
        self._provider_generation = 0
        self._closed = False
        self._subscribed_vehicle_ids: set[str] = set()

    @property
    def is_connected(self) -> bool:
        """Whether the push transport is connected."""
        with self._provider_lock:
            providers = list(self._providers.values())
        return any(provider.is_connected for provider in providers) or (
            self._client is not None and self._client.is_connected
        )

    def set_status_callback(self, callback: Callable[[str, str], None] | None) -> None:
        self._status_callback = callback

    def set_connection_change_callback(
        self, callback: Callable[[bool], None] | None
    ) -> None:
        self._connection_change_callback = callback
        with self._provider_lock:
            generation, client = self._provider_generation, self._client
        if client is not None:
            client.set_on_connection_change(
                lambda connected: self._report_connection_change(connected, generation)
            )

    def _report_connection_change(self, connected: bool, generation: int) -> None:
        callback = self._connection_change_callback
        if self._generation_active(generation) and callback is not None:
            callback(connected)

    def _generation_active(self, generation: int) -> bool:
        with self._provider_lock:
            return not self._closed and self._provider_generation == generation

    def ensure_started(self, vehicle_id: str) -> bool:
        """Register and connect unless stop or close invalidates this attempt."""
        with self._provider_lock:
            generation = self._provider_generation
            if self._closed:
                return False
        try:
            return self._ensure_started(vehicle_id, generation)
        finally:
            self._cleanup_closed_work()

    def _cleanup_closed_work(self) -> None:
        if self._closed and self._after_close_fn is not None:
            try:
                self._after_close_fn()
            except Exception as err:
                _LOGGER.warning(
                    "%s - Push late shutdown failed (%s)",
                    DOMAIN,
                    type(err).__name__[:64],
                )

    def _attempt_active(self, generation: int, cancel: threading.Event | None) -> bool:
        return self._generation_active(generation) and (
            cancel is None or not cancel.is_set()
        )

    def _ensure_started(
        self, vehicle_id: str, generation: int, cancel: threading.Event | None = None
    ) -> bool:
        token = self._get_token()
        vehicle = self._get_vehicle(vehicle_id)
        factory = getattr(self._api, "get_push_provider", None)
        provider = factory(token, vehicle) if callable(factory) else self._api
        if not self._attempt_active(generation, cancel):
            return False
        starter = getattr(provider, "start_push", None)
        if callable(starter):
            return self._start_provider(vehicle_id, provider, generation, cancel)
        if self._client is not None:
            if not self._register_new_vehicle(vehicle, token, generation, cancel):
                return False
            with self._provider_lock:
                if not self._attempt_active(generation, cancel):
                    return False
                self._vehicle_ids.add(vehicle_id)
            if self._client is not None and self._client.is_connected:
                self._subscribe_vehicle_topics(vehicle_id)
            return self._attempt_active(generation, cancel)

        broker = self._api.get_push_broker_info(token)
        if not self._attempt_active(generation, cancel):
            return False
        if not broker or not broker.get("mqtt_host"):
            _LOGGER.debug(f"{DOMAIN} - Push broker info not available")
            return False
        reg = self._api.register_push_client(token)
        if not self._attempt_active(generation, cancel):
            return False
        if not reg:
            _LOGGER.debug(f"{DOMAIN} - Push client registration failed")
            return False
        creds = self._api.get_push_credentials(token)
        if not self._attempt_active(generation, cancel):
            return False
        if not self._register_new_vehicle(vehicle, token, generation, cancel):
            return False

        def on_message(topic: str, payload: bytes) -> None:
            if self._attempt_active(generation, cancel):
                self._on_push_delivery_raw(topic, payload)

        def on_connect() -> None:
            if self._attempt_active(generation, cancel):
                self._on_push_connect()

        def on_disconnect(reason: str) -> None:
            if self._attempt_active(generation, cancel):
                self._on_push_disconnect(reason, generation)

        client = MqttTransport(
            on_message=on_message, on_connect=on_connect, on_disconnect=on_disconnect
        )
        client.configure(
            broker_host=broker["mqtt_host"],
            broker_port=broker.get("mqtt_port", 8883),
            client_id=reg.get("client_id"),
            broker_username=creds[0] if creds else None,
            broker_password=creds[1] if creds else None,
        )
        if self._connection_change_callback:
            client.set_on_connection_change(
                lambda connected: (
                    self._connection_change_callback(connected)
                    if self._attempt_active(generation, cancel)
                    and self._connection_change_callback
                    else None
                )
            )
        with self._provider_lock:
            if not self._attempt_active(generation, cancel):
                return False
            self._vehicle_ids.add(vehicle_id)
            self._client = client
        try:
            client.connect()
        except (ValueError, OSError):
            with self._provider_lock:
                if self._client is client:
                    self._vehicle_ids.discard(vehicle_id)
                    self._client = None
            raise
        finally:
            if not self._attempt_active(generation, cancel):
                client.disconnect()
                with self._provider_lock:
                    if self._client is client:
                        self._client = None
        if not self._attempt_active(generation, cancel):
            return False
        if client.is_connected:
            self._subscribe_vehicle_topics(vehicle_id)
        return self._attempt_active(generation, cancel)

    def _start_provider(
        self,
        vehicle_id: str,
        provider: Any,
        generation: int,
        cancel: threading.Event | None = None,
    ) -> bool:
        """Attach a provider-owned session instead of creating another client."""
        with self._provider_lock:
            if not self._attempt_active(generation, cancel):
                return False
            previous = self._providers.get(vehicle_id)
            self._providers[vehicle_id] = provider
            self._vehicle_ids.add(vehicle_id)
        if previous is not None and previous is not provider:
            previous.stop_push()

        def status(changed_vehicle: str, kind: str) -> None:
            with self._provider_lock:
                current = (
                    self._attempt_active(generation, cancel)
                    and self._providers.get(vehicle_id) is provider
                )
            if current and changed_vehicle == vehicle_id and self._status_callback:
                self._status_callback(vehicle_id, kind)

        def connection(connected: bool) -> None:
            with self._provider_lock:
                current = (
                    self._attempt_active(generation, cancel)
                    and self._providers.get(vehicle_id) is provider
                )
            if not current:
                return
            if self._connection_change_callback:
                self._connection_change_callback(connected)
            if not connected:
                self._on_push_disconnect("provider disconnected", generation)

        try:
            started = bool(provider.start_push(status, connection))
        except Exception:
            with self._provider_lock:
                stopped = not self._attempt_active(generation, cancel)
                if self._providers.get(vehicle_id) is provider:
                    self._providers.pop(vehicle_id, None)
                    self._vehicle_ids.discard(vehicle_id)
            provider.stop_push()
            if stopped:
                return False
            raise
        with self._provider_lock:
            if not self._attempt_active(generation, cancel):
                if self._providers.get(vehicle_id) is provider:
                    self._providers.pop(vehicle_id, None)
                    self._vehicle_ids.discard(vehicle_id)
                stopped = True
            else:
                stopped = False
        if stopped:
            provider.stop_push()
            return False
        return started

    def _register_new_vehicle(
        self,
        vehicle: Vehicle,
        token: Token,
        generation: int,
        cancel: threading.Event | None = None,
    ) -> bool:
        """Run the per-vehicle registration steps of the push contract."""
        self._api.get_push_vehicle_identity(self._get_token(), vehicle)
        if not self._attempt_active(generation, cancel):
            return False
        if not vehicle.mqtt_vehicle_id:
            _LOGGER.warning(
                f"{DOMAIN} - Push identity missing vehicle id for {vehicle.id}"
            )
            return False
        registered = self._api.register_push_vehicle(self._get_token(), vehicle)
        if not self._attempt_active(generation, cancel):
            return False
        if registered is None:
            _LOGGER.warning(
                f"{DOMAIN} - Push protocol registration failed for {vehicle.id}"
            )
            return False
        return True

    def _subscribe_vehicle_topics(self, vehicle_id: str) -> None:
        """Subscribe the topics the region API provides for the vehicle.

        Local computation only — no HTTP; a failure here is a bug.
        """
        with self._provider_lock:
            if (
                self._closed
                or self._client is None
                or vehicle_id in self._subscribed_vehicle_ids
            ):
                return
            assert self._client is not None
            vehicle = self._get_vehicle(vehicle_id)
            topics = self._api.get_push_topics(self._get_token(), vehicle)
            if topics:
                self._client.subscribe(topics)
                self._subscribed_vehicle_ids.add(vehicle_id)

    def _on_push_connect(self) -> None:
        """Subscribe topics for each started push vehicle on (re)connect."""
        for vehicle_id in list(self._vehicle_ids):
            if vehicle_id in self._vehicles():
                try:
                    self._subscribe_vehicle_topics(vehicle_id)
                except Exception:
                    _LOGGER.exception(
                        f"{DOMAIN} - Push subscribe failed for {vehicle_id}"
                    )

    def _on_push_delivery_raw(self, topic: str, payload: bytes) -> None:
        """Raw delivery from the transport; parse via the region API.

        Called from the paho background thread — must be thread-safe.
        """
        message = self._api.parse_push_message(topic, payload)
        if message is None:
            return
        self._on_push_message(message)

    def _on_push_message(self, message: Any) -> None:
        """Dispatch a parsed push message (duck-typed contract shape).

        Expected attributes: is_status (bool), vehicle_id (str, push
        level), topic_type (str). Messages other than status deliveries
        are recorded by the provider's parser and ignored here.
        """
        if not getattr(message, "is_status", False) or not self._status_callback:
            return
        vehicle_id = self._vehicle_id_for_push(getattr(message, "vehicle_id", ""))
        if vehicle_id is None:
            _LOGGER.debug(
                f"{DOMAIN} - Push vehicle id {message.vehicle_id} unknown, dropping"
            )
            return
        try:
            self._status_callback(vehicle_id, message.topic_type)
        except Exception:
            _LOGGER.exception(f"{DOMAIN} - Push status callback error")

    def _vehicle_id_for_push(self, mqtt_vehicle_id: str) -> str | None:
        """Map the push-level vehicle id to a fleet key.

        The fleet map is keyed by ``vehicle.id``; push messages carry the
        topic-level id. Multi-vehicle safe: no per-vehicle state — the
        identity lives on each Vehicle.
        """
        for vehicle_id, vehicle in self._vehicles().items():
            if vehicle.mqtt_vehicle_id and vehicle.mqtt_vehicle_id == mqtt_vehicle_id:
                return vehicle_id
        return None

    def close(self) -> None:
        """Permanently stop delivery and reject future startup attempts."""
        with self._provider_lock:
            if self._closed:
                return
            self._closed = True
        self.stop()

    def stop(self) -> None:
        """Disconnect push while allowing a later explicit restart."""
        with self._provider_lock:
            self._provider_generation += 1
            providers = list(self._providers.values())
            self._providers.clear()
            self._vehicle_ids.clear()
            self._subscribed_vehicle_ids.clear()
            cancel, self._reconnect_cancel = self._reconnect_cancel, None
            client, self._client = self._client, None
        if cancel is not None:
            cancel.set()
        try:
            with ExitStack() as cleanup:
                for provider in providers:
                    cleanup.callback(provider.stop_push)
        finally:
            if client is not None:
                try:
                    client.disconnect()
                except Exception:
                    _LOGGER.debug("Push disconnect failed (already disconnected)")

    def _on_push_disconnect(
        self, reason: str, expected_generation: int | None = None
    ) -> None:
        """Reconnect after unexpected drops unless stop or close invalidates it."""
        _LOGGER.debug(f"{DOMAIN} - Push disconnected: {reason}")
        with self._provider_lock:
            if (
                self._closed
                or reason == "clean disconnect"
                or not self._vehicle_ids
                or (
                    expected_generation is not None
                    and expected_generation != self._provider_generation
                )
            ):
                return
            generation = self._provider_generation
            self._subscribed_vehicle_ids.clear()
            previous_cancel = self._reconnect_cancel
            cancel_event = threading.Event()
            self._reconnect_cancel = cancel_event
            vehicle_ids = list(self._vehicle_ids)
        if previous_cancel is not None:
            previous_cancel.set()

        def _reconnect_with_backoff() -> None:
            with ExitStack() as cleanup:
                cleanup.callback(self._cleanup_closed_work)
                delay = const.MQTT_PUSH_RECONNECT_INITIAL_DELAY
                while not cancel_event.is_set():
                    _LOGGER.debug(f"{DOMAIN} - Push reconnect attempt in {delay}s...")
                    if cancel_event.wait(delay):
                        return
                    with self._reconnect_lock:
                        if not self._attempt_active(generation, cancel_event):
                            return
                        try:
                            try:
                                self._refresh_fn()
                            except Exception as token_ex:
                                _LOGGER.warning(
                                    "%s - Push reconnect token refresh failed (%s)",
                                    DOMAIN,
                                    type(token_ex).__name__[:64],
                                )
                            if cancel_event.is_set() or not self._generation_active(
                                generation
                            ):
                                return
                            if not self._cleanup_client(generation):
                                return
                            ok = all(
                                not cancel_event.is_set()
                                and self._generation_active(generation)
                                and self._ensure_started(vid, generation, cancel_event)
                                for vid in vehicle_ids
                            )
                            if ok and self.is_connected:
                                _LOGGER.debug(f"{DOMAIN} - Push reconnected")
                                return
                        except Exception as ex:
                            _LOGGER.warning(
                                "%s - Push reconnect failed (%s)",
                                DOMAIN,
                                type(ex).__name__[:64],
                            )
                    delay = min(delay * 2, const.MQTT_PUSH_RECONNECT_MAX_DELAY)
                _LOGGER.debug(f"{DOMAIN} - Push reconnect cancelled")

        threading.Thread(
            target=_reconnect_with_backoff, name="push-reconnect", daemon=True
        ).start()

    def _cleanup_client(self, generation: int) -> bool:
        with self._provider_lock:
            if not self._generation_active(generation):
                return False
            client, self._client = self._client, None
        if client is not None:
            try:
                client.disconnect()
            except Exception:
                _LOGGER.debug("Push disconnect during cleanup failed (already down)")
        return True
