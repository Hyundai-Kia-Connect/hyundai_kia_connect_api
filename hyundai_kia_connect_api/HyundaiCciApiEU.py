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
from typing import Any

import requests

from .const import (
    DISTANCE_UNITS,
    DOMAIN,
    ENGINE_TYPES,
)
from .exceptions import APIError, AuthenticationError
from .GspaApiEU import GspaApiEU
from .Token import Token
from .utils import (
    get_child_value,
)
from .Vehicle import DailyDrivingStats, Vehicle

_LOGGER = logging.getLogger(__name__)


class HyundaiCciApiEU(GspaApiEU):
    """Hyundai EU CCI/GSPA API.

    Uses the CCI login flow (OneApp client_id 4f4953b5) confirmed on
    production endpoints. Login, token lifecycle, the GSPA
    secure-request layer, and the GSPA remote-control layer are all
    inherited from ``GspaApiEU``. This subclass carries the brand
    constants and the Hyundai-specific read layer: CCS2 vehicle-property
    parsing, driving info/history, and breakdowns.
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

    # CCS2 EU vehicles support GSPA window control.
    supports_window_control: bool = True

    # Hyundai EU CCI remote control is live-verified.
    GSPA_REMOTE_CONTROL_VERIFIED = True

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

    def prewakeup(self, token: Token, vehicle: Vehicle) -> dict[str, Any] | None:
        """Send a prewakeup command to bring the vehicle online.

        GSPA remote paths are brand-global (the path is shared across EU
        CCI brands, issued on the instance's CCSP host).
        """
        car_id = vehicle.id
        url = self.CCSP_API_URL + f"/gspa/v1/remote/vehicles/{car_id}/prewakeup"
        self._validate_ccs_token(token)
        headers = self._get_authenticated_headers(
            token, vehicle.ccu_ccs2_protocol_support or 0
        )
        try:
            response = requests.post(url, headers=headers, timeout=(5, 60))
            if response.status_code == 401:
                raise AuthenticationError("GSPA: Token expired or invalid")
            if response.status_code >= 400:
                raise APIError(
                    f"GSPA control error: HTTP {response.status_code} - "
                    f"{response.text[:200]}"
                )
            data: dict[str, Any] = response.json()
            rc = data.get("rc")
            if rc and rc != "0000":
                raise APIError(f"GSPA error: rc={rc}, msg={data.get('msg', '')}")
            rs: dict[str, Any] = data.get("rs", data)
            return rs
        except AuthenticationError:
            raise
        except Exception:
            _LOGGER.debug(f"{DOMAIN} - GSPA prewakeup failed")
            return None

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
