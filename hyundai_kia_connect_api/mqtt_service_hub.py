"""mqtt_service_hub.py — MQTT Service Hub mixin for HyundaiCciApiEU.

Extracted from HyundaiCciApiEU.py for code organization. Contains the 5
Service Hub HTTP methods that configure and register the MQTT client.

These methods are mixed into HyundaiCciApiEU via MqttServiceHubMixin and
operate on the inheriting class's attributes/helpers (self._service_hub_*,
self._get_service_hub_url, self._get_service_hub_headers,
self._build_service_hub_url, self._service_hub_brand). No __init__ — the
mixin relies on the host class's state.
"""

# pylint:disable=invalid-name,missing-function-docstring,missing-class-docstring,broad-exception-caught,logging-fstring-interpolation

import json
import logging
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any
from urllib.parse import quote

import requests

from .const import (
    BRAND_GENESIS,
    BRAND_HYUNDAI,
    BRAND_KIA,
    BRANDS,
    DOMAIN,
)
from .GspaApiEU import USER_AGENT_OK_HTTP
from .Token import Token
from .Vehicle import Vehicle

_LOGGER = logging.getLogger(__name__)


class MqttServiceHubMixin:
    """Mixin providing MQTT Service Hub HTTP methods for HyundaiCciApiEU.

    This mixin is the provider implementation of the ApiImpl push
    contract for the CCI/EU lineage (both Hyundai and Kia brand classes
    mix it in); it also carries the shared Service Hub helpers
    (_service_hub_brand, _get_service_hub_headers, _build_service_hub_url,
    _get_service_hub_url) so both brand subclasses get them unchanged.
    The host class provides only:
      - self._service_hub_session  (requests.Session | None)
      - self._service_hub_tid      (str | None)
      - self.staging               (bool config flag)
      - CCSP_CLIENT_SERVICE_ID / PUSH_PROVIDER_ID   (per-brand constants)
    """

    # ------------------------------------------------------------------
    # MQTT Service Hub (api/v3/servicehub/*)
    # ------------------------------------------------------------------

    @property
    def _service_hub_brand(self) -> str:
        """Short brand code for Service Hub API (H, K, or G).

        Service Hub endpoints use single-letter brand codes in the body,
        not the full brand names used in HTTP headers.
        """
        if BRANDS[self.brand] == BRAND_KIA:
            return "K"
        if BRANDS[self.brand] == BRAND_GENESIS:
            return "G"
        return "H"

    def _get_service_hub_headers(self, token: Token) -> dict:
        """Headers for Service Hub (api/v3/servicehub/*).

        Service Hub uses the CCS SDK interceptor chain, NOT the CCI API
        headers. Based on the CCS interceptor chain (h.java, j.java,
        d.java): Authorization carries the CCS token, plus client-os-code,
        locale, Accept-Language, X-Application-Id (pushProviderId = FCM),
        EpitVersion, X-Service-Id (ccspServiceId) and a per-request UUID
        in X-Request-Id. exchangeable-token / non-ccs-token are attached
        when the token carries them.
        """
        ccs_access = token.ccs_token or token.access_token or ""
        ccs_access = ccs_access.removeprefix("Bearer ").strip()
        headers = {
            "Authorization": f"Bearer {ccs_access}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT_OK_HTTP,
            # CciAuthenticationHeaderInterceptor (h.java)
            "client-os-code": "AOS",
            "locale": self.LANGUAGE,
            # DefaultHeaderInterceptor (j.java)
            "Accept-Language": self.LANGUAGE,
            "X-Application-Id": self.PUSH_PROVIDER_ID,
            "EpitVersion": "EPITV2",
            "X-Service-Id": self.CCSP_CLIENT_SERVICE_ID,
            # X-Request-Id: per-request UUID (TSID)
            "X-Request-Id": str(uuid.uuid4()),
        }
        # CciAuthenticationHeaderInterceptor — exchangeable-token
        exchangeable = getattr(token, "exchangeable_token", None) or ""
        if exchangeable:
            headers["exchangeable-token"] = exchangeable
        # CciAuthenticationHeaderInterceptor — non-ccs-token (optional)
        non_ccs = getattr(token, "non_ccs_token", None) or ""
        if non_ccs:
            headers["non-ccs-token"] = non_ccs
        return headers

    @staticmethod
    def _build_service_hub_url(base_url: str, params: dict) -> str:
        """Build URL with query params matching OkHttp 3.12.0 encoding.

        OkHttp does NOT encode '@' in query parameter values, but Python's
        urllib3 (used by requests) encodes '@' as '%40'. The Service Hub
        server validates the tid session exactly as sent, so '%40' vs '@'
        causes "Invalid Parameter (client-id)" on device/protocol.

        This method uses quote() with safe='@' to match OkHttp's behavior.
        """
        if not params:
            return base_url
        # OkHttp 3.12.0 QUERY_COMPONENT encode set does NOT include @.
        # We add a generous safe set to match: unreserved chars + @ + sub-delims
        safe_chars = "-_.~!$'()*,;=:@/?"
        parts = []
        for k, v in params.items():
            encoded_key = quote(str(k), safe=safe_chars)
            encoded_val = quote(str(v), safe=safe_chars)
            parts.append(f"{encoded_key}={encoded_val}")
        return f"{base_url}?{'&'.join(parts)}"

    def _get_service_hub_url(self) -> str:
        """Get Service Hub base URL for the current brand/region.

        Production URLs from the official EU app (v1.1.4).
        Staging URLs are in plaintext config.
        """
        # Map brand + region to Service Hub key
        # Key format: {H|K|G}_{region_code}
        brand_prefix = {
            BRAND_HYUNDAI: "H",
            BRAND_KIA: "K",
            BRAND_GENESIS: "G",
        }.get(self.brand, "H")

        # Region mapping (EU is default for this API class)
        # The CCI EU API only supports EU region, but we provide
        # the full mapping for future region subclasses
        region_suffix = "EU"  # HyundaiCciApiEU is EU-only

        service_hub_key = f"{brand_prefix}_{region_suffix}"

        from .mqtt_service_hub import (
            SERVICE_HUB_PRODUCTION_BASES,
            SERVICE_HUB_STAGING_BASES,
        )

        if self.staging:
            bases = SERVICE_HUB_STAGING_BASES
        else:
            bases = SERVICE_HUB_PRODUCTION_BASES

        host_port = bases.get(service_hub_key)
        if not host_port:
            _LOGGER.error(f"{DOMAIN} - No Service Hub URL for key={service_hub_key}")
            return ""

        return f"https://{host_port}"

    def get_push_broker_info(self, token: Token) -> dict | None:
        """GET api/v3/servicehub/device/host — get MQTT broker config.

        Returns dict with keys: http_host, http_port, mqtt_host, mqtt_port, ssl.
        Stores broker info on the token for later MQTT connection.

        The app: no extra @Header params, uses CCS SDK interceptor chain for auth.
        Uses requests.Session to maintain cookies/connection state like OkHttp.
        """
        url = self._get_service_hub_url()
        if not url:
            return None
        url += "/api/v3/servicehub/device/host"
        headers = self._get_service_hub_headers(token)

        # Create session for all Service Hub calls — OkHttp uses a
        # ConnectionPool and the server may track HTTP session state
        # (cookies) between device/host → register → protocol calls.
        if self._service_hub_session is None:
            self._service_hub_session = requests.Session()

        try:
            response = self._service_hub_session.get(
                url, headers=headers, timeout=(5, 30)
            )
            if response.status_code == 200:
                # Log all response headers + cookies to debug tid/session
                _LOGGER.debug(
                    f"{DOMAIN} - MQTT device/host response headers: "
                    f"{dict(response.headers)}"
                )
                _LOGGER.debug(
                    f"{DOMAIN} - MQTT device/host response cookies: "
                    f"{dict(self._service_hub_session.cookies)}"
                )

                # Extract session tid from response headers — used for ALL
                # subsequent Service Hub requests (device/register, metadatalist,
                # vehicleId, device/protocol). The app stores this from
                # the "tid" response header after device/host responds.
                tid = response.headers.get("tid")
                if tid:
                    self._service_hub_tid = tid
                    _LOGGER.debug(
                        f"{DOMAIN} - MQTT device/host tid from headers: {tid}"
                    )
                else:
                    _LOGGER.warning(
                        f"{DOMAIN} - MQTT device/host: no tid header in response"
                    )

                data = response.json()
                _LOGGER.debug(f"{DOMAIN} - MQTT device/host response: {data}")
                mqtt_broker = data.get("mqtt", {})
                host = mqtt_broker.get("host")
                port = mqtt_broker.get("port", 8883)
                ssl_config = mqtt_broker.get("ssl")
                use_ssl = ssl_config is not None

                token.mqtt_broker_host = host
                token.mqtt_broker_port = port

                _LOGGER.info(f"{DOMAIN} - MQTT broker: {host}:{port} (SSL={use_ssl})")
                return {
                    "mqtt_host": host,
                    "mqtt_port": port,
                    "use_ssl": use_ssl,
                    "http_host": data.get("http", {}).get("host"),
                    "http_port": data.get("http", {}).get("port"),
                }
            _LOGGER.warning(
                f"{DOMAIN} - Service Hub device/host failed: "
                f"HTTP {response.status_code} — {response.text[:300]}"
            )
        except Exception as ex:
            _LOGGER.error(f"{DOMAIN} - Service Hub device/host error: {ex}")
        return None

    def register_push_client(self, token: Token) -> dict | None:
        """POST api/v3/servicehub/device/register — register device for MQTT push.

        The app sends: @Header("tid") + @Body {unit: "mobile", uuid: ccId}.
        Returns clientId and deviceId.

        The 'unit' field is the fixed string "mobile".
        The 'uuid' field is the CCSP device enrollment ID (ccId).

        CRITICAL: tid is an HTTP header (@Header), NOT a URL query param.
        """
        url = self._get_service_hub_url()
        if not url:
            return None
        url += "/api/v3/servicehub/device/register"
        headers = self._get_service_hub_headers(token)
        # tid is @Header("tid") in Retrofit interface, not @Query
        headers["tid"] = self._service_hub_tid or str(uuid.uuid4())

        body = {
            "unit": "mobile",
            "uuid": token.client_device_id or token.device_id or "",
        }
        try:
            _LOGGER.debug(
                f"{DOMAIN} - MQTT device/register REQUEST: "
                f"tid={headers['tid']}, uuid={body.get('uuid', '')[:8]}..."
            )
            session = self._service_hub_session or requests
            response = session.post(url, json=body, headers=headers, timeout=(5, 30))
            # Log response headers to check for session info
            _LOGGER.debug(
                f"{DOMAIN} - MQTT device/register response headers: "
                f"{dict(response.headers)}"
            )
            if response.status_code == 200:
                data = response.json()
                _LOGGER.debug(
                    f"{DOMAIN} - MQTT device/register response keys: "
                    f"{list(data.keys())} — full: {data}"
                )
                client_id = data.get("clientId") or data.get("client_id")
                username = data.get("username")
                password = data.get("password")
                device_id = data.get("deviceId")
                token.mqtt_client_id = client_id

                _LOGGER.info(
                    f"{DOMAIN} - MQTT client registered: {client_id}, "
                    f"username={'present' if username else 'absent'}, "
                    f"password={'present' if password else 'absent'}"
                )
                return {
                    "client_id": client_id,
                    "device_id": device_id,
                    "username": username,
                    "password": password,
                }
            _LOGGER.warning(
                f"{DOMAIN} - Service Hub device/register failed: "
                f"HTTP {response.status_code} — {response.text[:500]}"
            )
        except Exception as ex:
            _LOGGER.error(f"{DOMAIN} - Service Hub device/register error: {ex}")
        return None

    def register_push_vehicle(self, token: Token, vehicle: Vehicle) -> dict | None:
        """POST api/v3/servicehub/device/protocol — register protocol for vehicle.

        App Retrofit interface: @Header("tid") + @Header("client-id") +
        @Body MQTTRegisterProtocolApiRequest(protocols, protocolId, carId, brand).

        The body has exactly 4 fields — no mqttProtocol or ccuCCS2ProtocolSupport.
        Those fields were wrong guesses; the actual DTO uses carId and brand instead.

        CRITICAL: tid and client-id are HTTP headers (@Header), NOT URL query
        params. The Retrofit interface declares tid and client-id via
        @Header.
        Sending them as query params causes HTTP 400 "Invalid Parameter (client-id)".

        Note: CCSHeaderInterceptor is NOT in the MQTT OkHttp interceptor chain,
        so X-MQTT-Client-Id, X-MQTT-Vehicle-Id, X-Ccu-Ccs2-Protocol-Support
        are NOT added by interceptors. The server may not require them, but we
        send them for compatibility.

        The protocols list always starts with VSS, CONNECTION, RES, then
        conditionally adds RC and OTA protocols.
        """
        url = self._get_service_hub_url()
        if not url:
            return None
        url += "/api/v3/servicehub/device/protocol"
        headers = self._get_service_hub_headers(token)
        client_id = token.mqtt_client_id or token.client_device_id or ""

        # tid and client-id are @Header in the Retrofit interface, NOT @Query
        headers["tid"] = self._service_hub_tid or str(uuid.uuid4())
        headers["client-id"] = client_id

        # Add MQTT-specific headers (not from interceptors — CCSHeaderInterceptor
        # is NOT in the MQTT OkHttp client chain, but we send for compatibility)
        headers["X-Ccu-Ccs2-Protocol-Support"] = str(
            vehicle.ccu_ccs2_protocol_support or 0
        )
        headers["X-MQTT-Client-Id"] = client_id
        # X-MQTT-Vehicle-Id uses the MQTT vehicle ID from the vehicleId
        # endpoint, NOT the regular vehicle.id. The vehicleId endpoint
        # response supplies this ID.
        headers["X-MQTT-Vehicle-Id"] = vehicle.mqtt_vehicle_id or vehicle.id or ""

        from .mqtt_service_hub import (
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
            MQTT_PROTOCOL_ID_VSS,
        )

        # Base protocols — always included
        protocols = [
            MQTT_PROTOCOL_ID_VSS,
            MQTT_PROTOCOL_ID_CONNECTION,
            MQTT_PROTOCOL_ID_RES,
        ]
        # RC protocols — the app includes BOTH device.* AND vehicle.* variants
        # (the app adds both groups when remoteControllerOption == 1)
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
        # OTA protocols — conditionally added based on vehicle capabilities
        if getattr(vehicle, "ecu_remote_ota_update_support", None) == 1:
            protocols.append("vehicle.ota.otaprogress")
        if getattr(vehicle, "ecu_reservation_ota_update_support", None) == 1:
            protocols.append("vehicle.ota.scheduleupdate")

        # Body from MQTTRegisterProtocolApiRequest DTO (4 fields only):
        # protocols, protocolId, carId, brand
        body = {
            "protocols": protocols,
            "protocolId": MQTT_PROTOCOL_ID_CCU_UPDATE,
            "carId": vehicle.id or "",
            "brand": self._service_hub_brand,
        }
        _LOGGER.debug(
            f"{DOMAIN} - MQTT device/protocol REQUEST: "
            f"url={url}, body={json.dumps(body, default=str)}"
        )
        try:
            session = self._service_hub_session or requests
            response = session.post(url, json=body, headers=headers, timeout=(5, 30))
            # Debug 400: log CCS-specific headers + cookies
            if response.status_code == 400:
                sess_cookies = (
                    dict(self._service_hub_session.cookies)
                    if self._service_hub_session
                    else {}
                )
                _LOGGER.warning(
                    f"{DOMAIN} - MQTT device/protocol 400 debug: "
                    f"content_type={headers.get('Content-Type')}, "
                    f"body_len={len(json.dumps(body))}, "
                    f"ccs2={headers.get('X-Ccu-Ccs2-Protocol-Support')}, "
                    f"mqtt_client={headers.get('X-MQTT-Client-Id')}, "
                    f"mqtt_vehicle={headers.get('X-MQTT-Vehicle-Id')}, "
                    f"cookies={sess_cookies}"
                )
            _LOGGER.debug(
                f"{DOMAIN} - MQTT device/protocol RESPONSE: "
                f"status={response.status_code}, body={response.text[:500]}"
            )
            if response.status_code == 200:
                # device/protocol returns empty body (content-length: 0).
                # The app's empty-body interceptor handles this. Just return success.
                try:
                    data = response.json() if response.text.strip() else {}
                except (ValueError, json.JSONDecodeError):
                    data = {}
                _LOGGER.info(
                    f"{DOMAIN} - MQTT protocol registered OK for vehicle {vehicle.id}: {data}"
                )
                return data
            _LOGGER.warning(
                f"{DOMAIN} - Service Hub device/protocol failed: "
                f"HTTP {response.status_code} — {response.text[:300]}"
            )
        except Exception as ex:
            _LOGGER.error(f"{DOMAIN} - Service Hub device/protocol error: {ex}")
        return None

    def _service_hub_get_metadata(
        self, token: Token, vehicle: Vehicle
    ) -> dict[str, Any] | None:
        """GET api/v3/servicehub/vehicles/metadatalist — vehicle MQTT metadata.

        The app sends: @Header("tid") + @Header("client-id") +
                   @Query("carId") + @Query("brand").
        Returns MQTT cache response with vehicle capabilities and IDs.

        CRITICAL: tid and client-id are @Header, not @Query.
        """
        url = self._get_service_hub_url()
        if not url:
            return None
        url += "/api/v3/servicehub/vehicles/metadatalist"
        headers = self._get_service_hub_headers(token)
        client_id = token.mqtt_client_id or token.client_device_id or ""

        # tid and client-id are @Header in the Retrofit interface
        headers["tid"] = self._service_hub_tid or str(uuid.uuid4())
        headers["client-id"] = client_id
        headers["X-MQTT-Client-Id"] = client_id
        headers["X-MQTT-Vehicle-Id"] = vehicle.mqtt_vehicle_id or vehicle.id or ""
        headers["X-Ccu-Ccs2-Protocol-Support"] = str(
            vehicle.ccu_ccs2_protocol_support or 0
        )

        # carId and brand are @Query params (not headers)
        params = {
            "carId": vehicle.id or "",
            "brand": self._service_hub_brand,
        }
        try:
            full_url = self._build_service_hub_url(url, params)
            session = self._service_hub_session or requests
            response = session.get(full_url, headers=headers, timeout=(5, 30))
            if response.status_code == 200:
                data: dict[str, Any] = response.json()
                _LOGGER.debug(f"{DOMAIN} - MQTT metadatalist FULL response: {data}")
                # typed: response.json() is Any; the shape is a metadata object
                # Extract hu clientId from metadatalist for CarRemote topics.
                # The app distinguishes ccu clientId vs hu clientId here
                vehicles_list = data.get("vehicles", [])
                for v in vehicles_list:
                    unit = v.get("unit", "")
                    client_id_val = v.get("clientId", "")
                    if unit == "hu" and client_id_val:
                        vehicle.hu_client_id = client_id_val
                        _LOGGER.info(
                            f"{DOMAIN} - MQTT hu_client_id from metadatalist: "
                            f"{client_id_val}"
                        )
                return data
            _LOGGER.warning(
                f"{DOMAIN} - Service Hub metadatalist failed: "
                f"HTTP {response.status_code} — {response.text[:300]}"
            )
        except Exception as ex:
            _LOGGER.error(f"{DOMAIN} - Service Hub metadatalist error: {ex}")
        return None

    def _service_hub_get_vehicle_id(self, token: Token, vehicle: Vehicle) -> str | None:
        """POST api/v3/servicehub/vehicleId — get vehicle MQTT ID.

        The app sends: @Header("tid") + @Header("client-id") +
                   @Query("carId") + @Query("brand").
        Returns the MQTT vehicle ID used as infix in CarStatus topics.

        CRITICAL: tid and client-id are @Header, not @Query.
        carId and brand are @Query params, NOT @Body fields.
        """
        url = self._get_service_hub_url()
        if not url:
            return None
        url += "/api/v3/servicehub/vehicleId"
        headers = self._get_service_hub_headers(token)
        client_id = token.mqtt_client_id or token.client_device_id or ""

        # tid and client-id are @Header in the Retrofit interface
        headers["tid"] = self._service_hub_tid or str(uuid.uuid4())
        headers["client-id"] = client_id
        headers["X-MQTT-Client-Id"] = client_id
        # Note: mqtt_vehicle_id may be empty on first call — that's OK
        headers["X-MQTT-Vehicle-Id"] = vehicle.mqtt_vehicle_id or vehicle.id or ""
        headers["X-Ccu-Ccs2-Protocol-Support"] = str(
            vehicle.ccu_ccs2_protocol_support or 0
        )

        # carId and brand are @Query params, not @Body
        params = {
            "carId": vehicle.id or "",
            "brand": self._service_hub_brand,
        }

        try:
            full_url = self._build_service_hub_url(url, params)
            session = self._service_hub_session or requests
            response = session.post(full_url, headers=headers, timeout=(5, 30))
            if response.status_code == 200:
                data: dict[str, Any] = response.json()
                vehicle_id = data.get("vehicleId") or data.get("carId")
                if vehicle_id:
                    vehicle.mqtt_vehicle_id = vehicle_id
                    _LOGGER.info(f"{DOMAIN} - MQTT vehicle ID: {vehicle_id}")
                if vehicle_id:
                    return str(vehicle_id)
                return None
            _LOGGER.warning(
                f"{DOMAIN} - Service Hub vehicleId failed: "
                f"HTTP {response.status_code} — {response.text[:300]}"
            )
        except Exception as ex:
            _LOGGER.error(f"{DOMAIN} - Service Hub vehicleId error: {ex}")
        return None

    def get_push_connection_state(self, token: Token) -> str | None:
        """GET api/v3/vstatus/connstate — check the push connection state.

        The app sends: @Query("clientId") — no tid or client-id headers.
        Returns: "ONLINE", "OFFLINE", or "UNKNOWN".
        """
        base = self._get_service_hub_url()
        if not base:
            return None
        url = base + "/api/v3/vstatus/connstate"
        headers = self._get_service_hub_headers(token)

        params = {
            "clientId": token.mqtt_client_id or token.client_device_id or "",
        }
        try:
            full_url = self._build_service_hub_url(url, params)
            response = requests.get(full_url, headers=headers, timeout=(5, 30))
            if response.status_code == 200:
                data: dict[str, Any] = response.json()
                state = (
                    data.get("connState")
                    or data.get("state")
                    or data.get("status")
                    or "UNKNOWN"
                )
                _LOGGER.debug(f"{DOMAIN} - Push connection state: {state}")
                return state.upper()
            _LOGGER.warning(
                f"{DOMAIN} - Service Hub connstate failed: "
                f"HTTP {response.status_code} — {response.text[:300]}"
            )
        except Exception as ex:
            _LOGGER.error(f"{DOMAIN} - Service Hub connstate error: {ex}")
        return None

    def get_push_vehicle_identity(
        self, token: Token, vehicle: Vehicle
    ) -> dict[str, Any] | None:
        """Get the vehicle push identity: metadata + MQTT vehicle id.

        Combines the two Service Hub reads (metadata/vehicleId endpoint).
        Side effects: populates vehicle.hu_client_id (from the metadata
        list) and vehicle.mqtt_vehicle_id (from the vehicleId endpoint).
        """
        self._service_hub_get_metadata(token, vehicle)
        self._service_hub_get_vehicle_id(token, vehicle)
        return {
            "mqtt_vehicle_id": vehicle.mqtt_vehicle_id,
            "hu_client_id": vehicle.hu_client_id,
        }

    def parse_push_message(self, topic: str, payload: bytes) -> "CciPushMessage | None":
        """Parse a raw Service Hub MQTT delivery (push-contract method).

        Transport delivers raw (topic, payload bytes); JSON decoding and
        the envelope's raw-hex fallback belong to this schema, not to the
        transport. Returns None for unknown topic schemas.
        """
        msg = parse_mqtt_message(topic, payload)
        if msg.topic_group == "Unknown":
            return None
        out = CciPushMessage(
            topic_group=msg.topic_group,
            topic_type=msg.topic_type,
            vehicle_id=msg.vehicle_id,
            payload=msg.payload,
            is_status=msg.topic_group == "CarStatus",
        )
        header = out.payload.get("header", {})
        body = out.payload.get("body", {})
        tid = header.get("tid") or header.get("tId")
        if out.topic_type == "Res":
            out.action_id = tid
            res_code = body.get("resCode", header.get("resCode"))
            out.action_result = str(res_code) if res_code is not None else "success"
        elif out.topic_type == "Connect":
            out.action_id = tid
            out.action_connected = True
            out.action_result = "connected"
        return out

    def get_push_topics(self, token: Token, vehicle: Vehicle) -> list[str]:
        """Topics to subscribe for a vehicle (Service Hub schema).

        Excluded on purpose (broker rejects with rc=128):
          - service/phone/_/res/{id} (CarStatus.Res)
          - device/{id}/closeremote/* (DeviceCloseRemote)
          QoS 0 only — the broker rejects QoS 1 SUBSCRIBEs outright.
        DeviceRemote/CarRemote use the device/client id as the infix;
        CarStatus/OTA use the MQTT vehicle id.
        """
        client_id = token.mqtt_client_id or token.client_device_id or ""
        vid = vehicle.mqtt_vehicle_id or vehicle.id or ""
        caps = MqttCacheCapabilities(
            vehicle_id=vid,
            client_id=client_id,
        )
        topics: list[str] = []
        topics.extend(build_car_status_topics(vid, caps))
        topics.extend(build_device_remote_topics(client_id))
        # CarRemote / CarCloseRemote / DeviceCloseRemote excluded (broker
        # rejects vehicle/{huClientId}/... and closeremote/* with rc=128)
        topics.extend(build_ota_topics(vid, caps))
        return topics


# ---------------------------------------------------------------------------
# Service Hub MQTT topic schema (wire-format: topic constants + enums
# + protocol IDs + message data classes + builders + parser).
# These belong to the EU/CCI Service Hub wire format, NOT to the generic
# MQTT transport, which stays schema-neutral (mqtt_client.py).
# ---------------------------------------------------------------------------

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
    payload: dict[str, Any]  # Parsed JSON (header + body)


@dataclass
class CciPushMessage:
    """Parsed Service Hub MQTT message — the push-message contract shape."""

    topic_group: str  # "CarStatus", "DeviceRemote", "CarRemote", ...
    topic_type: str  # "Status", "Connect", "Res", ...
    vehicle_id: str  # push-level vehicle id (topic infix)
    payload: dict[str, Any]  # parsed JSON
    is_status: bool = False
    action_id: str | None = None
    action_result: str | None = None
    action_connected: bool = False


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
