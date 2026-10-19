# pylint:disable=invalid-name,missing-function-docstring,missing-class-docstring,broad-exception-caught,logging-fstring-interpolation
"""mqtt_service_hub.py — push provider implementation for the CCI/EU lineage.

``MqttServiceHubMixin`` implements the ``ApiImpl`` push contract for the
EU CCI/GPGA brand classes (``HyundaiCciApiEU`` and ``KiaCciApiEU`` both
mix it in). It carries the Service Hub HTTP methods that configure and
register the receive-only MQTT client, plus the Service Hub topic
schema (constants, builders, parser).

Endpoint wire shapes are live-probed/app-confirmed. Where a shape could
not be confirmed on production endpoints (the metadata capabilities
report), parsing is tolerant and the corresponding ``Vehicle`` fields
stay ``None`` (unknown) — conditional topics are only subscribed on an
explicit ``True``.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

import requests

from .const import (
    BRAND_GENESIS,
    BRAND_KIA,
    BRANDS,
    DOMAIN,
    MQTT_PUSH_CONNECTION_STATE,
)
from .GspaApiEU import USER_AGENT_OK_HTTP
from .Token import Token
from .Vehicle import Vehicle

_LOGGER = logging.getLogger(__name__)


class MqttServiceHubMixin:
    """Push contract implementation (Service Hub) for the CCI/EU lineage.

    Mixed into both ``HyundaiCciApiEU`` and ``KiaCciApiEU``. All
    brand-specific values are host-class attributes:
      - SERVICE_HUB_BASE (REST base, host:port)
      - CCSP_CLIENT_SERVICE_ID (X-Service-Id header value)
      - PUSH_PROVIDER_ID (X-Application-Id header value)
      - supports_mqtt_push (push contract flag)
    Session state lives on the host instance:
      - self._service_hub_session (requests.Session | None)
      - self._service_hub_tid (str | None, set from device/host response)
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

    # ------------------------------------------------------------------
    # Push contract (ApiImpl) implementation
    # ------------------------------------------------------------------

    def get_push_broker_info(self, token: Token) -> dict[str, Any] | None:
        """GET api/v3/servicehub/device/host — get the MQTT broker config.

        Returns dict with keys: mqtt_host, mqtt_port, use_ssl, http_host,
        http_port. The broker credential triple (username/password) comes
        from device/register, not the account Token. Stores the response tid (header) for the subsequent
        Service Hub calls.
        """
        url = self._get_service_hub_url() + "/api/v3/servicehub/device/host"
        headers = self._get_service_hub_headers(token)
        if self._service_hub_session is None:
            # One session for all Service Hub calls — the server may track
            # HTTP session state (cookies) between host → register → protocol.
            self._service_hub_session = requests.Session()
        try:
            response = self._service_hub_session.get(
                url, headers=headers, timeout=(5, 30)
            )
            if response.status_code != 200:
                _LOGGER.warning(
                    f"{DOMAIN} - Service Hub device/host failed: "
                    f"HTTP {response.status_code} — {response.text[:300]}"
                )
                return None
            tid = response.headers.get("tid")
            if tid:
                self._service_hub_tid = tid
                _LOGGER.debug(f"{DOMAIN} - Push device/host tid (header): {tid}")
            else:
                _LOGGER.info(f"{DOMAIN} - Push device/host: no tid header in response")
            data = response.json()
            _LOGGER.debug(f"{DOMAIN} - Push device/host response: {data}")
            mqtt_broker = data.get("mqtt", {})
            host = mqtt_broker.get("host")
            port = mqtt_broker.get("port", 8883)
            if not host:
                _LOGGER.warning(
                    f"{DOMAIN} - Push device/host: no mqtt host in response"
                )
                return None
            _LOGGER.debug(f"{DOMAIN} - Push broker: {host}:{port}")
            return {
                "mqtt_host": host,
                "mqtt_port": port,
                "use_ssl": mqtt_broker.get("ssl") is not None,
                "http_host": data.get("http", {}).get("host"),
                "http_port": data.get("http", {}).get("port"),
            }
        except Exception as ex:
            _LOGGER.error(f"{DOMAIN} - Service Hub device/host error: {ex}")
        return None

    def register_push_client(self, token: Token) -> dict[str, Any] | None:
        """POST api/v3/servicehub/device/register — register device for push.

        Body: {"unit": "mobile", "uuid": device_id}. The tid session
        header is required (422 without it). Returns clientId,
        deviceId, username, password.
        """
        url = self._get_service_hub_url() + "/api/v3/servicehub/device/register"
        headers = self._get_service_hub_headers(token)
        headers["tid"] = self._service_hub_tid or str(uuid.uuid4())
        body = {
            "unit": "mobile",
            "uuid": token.device_id or "",
        }
        try:
            session = self._service_hub_session or requests
            response = session.post(url, json=body, headers=headers, timeout=(5, 30))
            if response.status_code != 200:
                _LOGGER.warning(
                    f"{DOMAIN} - Service Hub device/register failed: "
                    f"HTTP {response.status_code} — {response.text[:300]}"
                )
                return None
            data = response.json()
            _LOGGER.debug(
                f"{DOMAIN} - Push device/register response keys: {list(data.keys())}"
            )
            client_id = data.get("clientId") or data.get("client_id")
            result = {
                "client_id": client_id,
                "device_id": data.get("deviceId"),
                "username": data.get("username"),
                "password": data.get("password"),
            }
            if not client_id:
                _LOGGER.warning(
                    f"{DOMAIN} - Push device/register: no clientId in response"
                )
                return None
            token.mqtt_client_id = client_id
            return result
        except Exception as ex:
            _LOGGER.error(f"{DOMAIN} - Service Hub device/register error: {ex}")
        return None

    def register_push_vehicle(
        self, token: Token, vehicle: Vehicle
    ) -> dict[str, Any] | None:
        """POST api/v3/servicehub/device/protocol — register vehicle protocols.

        Body (app-confirmed 4-field DTO shape): protocols, protocolId,
        carId, brand. The protocols list starts with VSS, CONNECTION and
        RES, adds both device.* and vehicle.* remote-controller sets,
        and appends the OTA protocols only for vehicles that report the
        corresponding metadata capabilities.
        """
        url = self._get_service_hub_url() + "/api/v3/servicehub/device/protocol"
        headers = self._get_service_hub_headers(token)
        headers["tid"] = self._service_hub_tid or str(uuid.uuid4())
        headers["client-id"] = token.mqtt_client_id or ""

        # Base protocols — always included
        protocols = [
            MQTT_PROTOCOL_ID_VSS,
            MQTT_PROTOCOL_ID_CONNECTION,
            MQTT_PROTOCOL_ID_RES,
        ]
        # RC protocols — the app-confirmed registration includes BOTH
        # device.* AND vehicle.* variants.
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
        # OTA protocols — only when the vehicle reports the capability.
        if vehicle.supports_ota_progress is True:
            protocols.append("vehicle.ota.otaprogress")
        if vehicle.supports_schedule_update is True:
            protocols.append("vehicle.ota.scheduleupdate")

        body = {
            "protocols": protocols,
            "protocolId": MQTT_PROTOCOL_ID_CCU_UPDATE,
            "carId": vehicle.id or "",
            "brand": self._service_hub_brand,
        }
        _LOGGER.debug(f"{DOMAIN} - Push device/protocol REQUEST: body={body}")
        try:
            session = self._service_hub_session or requests
            response = session.post(url, json=body, headers=headers, timeout=(5, 30))
            if response.status_code != 200:
                _LOGGER.warning(
                    f"{DOMAIN} - Service Hub device/protocol failed: "
                    f"HTTP {response.status_code} — {response.text[:300]}"
                )
                return None
            data = response.json() if response.text.strip() else {}
            return data
        except Exception as ex:
            _LOGGER.error(f"{DOMAIN} - Service Hub device/protocol error: {ex}")
        return None

    def get_push_vehicle_identity(
        self, token: Token, vehicle: Vehicle
    ) -> dict[str, Any]:
        """Get the vehicle push identity: metadata + MQTT vehicle id.

        Combines the two Service Hub reads (metadatalist + vehicleId).
        Side effects: populates vehicle.hu_client_id, vehicle.mqtt_vehicle_id
        and the capability flags. Returns a dict of the two level ids.
        """
        self._service_hub_get_metadata(token, vehicle)
        self._service_hub_get_vehicle_id(token, vehicle)
        return {
            "mqtt_vehicle_id": vehicle.mqtt_vehicle_id,
            "hu_client_id": vehicle.hu_client_id,
        }

    def get_push_connection_state(
        self, token: Token
    ) -> MQTT_PUSH_CONNECTION_STATE | None:
        """GET api/v3/vstatus/connstate — push connection state.

        Query: clientId — no tid/client-id headers.
        """
        headers = self._get_service_hub_headers(token)
        # clientId only — no tid/client-id headers on this endpoint.
        params = {
            "clientId": token.mqtt_client_id or "",
        }
        try:
            full_url = self._service_hub_get_url_with_params(
                "/api/v3/vstatus/connstate", params
            )
            session = self._service_hub_session or requests
            response = session.get(full_url, headers=headers, timeout=(5, 30))
            if response.status_code != 200:
                _LOGGER.warning(
                    f"{DOMAIN} - Service Hub connstate failed: "
                    f"HTTP {response.status_code} — {response.text[:300]}"
                )
                return None
            data: dict[str, Any] = response.json()
            raw = data.get("connState") or data.get("state") or data.get("status")
            try:
                state = MQTT_PUSH_CONNECTION_STATE(str(raw).upper())
                _LOGGER.debug(f"{DOMAIN} - Push connection state: {state.value}")
                return state
            except ValueError:
                _LOGGER.debug(f"{DOMAIN} - Push connection state unknown value: {raw}")
                return None
        except Exception as ex:
            _LOGGER.error(f"{DOMAIN} - Service Hub connstate error: {ex}")
        return None

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
        topics: list[str] = build_car_status_topics(
            vid, vehicle.supports_speed_event is True
        )
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
                "(supports_mqtt_push=True requires SERVICE_HUB_BASE)"
            )
            return ""
        return f"https://{base}"

    def _service_hub_get_url_with_params(
        self, path: str, params: dict[str, Any]
    ) -> str:
        """Build a Service Hub URL with query params (app-confirmed encoding).

        The server validates parameter values exactly as sent
        (live-probed): '@' is preserved, not percent-encoded.
        """
        base = self._get_service_hub_url()
        full_url = base + path
        if not params:
            return full_url
        safe_chars = "-_.~!$'()*,;=:@/?"
        parts = [
            f"{quote(str(k), safe=safe_chars)}={quote(str(v), safe=safe_chars)}"
            for k, v in params.items()
        ]
        return f"{full_url}?{'&'.join(parts)}"

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

    def _service_hub_get_metadata(
        self, token: Token, vehicle: Vehicle
    ) -> dict[str, Any] | None:
        """GET api/v3/servicehub/vehicles/metadatalist — vehicle push metadata.

        Query: carId + brand. Returns the full response; populates
        vehicle.hu_client_id (entry with unit=="hu") and the capability
        flags (tolerant parsing — see _apply_vehicle_capabilities).
        """
        headers = self._get_service_hub_headers(token)
        headers["tid"] = self._service_hub_tid or str(uuid.uuid4())
        headers["client-id"] = token.mqtt_client_id or ""
        # carId and brand are query params (the rest are headers)
        params = {
            "carId": vehicle.id or "",
            "brand": self._service_hub_brand,
        }
        try:
            full_url = self._service_hub_get_url_with_params(
                "/api/v3/servicehub/vehicles/metadatalist", params
            )
            session = self._service_hub_session or requests
            response = session.get(full_url, headers=headers, timeout=(5, 30))
            if response.status_code != 200:
                _LOGGER.warning(
                    f"{DOMAIN} - Service Hub metadatalist failed: "
                    f"HTTP {response.status_code} — {response.text[:300]}"
                )
                return None
            data: dict[str, Any] = response.json()
            _LOGGER.debug(f"{DOMAIN} - Push metadatalist response: {data}")
            entries = data.get("vehicles", []) or []
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                if entry.get("unit") == "hu" and entry.get("clientId"):
                    vehicle.hu_client_id = entry["clientId"]
            _apply_vehicle_capabilities(entries, vehicle)
            return data
        except Exception as ex:
            _LOGGER.error(f"{DOMAIN} - Service Hub metadatalist error: {ex}")
        return None

    def _service_hub_get_vehicle_id(self, token: Token, vehicle: Vehicle) -> str | None:
        """POST api/v3/servicehub/vehicleId — get the vehicle MQTT id.

        Query: carId + brand. The MQTT vehicle id is used as the infix
        of the per-vehicle topics.
        """
        headers = self._get_service_hub_headers(token)
        headers["tid"] = self._service_hub_tid or str(uuid.uuid4())
        headers["client-id"] = token.mqtt_client_id or ""
        # carId and brand are query params (the rest are headers)
        params = {
            "carId": vehicle.id or "",
            "brand": self._service_hub_brand,
        }
        try:
            full_url = self._service_hub_get_url_with_params(
                "/api/v3/servicehub/vehicleId", params
            )
            session = self._service_hub_session or requests
            response = session.post(full_url, headers=headers, timeout=(5, 30))
            if response.status_code != 200:
                _LOGGER.warning(
                    f"{DOMAIN} - Service Hub vehicleId failed: "
                    f"HTTP {response.status_code} — {response.text[:300]}"
                )
                return None
            data: dict[str, Any] = response.json()
            vehicle_id = data.get("vehicleId") or data.get("carId")
            if not vehicle_id:
                return None
            vehicle.mqtt_vehicle_id = str(vehicle_id)
            return str(vehicle_id)
        except Exception as ex:
            _LOGGER.error(f"{DOMAIN} - Service Hub vehicleId error: {ex}")
        return None


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
    "supports_hvac_close_remote": ("device.closeremote.precondition",),
    "supports_media_close_remote": ("device.closeremote.media.vehiclestatus",),
    "supports_ota_progress": ("vehicle.ota.otaprogress",),
    "supports_schedule_update": ("vehicle.ota.scheduleupdate",),
}

