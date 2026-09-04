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

import requests

from .const import DOMAIN
from .Token import Token
from .Vehicle import Vehicle

_LOGGER = logging.getLogger(__name__)


class MqttServiceHubMixin:
    """Mixin providing MQTT Service Hub HTTP methods for HyundaiCciApiEU.

    Inherits nothing; expects the host class to provide:
      - self._service_hub_session  (requests.Session | None)
      - self._service_hub_tid      (str | None)
      - self._service_hub_brand    (property -> str)
      - self._get_service_hub_url() -> str | None
      - self._get_service_hub_headers(token) -> dict
      - self._build_service_hub_url(base_url, params) -> str
    """

    def get_mqtt_host(self, token: Token) -> dict | None:
        """GET api/v3/servicehub/device/host — get MQTT broker config.

        Returns dict with keys: http_host, http_port, mqtt_host, mqtt_port, ssl.
        Stores broker info on the token for later MQTT connection.

        No extra @Header params; auth via the CCS SDK HTTP client
        header chain (app-confirmed).
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
                # vehicleId, device/protocol). The device id comes from
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

    def register_mqtt_client(self, token: Token) -> dict | None:
        """POST api/v3/servicehub/device/register — register device for MQTT push.

        Wire shape (app-confirmed): @Header("tid") + @Body {unit: "mobile", uuid: ccId}.
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

    def register_mqtt_protocol(self, token: Token, vehicle: Vehicle) -> dict | None:
        """POST api/v3/servicehub/device/protocol — register protocol for vehicle.

        App Retrofit interface: @Header("tid") + @Header("client-id") +
        @Body MQTTRegisterProtocolApiRequest(protocols, protocolId, carId, brand).

        The body has exactly 4 fields — no mqttProtocol or ccuCCS2ProtocolSupport.
        Those fields were wrong guesses; the actual DTO uses carId and brand instead.

        CRITICAL: tid and client-id are HTTP headers (@Header), NOT URL query
        params. The Retrofit interface declares tid and client-id via
        @Header.
        Sending them as query params causes HTTP 400 "Invalid Parameter (client-id)".

        Note: the CCI header interceptor is not part of the MQTT HTTP
        client chain (app-confirmed),
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
        headers["X-MQTT-Vehicle-Id"] = vehicle.mqtt_client_id or vehicle.id or ""

        from .mqtt_client import (
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
        # RC protocols — BOTH device.* AND vehicle.* variants are
        # registered (app-confirmed when remoteControllerOption == 1)
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
                # An empty body is treated as success (app-confirmed).
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

    def get_mqtt_metadata(self, token: Token, vehicle: Vehicle) -> dict | None:
        """GET api/v3/servicehub/vehicles/metadatalist — vehicle MQTT metadata.

        Wire shape (app-confirmed): @Header("tid") + @Header("client-id") +
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
        headers["X-MQTT-Vehicle-Id"] = vehicle.mqtt_client_id or vehicle.id or ""
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
                data = response.json()
                _LOGGER.debug(f"{DOMAIN} - MQTT metadatalist FULL response: {data}")
                # Extract hu clientId from metadatalist for CarRemote topics.
                # ccu vs hu clientId are distinguished here (app-confirmed)
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

    def get_mqtt_vehicle_id(self, token: Token, vehicle: Vehicle) -> str | None:
        """POST api/v3/servicehub/vehicleId — get vehicle MQTT ID.

        Wire shape (app-confirmed): @Header("tid") + @Header("client-id") +
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
        headers["X-MQTT-Vehicle-Id"] = vehicle.mqtt_client_id or vehicle.id or ""
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
                data = response.json()
                vehicle_id = data.get("vehicleId") or data.get("carId")
                if vehicle_id:
                    vehicle.mqtt_client_id = vehicle_id
                    _LOGGER.info(f"{DOMAIN} - MQTT vehicle ID: {vehicle_id}")
                return vehicle_id
            _LOGGER.warning(
                f"{DOMAIN} - Service Hub vehicleId failed: "
                f"HTTP {response.status_code} — {response.text[:300]}"
            )
        except Exception as ex:
            _LOGGER.error(f"{DOMAIN} - Service Hub vehicleId error: {ex}")
        return None
