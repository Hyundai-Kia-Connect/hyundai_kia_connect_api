"""HyundaiCciApiEU.py — Hyundai EU CCI/GSPA API.

Hyundai-specific EU implementation inheriting the OneApp (CCI) login flow,
the GSPA secure-request layer, and the shared GSPA remote-control layer
from ``GspaApiEU``. This module keeps Hyundai brand constants and
vehicle-read parsers (stored-status, driving info/history, breakdowns,
CCS2 status). OTA and MQTT are handled in later PRs.
"""

# pylint:disable=missing-class-docstring,missing-function-docstring,invalid-name,logging-fstring-interpolation,broad-except,too-many-lines

import datetime as dt
import logging
import uuid
from typing import Any
from urllib.parse import quote

from .const import (
    BRAND_GENESIS,
    BRAND_HYUNDAI,
    BRAND_KIA,
    BRANDS,
    DISTANCE_UNITS,
    DOMAIN,
    ENGINE_TYPES,
)
from .exceptions import APIError
from .GspaApiEU import USER_AGENT_OK_HTTP, GspaApiEU
from .mqtt_service_hub import MqttServiceHubMixin
from .Token import Token
from .utils import (
    get_child_value,
)
from .Vehicle import DailyDrivingStats, Vehicle

_LOGGER = logging.getLogger(__name__)