# Protocols with no confirmed topic mapping are left None (unknown) —
# e.g. the location topic has no 1:1 protocol counterpart, so
# supports_speed_event is never set from metadata.


def _apply_vehicle_capabilities(entries: Any, vehicle: Vehicle) -> None:
    """Populate Vehicle capability flags from metadata entries.

    A flag is True only when its matching protocol appears in the
    vehicle's protocol list; False when the list was read and the
    protocol is absent; None when no protocol list was available
    (response without per-vehicle protocol membership).
    """
    protocols: set[str] | None = None
    for entry in entries or []:
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
    for field, keys in _CAPS_PROTOCOL_KEYS.items():
        setattr(vehicle, field, any(key in protocols for key in keys))


# ---------------------------------------------------------------------------
# Service Hub MQTT topic schema (wire format: topic constants +
# protocol ids + message data classes + builders + parser).
# These belong to the CCI/EU Service Hub wire format, NOT to the generic
# MQTT transport, which stays schema-neutral (mqtt_client.py).
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


def build_car_status_topics(vehicle_id: str, include_location: bool) -> list[str]:
    """Build CarStatus topics for a vehicle.

    The res topic is excluded — the broker rejects phone-client
    subscriptions to it with rc=128.
    """
    topics = [
        TOPIC_PREFIX_VSS + vehicle_id,
        TOPIC_PREFIX_CONNECTION + vehicle_id,
    ]
    if include_location:
        topics.append(TOPIC_PREFIX_LOCATION + vehicle_id)
    return topics


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
    if vehicle.supports_ota_progress is True:
        topics.append(
            TOPIC_PREFIX_VEHICLE + vehicle.mqtt_vehicle_id + POSTFIX_OTA_PROGRESS
        )
    if vehicle.supports_schedule_update is True:
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
    # CarStatus topics (service/phone/_/ prefix)
    if topic.startswith(TOPIC_PREFIX_VSS):
        return ("CarStatus", "Status", _extract_infix(topic, TOPIC_PREFIX_VSS))
    if topic.startswith(TOPIC_PREFIX_CONNECTION):
        return ("CarStatus", "Connect", _extract_infix(topic, TOPIC_PREFIX_CONNECTION))
    if topic.startswith(TOPIC_PREFIX_LOCATION):
        return ("CarStatus", "Location", _extract_infix(topic, TOPIC_PREFIX_LOCATION))

    # DeviceRemote.Res (special $/device/res/ prefix)
    if topic.startswith(TOPIC_PREFIX_DEVICE_RES):
        infix = _extract_infix(topic, TOPIC_PREFIX_DEVICE_RES)
        return ("DeviceRemote", "Res", infix)

    # Device-prefixed topics (DeviceRemote remotecontroller/*)
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
