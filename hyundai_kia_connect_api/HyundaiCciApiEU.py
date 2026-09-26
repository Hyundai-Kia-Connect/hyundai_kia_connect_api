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

from .const import (
    DISTANCE_UNITS,
    DOMAIN,
    ENGINE_TYPES,
)
from .exceptions import APIError
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

    # v1 CCAPI host (prd.eu-ccapi.<brand>.com:8080) — used only by the legacy
    # /tripinfo endpoint; the GSPA layer stays on the CCSP host.
    CCAPI_BASE_URL = "prd.eu-ccapi.hyundai.com:8080"

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
    def _update_vehicle_extended_data(self, token: Token, vehicle: Vehicle) -> None:
        """Populate Vehicle fields from GSPA query endpoints (read-only).

        These are GET reads of server-side cached data — they do not wake
        the telematics unit or drain the 12V battery. Failures leave the
        fields unknown (None), never stale values (HA convention).
        """
        try:
            valet = self.get_valet_status(token, vehicle)
            if valet and isinstance(valet, dict):
                mode = valet.get("valetMode", "").lower()
                vehicle.valet_mode_active = mode == "active"
        except Exception:
            vehicle.valet_mode_active = None
            _LOGGER.debug(f"{DOMAIN} - GSPA valet_status update failed")

        # Stored-status widget provides lamp, signal, ignition, sleep data
        try:
            widget = self.get_stored_status_widget(token, vehicle)
            if widget and isinstance(widget, dict):
                self._parse_gspa_widget_lamp_data(vehicle, widget)
        except Exception:
            _LOGGER.debug(f"{DOMAIN} - GSPA stored-status-widget failed")

        # DTC breakdowns
        try:
            bd = self.get_breakdowns(token, vehicle)
            if bd and isinstance(bd, dict):
                self._parse_breakdowns(vehicle, bd)
        except Exception:
            _LOGGER.debug(f"{DOMAIN} - GSPA breakdowns update failed")

    @staticmethod
    def _parse_gspa_widget_lamp_data(vehicle: Vehicle, widget: dict[str, Any]) -> None:
        """Parse lamp/signal/ignition/sleep data from GSPA stored-status-widget.

        Live-probed structure (2026-06-15, Santa Fe HEV):
          state.Vehicle.Body.Lights.Front.Left.Low.Warning  (0.0/1.0)
          state.Vehicle.Body.Lights.Front.Left.High.Warning
          state.Vehicle.Body.Lights.Front.Left.TurnSignal.Warning
          state.Vehicle.Body.Lights.Rear.Left.StopLamp.Warning
          state.Vehicle.Electronics.PowerSupply.Ignition3  (0.0/1.0)
          state.Vehicle.RemoteControl.SleepMode  (0.0/1.0)
          state.Vehicle.Drivetrain.Transmission.ParkingPosition  (0.0/1.0)

        Values are 0.0/1.0 floats. All parsing is defensive.
        """
        # The widget data may be nested under "state.Vehicle" or flat
        state = widget.get("state", widget)
        vehicle_data = state.get("Vehicle", state)

        lights = vehicle_data.get("Body", {}).get("Lights", {})
        front = lights.get("Front", {})
        for side, obj in (
            ("left", front.get("Left", {})),
            ("right", front.get("Right", {})),
        ):
            if not isinstance(obj, dict):
                continue
            low = obj.get("Low", {})
            if isinstance(low, dict) and "Warning" in low:
                setattr(vehicle, f"headlamp_{side}_low", bool(low["Warning"]))
            high = obj.get("High", {})
            if isinstance(high, dict) and "Warning" in high:
                setattr(vehicle, f"headlamp_{side}_high", bool(high["Warning"]))
            bifunc = obj.get("Bifunc", {})
            if isinstance(bifunc, dict) and "Warning" in bifunc:
                setattr(vehicle, f"headlamp_{side}_bifunc", bool(bifunc["Warning"]))
            ts = obj.get("TurnSignal", {})
            if isinstance(ts, dict) and "Warning" in ts:
                setattr(vehicle, f"turn_signal_{side}_front", bool(ts["Warning"]))

        head_lamp = front.get("HeadLamp", {})
        if isinstance(head_lamp, dict) and "SystemWarning" in head_lamp:
            vehicle.headlamp_status = head_lamp.get("SystemWarning")

        rear = lights.get("Rear", {})
        for side, obj in (
            ("left", rear.get("Left", {})),
            ("right", rear.get("Right", {})),
        ):
            if not isinstance(obj, dict):
                continue
            sl = obj.get("StopLamp", {})
            if isinstance(sl, dict) and "Warning" in sl:
                setattr(vehicle, f"stop_lamp_{side}", bool(sl["Warning"]))
            ts = obj.get("TurnSignal", {})
            if isinstance(ts, dict) and "Warning" in ts:
                setattr(vehicle, f"turn_signal_{side}_rear", bool(ts["Warning"]))

        power_supply = vehicle_data.get("Electronics", {}).get("PowerSupply", {})
        if isinstance(power_supply, dict) and "Ignition3" in power_supply:
            vehicle.ign3 = bool(power_supply["Ignition3"])

        remote_ign = vehicle_data.get("remoteIgnition")
        if remote_ign is not None:
            vehicle.remote_ignition = bool(remote_ign)

        remote_control = vehicle_data.get("RemoteControl", {})
        if isinstance(remote_control, dict) and "SleepMode" in remote_control:
            vehicle.sleep_mode_check = bool(remote_control["SleepMode"])

        drivetrain = vehicle_data.get("Drivetrain", {})
        transmission = drivetrain.get("Transmission", {})
        if isinstance(transmission, dict) and "ParkingPosition" in transmission:
            vehicle.transmission_condition = transmission["ParkingPosition"]

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
        self._update_vehicle_extended_data(token, vehicle)
