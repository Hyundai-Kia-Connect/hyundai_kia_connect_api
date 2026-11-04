"""Shared Service Hub registration and topic construction for push providers."""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

import requests

from .const import DOMAIN, MQTT_PUSH_CONNECTION_STATE
from .Token import Token
from .Vehicle import Vehicle

_LOGGER = logging.getLogger(__name__)

_SERVICE_HUB_STAGES = {
    "/api/v3/servicehub/device/host": "broker discovery",
    "/api/v3/servicehub/device/register": "client registration",
    "/api/v3/servicehub/device/protocol": "vehicle registration",
    "/api/v3/servicehub/vehicles/metadatalist": "vehicle metadata",
    "/api/v3/servicehub/vehicleId": "vehicle identity",
    "/api/v3/vstatus/connstate": "connection state",
}

TOPIC_PREFIX_VSS = "service/phone/_/vss/"
TOPIC_PREFIX_CONNECTION = "service/phone/_/connection/"
TOPIC_PREFIX_LOCATION = "service/phone/_/location/"
TOPIC_PREFIX_RESULT = "service/phone/_/res/"
MQTT_PROTOCOL_ID_VSS = "service.phone.vss"
MQTT_PROTOCOL_ID_CONNECTION = "service.phone.connection"
MQTT_PROTOCOL_ID_RES = "service.phone.res"
MQTT_PROTOCOL_ID_CCU_UPDATE = "statesync.vehicle.ccu.update"


def build_service_hub_status_topics(
    vehicle_id: str, *, result_client_id: str | None = None
) -> list[str]:
    """Build common status topics, with result delivery only where supported."""
    topics = [TOPIC_PREFIX_VSS + vehicle_id]
    if result_client_id is not None:
        topics.append(TOPIC_PREFIX_RESULT + result_client_id)
    topics.append(TOPIC_PREFIX_CONNECTION + vehicle_id)
    return topics


def classify_service_hub_status_topic(
    topic: str, *, include_result: bool = False
) -> tuple[str, str, str] | None:
    prefixes = [
        (TOPIC_PREFIX_VSS, "Status"),
        (TOPIC_PREFIX_CONNECTION, "Connect"),
        (TOPIC_PREFIX_LOCATION, "Location"),
    ]
    if include_result:
        prefixes.append((TOPIC_PREFIX_RESULT, "Res"))
    for prefix, kind in prefixes:
        if topic.startswith(prefix):
            identity = topic[len(prefix) :].split("/", 1)[0]
            return "CarStatus", kind, identity
    return None


@dataclass
class ServiceHubPushMessage:
    topic_group: str
    topic_type: str
    vehicle_id: str
    payload: dict[str, Any]
    is_status: bool = False


def parse_service_hub_message(
    topic: str, payload: bytes, *, include_result: bool = False
) -> ServiceHubPushMessage | None:
    classification = classify_service_hub_status_topic(
        topic, include_result=include_result
    )
    if classification is None or len(payload) > 2 * 1024 * 1024:
        return None
    try:
        data = json.loads(payload)
    except (ValueError, UnicodeDecodeError, RecursionError):
        return None
    if not isinstance(data, dict):
        return None
    group, kind, identity = classification
    return ServiceHubPushMessage(group, kind, identity, data, kind == "Status")