class HyundaiCciApiEU(GspaApiEU, MqttServiceHubMixin):
    """Hyundai EU CCI/GSPA API.

    Uses the CCI login flow (OneApp client_id 4f4953b5) confirmed on
    production endpoints. Login, token lifecycle, the GSPA
    secure-request layer, and the GSPA remote-control layer are all
    inherited from ``GspaApiEU``. This subclass carries the brand
    constants and the Hyundai-specific read layer: CCS2 vehicle-property
    parsing, driving info/history, and breakdowns. MQTT Service Hub
    registration and the receive-only MQTT client are provided by
    ``MqttServiceHubMixin`` and ``mqtt_client.py``.
    """

    # Brand constants (Hyundai OneApp EU, confirmed on production endpoints).
    ONEAPP_CLIENT_ID = "4f4953b5-02e1-4dbc-8599-87e983ee1be5"
    ONEAPP_REDIRECT_URI = "https://oneapp.hyundai.com/redirect"
    CCI_API_URL = "https://cci-api-eu.hyundai.com"
    CCI_PACKAGE_ID = "com.hyundai.oneapp.eu"
    GSPA_BASE_URL = "https://gspa-ccs-eu.hyundai.com/"
    LOGIN_FORM_HOST = "https://idpconnect-eu.hyundai.com"
    CIPHER_BRAND = "hyundai"
    REQUEST_ID_HEADER = "X-Request-Id"
    DEVICE_ID_HEADER = "X-Device-Id"

    # SVM reads confirmed live on the EU GSPA endpoints (na-images);
    # KiaCciApiEU keeps the inherited False until verified there too.
    supports_svm: bool = True

    # ccspServiceId — the service identifier in the X-Service-Id header
    # (Service Hub). Distinct from CCSP_SERVICE_ID (OAuth client_id).
    CCSP_CLIENT_SERVICE_ID = "4f4953b5-02e1-4dbc-8599-87e983ee1be5"
    # pushProviderId — X-Application-Id header value.
    PUSH_PROVIDER_ID = "33258e5f-b8c8-459e-add1-1e94217ba148"

    # Service Hub staging flag (class attr — production endpoints only
    # for now; staging bases live in mqtt_client.SERVICE_HUB_STAGING_BASES).
    staging: bool = False

    # CCS2 EU vehicles support GSPA window control.
    supports_window_control: bool = True

    # Hyundai EU CCI remote control is live-verified.
    GSPA_REMOTE_CONTROL_VERIFIED = True

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

        from .mqtt_client import SERVICE_HUB_PRODUCTION_BASES, SERVICE_HUB_STAGING_BASES

        if self.staging:
            bases = SERVICE_HUB_STAGING_BASES
        else:
            bases = SERVICE_HUB_PRODUCTION_BASES

        host_port = bases.get(service_hub_key)
        if not host_port:
            _LOGGER.error(f"{DOMAIN} - No Service Hub URL for key={service_hub_key}")
            return ""

        return f"https://{host_port}"

    # ------------------------------------------------------------------
    # Driving info + history (GSPA, read-only)
    # ------------------------------------------------------------------

    def _get_driving_info(
        self, token: Token, vehicle: Vehicle
    ) -> dict[str, Any] | None:
        """Fetch driving info from GSPA driving-info endpoint."""
        self._validate_ccs_token(token)
        try:
            return self._gspa_get(token, vehicle, "driving-info/vehicles/{carId}")
        except Exception:
            _LOGGER.debug(f"{DOMAIN} - GSPA driving-info failed")
            return None

    def _update_vehicle_drive_info(
        self, vehicle: Vehicle, state: dict[str, Any]
    ) -> None:
        if isinstance(state, dict):
            driving_info = state.get("drivingInfo", state)
            if driving_info is None:
                return
            if isinstance(driving_info, list) and len(driving_info) > 0:
                driving_info = driving_info[0]
            vehicle.total_driving_range = (
                driving_info.get("totalDistance"),
                DISTANCE_UNITS.get(1, "km"),
            )
            total_consumed = driving_info.get("totalPwrCsp")
            if total_consumed is not None:
                vehicle.total_power_consumed = float(total_consumed)
            total_regen = driving_info.get("regenPwr")
            if total_regen is not None:
                vehicle.total_power_regenerated = float(total_regen)

    def _get_driving_history(
        self, token: Token, vehicle: Vehicle
    ) -> dict[str, Any] | None:
        """Fetch 30-day driving history from GSPA driving-history endpoint."""
        self._validate_ccs_token(token)
        try:
            return self._gspa_get(token, vehicle, "driving-history/vehicles/{carId}")
        except Exception:
            _LOGGER.debug(f"{DOMAIN} - GSPA driving-history failed")
            return None

    def _update_vehicle_driving_history(
        self, vehicle: Vehicle, state: dict[str, Any]
    ) -> None:
        """Parse 30-day driving history into power_consumption_30d and daily_stats."""
        # Filter for the summary period (drivingPeriod == 0) which contains
        # total power consumption and calculative odometer.
        driving_info_list = state.get("drivingInfo", [])
        if not driving_info_list:
            return

        for item in driving_info_list:
            if not isinstance(item, dict):
                continue
            if item.get("drivingPeriod") != 0:
                continue
            total_pwr = item.get("totalPwrCsp")
            odo = next(
                (v for k, v in item.items() if k.lower() == "calculativeodo"),
                0,
            )
            if total_pwr and odo and odo > 0:
                vehicle.power_consumption_30d = round(total_pwr / odo)
                break

        detail_list = state.get("drivingInfoDetail", [])
        if detail_list:
            daily_stats = []
            for day in detail_list:
                if not isinstance(day, dict):
                    continue
                try:
                    processed = DailyDrivingStats(
                        date=dt.datetime.strptime(day["drivingDate"], "%Y%m%d").replace(
                            tzinfo=self.data_timezone
                        ),
                        total_consumed=get_child_value(day, "totalPwrCsp"),
                        engine_consumption=get_child_value(day, "motorPwrCsp"),
                        climate_consumption=get_child_value(day, "climatePwrCsp"),
                        onboard_electronics_consumption=get_child_value(
                            day, "eDPwrCsp"
                        ),
                        battery_care_consumption=get_child_value(
                            day, "batteryMgPwrCsp"
                        ),
                        regenerated_energy=get_child_value(day, "regenPwr"),
                        distance=get_child_value(day, "calculativeOdo"),
                        distance_unit=vehicle.odometer_unit,
                    )
                    daily_stats.append(processed)
                except (KeyError, ValueError):
                    continue
            if daily_stats:
                vehicle.daily_stats = daily_stats

    # ------------------------------------------------------------------
    # DTC breakdowns (GSPA, read-only)
    # ------------------------------------------------------------------

    def get_breakdowns(self, token: Token, vehicle: Vehicle) -> dict[str, Any] | None:
        """Get vehicle diagnostic trouble codes (DTCs) from GSPA."""
        self._validate_ccs_token(token)
        try:
            return self._gspa_get(
                token, vehicle, "diagnostics/vehicles/{carId}/breakdowns"
            )
        except Exception:
            _LOGGER.debug(f"{DOMAIN} - GSPA breakdowns failed")
            return None

    def _parse_breakdowns(self, vehicle: Vehicle, data: dict[str, Any]) -> None:
        """Parse DTC data from GSPA breakdown response.

        Response structure:
          {"breakdown": [{"ecuName": "...", "ecuIdx": 0, "dtcList": [...]}]}
        """
        breakdown = data.get("breakdown", [])
        if not breakdown:
            return
        vehicle.dtc_count = len(breakdown)
        descriptions = {}
        for item in breakdown:
            if not isinstance(item, dict):
                continue
            ecu_name = item.get("ecuName", item.get("ecuIdx", "unknown"))
            dtc_list = item.get("dtcList", [])
            if dtc_list:
                descriptions[str(ecu_name)] = dtc_list
        if descriptions:
            vehicle.dtc_descriptions = descriptions

    # ------------------------------------------------------------------
    # CCS2 vehicle property mapping
    # ------------------------------------------------------------------
    # Force refresh
    # ------------------------------------------------------------------

    def force_refresh_vehicle_state(self, token: Token, vehicle: Vehicle) -> None:
        """Wake the vehicle and re-read GSPA stored-status.

        Prewakeup is best-effort (car may be offline). The status read
        returns the last cached state regardless.
        """
        self._validate_ccs_token(token)
        try:
            self.prewakeup(token, vehicle)
        except Exception:
            _LOGGER.debug(f"{DOMAIN} - prewakeup failed (car may be offline)")
        self.update_vehicle_with_cached_state(token, vehicle)

    # ------------------------------------------------------------------
    # Update vehicle with cached state
    # ------------------------------------------------------------------

    def update_vehicle_with_cached_state(self, token: Token, vehicle: Vehicle) -> None:
        """Fetch GSPA stored-status and update vehicle properties.

        GSPA stored-status is the primary status for all vehicles. The
        response shape is:
          {serviceNo, lastUpdateTime, state:{Vehicle:{Body,Cabin,Chassis,
          Drivetrain,Green,Electronics,Location,...}}}
        state.Vehicle is the ccs2 vehicleStatus — fed to
        _update_vehicle_properties_ccs2.
        """
        if not (token.access_token or token.exchangeable_token):
            raise APIError("No CCS token — cannot fetch GSPA stored-status")
        data = self.get_stored_status(token, vehicle)
        if not data:
            raise APIError("GSPA stored-status returned no data")
        state = data.get("state", {})
        if isinstance(state, dict) and "Vehicle" in state:
            state = state["Vehicle"]
        self._update_vehicle_properties_ccs2(vehicle, state)

        if vehicle.engine_type in (ENGINE_TYPES.EV, ENGINE_TYPES.PHEV):
            try:
                state = self._get_driving_info(token, vehicle)
                if state:
                    self._update_vehicle_drive_info(vehicle, state)
            except Exception:
                _LOGGER.debug(f"{DOMAIN} - Driving info fetch failed")
            try:
                history = self._get_driving_history(token, vehicle)
                if history and isinstance(history, dict):
                    self._update_vehicle_driving_history(vehicle, history)
            except Exception:
                _LOGGER.debug(f"{DOMAIN} - Driving history fetch failed")
