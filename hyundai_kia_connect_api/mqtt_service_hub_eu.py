# pylint:disable=invalid-name,missing-function-docstring,missing-class-docstring,broad-exception-caught,logging-fstring-interpolation
"""mqtt_service_hub_eu.py — push provider implementation for the CCI/EU lineage.

``MqttServiceHubMixin`` implements the ``ApiImpl`` push contract for the
EU CCI/GPGA brand classes (``HyundaiCciApiEU`` and ``KiaCciApiEU`` both
mix it in). It carries the Service Hub HTTP methods that configure and register
the receive-only MQTT client, the capability mapping, and the EU-only
DeviceRemote/OTA topic family (phone-family topics live in
mqtt_service_hub.py).

Endpoint wire shapes are live-probed; parsing is tolerant and shapes
that could not be confirmed leave the corresponding ``Vehicle`` fields
unknown (``None``).
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, Any

import requests

from .const import (
    BRAND_GENESIS,
    BRAND_KIA,
    BRANDS,
    DOMAIN,
)
from .GspaApiEU import USER_AGENT_OK_HTTP
from .mqtt_service_hub import (
    PushMessage,
    ServiceHubProviderMixin,
    _extract_infix,
    build_car_status_topics,
    classify_phone_topic,
    decode_payload,
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
        if not isinstance(entries, list):
            return
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

    def parse_push_message(self, topic: str, payload: bytes) -> PushMessage | None:
        """Parse a raw Service Hub MQTT delivery (push-contract method).

        Phone topics parse via the neutral module; device/vehicle topics
        via the local branches. None only for finally-Unknown topics.
        """
        msg = parse_mqtt_message(topic, payload)
        if msg.topic_group == "Unknown":
            return None
        return msg

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


# ---------------------------------------------------------------------------
# Topic builders (DeviceRemote / OTA families — EU-only schemas)
# ---------------------------------------------------------------------------


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


def parse_mqtt_message(topic: str, payload: bytes) -> PushMessage:
    """Parse an MQTT delivery into a PushMessage (schema-internal).

    Phone-family topics classify via the neutral module; device/
    vehicle topics via the local branches. decode_payload covers both.
    """
    topic_group, topic_type, vehicle_id = _classify_topic(topic)
    return PushMessage(
        topic_group=topic_group,
        topic_type=topic_type,
        vehicle_id=vehicle_id,
        payload=decode_payload(payload),
        is_status=topic_group == "CarStatus",
    )


def _classify_topic(topic: str) -> tuple[str, str, str]:
    """Classify a topic string into (group, type, infix)."""
    # Phone family (service/phone/_/ prefix) — neutral contract
    phone = classify_phone_topic(topic)
    if phone is not None:
        return phone

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