class ServiceHubProviderMixin:
    """Shared push contract; regional hooks retain each server's wire policy."""

    SERVICE_HUB_IDENTITY_FROM_METADATA = False
    SERVICE_HUB_METADATA_OPTIONAL = False

    if TYPE_CHECKING:
        _service_hub_session: requests.Session | None
        _service_hub_tid: str | None

        @property
        def _service_hub_brand(self) -> str: ...

        def _get_service_hub_url(self) -> str: ...

        def _get_service_hub_headers(self, token: Token) -> dict[str, str]: ...

    def _service_hub_get_url_with_params(
        self, path: str, params: dict[str, Any]
    ) -> str:
        url = self._get_service_hub_url() + path
        if not params:
            return url
        safe_chars = "-_.~!$'()*,;=:@/?"
        parts = [
            f"{quote(str(key), safe=safe_chars)}={quote(str(value), safe=safe_chars)}"
            for key, value in params.items()
        ]
        return f"{url}?{'&'.join(parts)}"

    def _service_hub_registered_client_id(self, token: Token) -> str:
        return token.mqtt_client_id or ""

    def _service_hub_registration_uuid(self, token: Token) -> str:
        return token.device_id or ""

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
        # Only fixed route names are safe; arbitrary paths may contain identities.
        endpoint = path if path in _SERVICE_HUB_STAGES else "<unknown>"
        stage = _SERVICE_HUB_STAGES.get(path, "unknown stage")
        safe_method = method if method in ("GET", "POST") else "OTHER"
        request_headers = self._get_service_hub_headers(token)
        if headers:
            request_headers.update(headers)
        if (
            path == "/api/v3/servicehub/device/host"
            and self._service_hub_session is None
        ):
            self._service_hub_session = requests.Session()
        url = self._service_hub_get_url_with_params(path, params or {})
        kwargs: dict[str, Any] = {"headers": request_headers, "timeout": (5, 30)}
        if json is not None:
            kwargs["json"] = json
        try:
            session = self._service_hub_session or requests
            response = (
                session.get(url, **kwargs)
                if method == "GET"
                else session.post(url, **kwargs)
            )
            if response.status_code != 200:
                _LOGGER.warning(
                    "%s - Service Hub %s %s (%s) failed: HTTP %s",
                    DOMAIN,
                    safe_method,
                    endpoint,
                    stage,
                    response.status_code,
                )
                return None
            if path == "/api/v3/servicehub/device/host":
                tid = response.headers.get("tid")
                if tid:
                    self._service_hub_tid = tid
            if (
                path == "/api/v3/servicehub/device/protocol"
                and not response.text.strip()
            ):
                return {}
            data: dict[str, Any] = response.json()
            if not isinstance(data, dict):
                _LOGGER.warning(
                    "%s - Service Hub %s %s (%s) failed: invalid response object",
                    DOMAIN,
                    safe_method,
                    endpoint,
                    stage,
                )
                return None
            return data
        except Exception as error:
            _LOGGER.error(
                "%s - Service Hub %s %s (%s) failed: %s",
                DOMAIN,
                safe_method,
                endpoint,
                stage,
                type(error).__name__[:64],
            )
            return None

    def _service_hub_registration_headers(
        self, token: Token, *, client: bool = False
    ) -> dict[str, str]:
        headers = {"tid": self._service_hub_tid or str(uuid.uuid4())}
        if client:
            headers["client-id"] = self._service_hub_registered_client_id(token)
        return headers

    def get_push_broker_info(self, token: Token) -> dict[str, Any] | None:
        data = self._service_hub_request(token, "GET", "/api/v3/servicehub/device/host")
        if data is None:
            return None
        return self._service_hub_broker_info(data)

    def _service_hub_broker_info(self, data: dict[str, Any]) -> dict[str, Any] | None:
        broker = data.get("mqtt", {})
        if not isinstance(broker, dict):
            return None
        host = broker.get("host")
        if not host:
            return None
        http = data.get("http", {})
        if not isinstance(http, dict):
            return None
        return {
            "mqtt_host": host,
            "mqtt_port": broker.get("port", 8883),
            "http_host": http.get("host"),
            "http_port": http.get("port"),
        }

    def register_push_client(self, token: Token) -> dict[str, Any] | None:
        data = self._service_hub_request(
            token,
            "POST",
            "/api/v3/servicehub/device/register",
            headers=self._service_hub_registration_headers(token),
            json={"unit": "mobile", "uuid": self._service_hub_registration_uuid(token)},
        )
        if data is None:
            return None
        client_id = self._service_hub_client_id(data)
        if not client_id:
            return None
        token.mqtt_client_id = client_id
        return {
            "client_id": client_id,
            "device_id": data.get("deviceId"),
            "username": data.get("username"),
            "password": data.get("password"),
        }

    def _service_hub_client_id(self, data: dict[str, Any]) -> str | None:
        value = data.get("clientId") or data.get("client_id")
        return value if isinstance(value, str) else None

    def _service_hub_protocols(self, vehicle: Vehicle) -> list[str]:
        return [MQTT_PROTOCOL_ID_VSS, MQTT_PROTOCOL_ID_CONNECTION, MQTT_PROTOCOL_ID_RES]

    def register_push_vehicle(
        self, token: Token, vehicle: Vehicle
    ) -> dict[str, Any] | None:
        return self._service_hub_request(
            token,
            "POST",
            "/api/v3/servicehub/device/protocol",
            headers=self._service_hub_registration_headers(token, client=True),
            json={
                "protocols": self._service_hub_protocols(vehicle),
                "protocolId": MQTT_PROTOCOL_ID_CCU_UPDATE,
                "carId": vehicle.id or "",
                "brand": self._service_hub_brand,
            },
        )

    def _service_hub_vehicle_params(self, vehicle: Vehicle) -> dict[str, Any]:
        return {"carId": vehicle.id or "", "brand": self._service_hub_brand}

    def _service_hub_get_metadata(
        self, token: Token, vehicle: Vehicle
    ) -> dict[str, Any] | None:
        if self.SERVICE_HUB_IDENTITY_FROM_METADATA:
            vehicle.mqtt_vehicle_id = None
        data = self._service_hub_request(
            token,
            "GET",
            "/api/v3/servicehub/vehicles/metadatalist",
            headers=self._service_hub_registration_headers(token, client=True),
            params=self._service_hub_vehicle_params(vehicle),
            allow_not_found=self.SERVICE_HUB_METADATA_OPTIONAL,
        )
        if data is not None:
            self._service_hub_apply_metadata(data, vehicle)
        return data

    def _service_hub_apply_metadata(
        self, data: dict[str, Any], vehicle: Vehicle
    ) -> None:
        """Regional metadata parsers may populate identity and capability fields."""

    def _service_hub_vehicle_id_request(self, vehicle: Vehicle) -> dict[str, Any]:
        return {"params": self._service_hub_vehicle_params(vehicle)}

    def _service_hub_get_vehicle_id(self, token: Token, vehicle: Vehicle) -> str | None:
        data = self._service_hub_request(
            token,
            "POST",
            "/api/v3/servicehub/vehicleId",
            headers=self._service_hub_registration_headers(token, client=True),
            **self._service_hub_vehicle_id_request(vehicle),
        )
        if data is None:
            return None
        vehicle_id = data.get("vehicleId") or data.get("carId")
        if vehicle_id:
            vehicle.mqtt_vehicle_id = str(vehicle_id)
            return str(vehicle_id)
        return None

    def get_push_vehicle_identity(
        self, token: Token, vehicle: Vehicle
    ) -> dict[str, Any]:
        self._service_hub_get_metadata(token, vehicle)
        if not self.SERVICE_HUB_IDENTITY_FROM_METADATA or not vehicle.mqtt_vehicle_id:
            self._service_hub_get_vehicle_id(token, vehicle)
            if self.SERVICE_HUB_IDENTITY_FROM_METADATA:
                self._service_hub_get_metadata(token, vehicle)
        self._service_hub_validate_vehicle_identity(vehicle)
        return {
            "mqtt_vehicle_id": vehicle.mqtt_vehicle_id,
            "hu_client_id": vehicle.hu_client_id,
        }

    def _service_hub_validate_vehicle_identity(self, vehicle: Vehicle) -> None:
        """Regions with a strict identity schema validate it before subscribing."""

    def get_push_connection_state(
        self, token: Token
    ) -> MQTT_PUSH_CONNECTION_STATE | None:
        data = self._service_hub_request(
            token,
            "GET",
            "/api/v3/vstatus/connstate",
            params={"clientId": self._service_hub_registered_client_id(token)},
        )
        if data is None:
            return None
        raw = data.get("connState") or data.get("state") or data.get("status")
        try:
            return MQTT_PUSH_CONNECTION_STATE(str(raw).upper())
        except ValueError:
            return None

    def get_push_credentials(self, token: Token) -> tuple[str, str] | None:
        return (
            self._service_hub_registered_client_id(token),
            (token.access_token or "").removeprefix("Bearer ").strip(),
        )

    def get_push_topics(self, token: Token, vehicle: Vehicle) -> list[str]:
        if not vehicle.mqtt_vehicle_id or not self._service_hub_registered_client_id(
            token
        ):
            return []
        return build_service_hub_status_topics(vehicle.mqtt_vehicle_id)
