"""Japan command contracts observed in Bluelink Android 1.0.17/1.0.18.

Commands require an explicit PIN and an acknowledged MQTT subscription. HTTP
acceptance alone never establishes that a vehicle command completed.
"""

import copy
import datetime as dt
import math
import re
import time
import uuid
from typing import TYPE_CHECKING, Any

from .ApiImpl import (
    ClimateRequestOptions,
    POIInfo,
    ScheduleChargingClimateRequestOptions,
    WindowRequestOptions,
)
from .Token import Token
from .Vehicle import Vehicle

if TYPE_CHECKING:
    from .japan_mqtt import JapanMqttSession

from .const import (
    CHARGE_PORT_ACTION,
    ORDER_STATUS,
    VALET_MODE_ACTION,
    VEHICLE_LOCK_ACTION,
    WINDOW_STATE,
)
from .exceptions import (
    AuthenticationError,
    InvalidAPIResponseError,
    RequestTimeoutError,
    UnsupportedControlError,
)


class JapanCommandsMixin:
    """Vehicle commands and stored charge settings for the Japan adapter.

    Wire commands are RemoteRepository$Command.command values, not enum names.
    The app constructor must be checked in bytecode: Java decompilation can
    silently omit its Object command argument and display misleading names.
    """

    _vehicle_profiles: dict[str, dict[str, Any]]
    _japan_actions: dict[str, tuple[str, tuple[str, ...] | None, ORDER_STATUS | None]]
    _charge_target_cache: dict[str, tuple[float, dict[int, int]]]
    _japan_command_diagnostics: dict[str, Any]
    _japan_diagnostic_action_id: str | None

    if TYPE_CHECKING:

        def _request(
            self, method: str, path: str, *, json_response: bool = True, **kwargs: Any
        ) -> Any: ...
        def _headers(self, token: Token | None = None) -> dict[str, str]: ...
        def _vehicle_headers(
            self, token: Token, vehicle: Vehicle, helper: JapanMqttSession | None = None
        ) -> dict[str, str]: ...
        def _mqtt_for_vehicle(
            self, token: Token, vehicle: Vehicle
        ) -> JapanMqttSession: ...
        def _legacy_temperature(
            self, vehicle: Vehicle, air_temp: object
        ) -> float | None: ...
        @staticmethod
        def _payload(result: dict[str, Any]) -> dict[str, Any]: ...
        def update_vehicle_with_charge_targets(
            self, token: Token, vehicle: Vehicle
        ) -> None: ...

    @staticmethod
    def _japan_capability(value: object) -> bool:
        return type(value) is int and value == 1

    def _japan_profile_section(self, vehicle: Vehicle, section: str) -> dict[str, Any]:
        profile = self._vehicle_profiles.get(vehicle.id, {})
        value = profile.get(section) if isinstance(profile, dict) else None
        return value if isinstance(value, dict) else {}

    def _command_vehicle_id(self, vehicle: Vehicle) -> str:
        if not isinstance(vehicle.id, str) or not re.fullmatch(
            r"[A-Za-z0-9_-]+", vehicle.id
        ):
            raise InvalidAPIResponseError("Japan vehicle identifier is invalid")
        return vehicle.id

    @staticmethod
    def _require_mqtt_identity(token: Token) -> None:
        identity = getattr(token, "mqtt_registration_uuid", None)
        if not isinstance(identity, str) or not re.fullmatch(
            r"[A-Za-z0-9_-]{1,128}", identity
        ):
            raise AuthenticationError(
                "Sign in again to register Japan vehicle commands"
            )

    def _control_headers(
        self, token: Token, vehicle: Vehicle | None = None
    ) -> dict[str, str]:
        self._require_mqtt_identity(token)
        if not isinstance(token.pin, str) or not token.pin.strip():
            raise AuthenticationError("Japan vehicle commands require an explicit PIN")
        expiry = token.control_token_expiry
        if (
            not isinstance(token.control_token, str)
            or not token.control_token.startswith("Bearer ")
            or not token.control_token[7:].strip()
            or isinstance(expiry, bool)
            or not isinstance(expiry, (int, float))
            or not math.isfinite(expiry)
            or expiry <= time.time()
        ):
            result = self._request(
                "PUT",
                "/api/v1/user/pin",
                headers=self._vehicle_headers(token, vehicle)
                if vehicle is not None
                else self._headers(token),
                json={"deviceId": token.device_id, "pin": token.pin},
            )
            control = result.get("controlToken")
            expires = result.get("expiresTime")
            if (
                not isinstance(control, str)
                or not control
                or isinstance(expires, bool)
                or not isinstance(expires, (int, float))
                or not math.isfinite(expires)
                or expires <= 0
            ):
                raise InvalidAPIResponseError("Japan control token is incomplete")
            token.control_token = "Bearer " + control
            token.control_token_expiry = time.time() + expires
        headers = (
            self._vehicle_headers(token, vehicle)
            if vehicle is not None
            else self._headers(token)
        )
        if token.control_token is None:
            raise InvalidAPIResponseError("Japan control token is incomplete")
        headers["Authorization"] = token.control_token
        return headers

    def _remember_japan_action(
        self,
        action_id: str,
        vehicle_id: str,
        children: tuple[str, ...] | None = None,
        *,
        status: ORDER_STATUS | None = None,
    ) -> None:
        """Keep bounded local receipts; the adapter owns telemetry sessions."""
        if not hasattr(self, "_japan_actions"):
            self._japan_actions = {}
        if action_id not in self._japan_actions and len(self._japan_actions) >= 128:
            self._japan_actions.pop(next(iter(self._japan_actions)))
        if status is None and children is None:
            status = ORDER_STATUS.SUCCESS
        self._japan_actions[action_id] = (vehicle_id, children, status)

    def _record_japan_command_failure(self, error: Exception) -> None:
        category = "other"
        if isinstance(error, AuthenticationError):
            category = "authentication"
        elif isinstance(error, RequestTimeoutError):
            category = "timeout"
        elif isinstance(error, InvalidAPIResponseError):
            category = "invalid_response"
        elif isinstance(error, ValueError):
            category = "invalid_input"
        diagnostic = getattr(self, "_japan_command_diagnostics", {})
        diagnostic["failed_stage"] = diagnostic.get("stage", "unknown")
        diagnostic["stage"] = (
            "submission_unknown"
            if isinstance(error, RequestTimeoutError)
            and diagnostic["failed_stage"] == "http_submitting"
            else "failed"
        )
        diagnostic["failure_category"] = category
        self._japan_command_diagnostics = diagnostic

    def _japan_command(
        self,
        token: Token,
        vehicle: Vehicle,
        endpoint: str,
        payload: dict[str, Any],
        *,
        ccs2: bool = True,
    ) -> str:
        endpoints = {
            "control/door": "door",
            "control/portdoor": "portdoor",
            "control/charge": "charge",
            "control/temperature": "temperature",
            "control/window": "window",
            "control/windowcurtain": "windowcurtain",
            "control/hornlight": "hornlight",
            "control/light": "light",
            "control/valet": "valet",
            "location/routes": "routes",
            "charge/chargingcurrent": "chargingcurrent",
            "charge/dischargelimit": "dischargelimit",
            "reservation/chargehvac": "chargehvac",
            "reservation/charge": "charge_schedule",
            "reservation/hvac": "hvac_schedule",
        }
        self._japan_diagnostic_action_id = None
        self._japan_command_diagnostics = {
            "endpoint": endpoints.get(endpoint, "other"),
            "stage": "pin_validation",
            "vehicle_execution_confirmed": False,
        }
        command = payload.get("command") if isinstance(payload, dict) else None
        if isinstance(command, str) and command in {
            "open",
            "close",
            "start",
            "stop",
            "on",
            "lowbeam",
        }:
            self._japan_command_diagnostics["command"] = command
        try:
            vehicle_id = self._command_vehicle_id(vehicle)
            # Validate PIN before connecting MQTT or performing any command request.
            headers = self._control_headers(token, vehicle)
            self._japan_command_diagnostics["stage"] = "mqtt_setup"
            helper = self._mqtt_for_vehicle(token, vehicle)
        except Exception as error:
            self._record_japan_command_failure(error)
            raise
        try:
            helper.prepare()
            # MQTT bootstrap may outlast a nearly expired cached PIN grant.
            # Recheck it immediately before the control request is submitted.
            authorization = self._control_headers(token, vehicle)["Authorization"]
            headers = self._vehicle_headers(token, vehicle, helper)
            headers["Authorization"] = authorization
            self._japan_command_diagnostics["stage"] = "http_submitting"
            result = self._request(
                "POST",
                f"/api/v2/spa/vehicles/{vehicle_id}/"
                + ("ccs2/" if ccs2 else "")
                + endpoint,
                headers=headers,
                json=payload,
                timeout=(10, 140),
            )
            self._japan_command_diagnostics["stage"] = "http_submitted"
            if not isinstance(result, dict):
                raise InvalidAPIResponseError("Japan command response is invalid")
            action_id = result.get("msgId")
            if not isinstance(action_id, str) or not action_id:
                action_id = str(uuid.uuid4())
            self._japan_diagnostic_action_id = action_id
            self._japan_command_diagnostics.update(
                stage="accepted",
                confirmation_source="http",
                vehicle_execution_confirmed=False,
            )
        except Exception as error:
            self._record_japan_command_failure(error)
            # A request timeout does not prove that the car did not execute it.
            # Keep the adapter-owned telemetry session available; never retry.
            raise
        self._remember_japan_action(action_id, vehicle_id, status=ORDER_STATUS.ACCEPTED)
        return action_id

    def check_action_status(
        self,
        token: Token,
        vehicle: Vehicle,
        action_id: str,
        synchronous: bool = False,
        timeout: int = 0,
    ) -> ORDER_STATUS:
        """Return a local service receipt, never fabricate vehicle execution."""
        vehicle_id = self._command_vehicle_id(vehicle)
        tracked = (
            self._japan_actions.get(action_id)
            if hasattr(self, "_japan_actions")
            else None
        )
        if tracked is None or tracked[0] != vehicle_id:
            return ORDER_STATUS.UNKNOWN
        children, status = tracked[1:]
        if status is not None:
            return status
        if children is None:
            return ORDER_STATUS.SUCCESS
        if not isinstance(children, tuple) or not children:
            return ORDER_STATUS.UNKNOWN
        statuses = [
            self.check_action_status(token, vehicle, child)
            if child != action_id
            else ORDER_STATUS.UNKNOWN
            for child in children
        ]
        if ORDER_STATUS.FAILED in statuses:
            status = ORDER_STATUS.FAILED
        elif ORDER_STATUS.TIMEOUT in statuses:
            status = ORDER_STATUS.TIMEOUT
        elif all(
            value in (ORDER_STATUS.SUCCESS, ORDER_STATUS.ACCEPTED) for value in statuses
        ):
            status = (
                ORDER_STATUS.ACCEPTED
                if ORDER_STATUS.ACCEPTED in statuses
                else ORDER_STATUS.SUCCESS
            )
        elif ORDER_STATUS.PENDING in statuses:
            return ORDER_STATUS.PENDING
        else:
            return ORDER_STATUS.UNKNOWN
        self._japan_actions[action_id] = (vehicle_id, children, status)
        return status

    def valet_mode_action(
        self, token: Token, vehicle: Vehicle, action: VALET_MODE_ACTION
    ) -> str:
        service = self._japan_profile_section(vehicle, "serviceOption")
        if not self._japan_capability(service.get("remoteValetActOption")):
            raise UnsupportedControlError("Japan valet control capability is absent")
        if action not in (VALET_MODE_ACTION.ACTIVATE, VALET_MODE_ACTION.DEACTIVATE):
            raise ValueError("Japan valet action is invalid")
        return self._japan_command(
            token, vehicle, "control/valet", {"action": action.value}, ccs2=False
        )

    def set_navigation(
        self, token: Token, vehicle: Vehicle, poi_list: list[POIInfo]
    ) -> str:
        """Send the app's route envelope using validated existing POI models."""
        if not isinstance(poi_list, list) or not 1 <= len(poi_list) <= 10:
            raise ValueError("Japan navigation requires one to ten destinations")
        destinations = []
        for poi in poi_list:
            try:
                value = poi.to_dict()
                coord = value["coord"]
                if not isinstance(coord, dict):
                    raise TypeError
                lat, lon = coord["lat"], coord["lon"]
                if (
                    isinstance(lat, bool)
                    or isinstance(lon, bool)
                    or not isinstance(lat, (int, float))
                    or not isinstance(lon, (int, float))
                    or not math.isfinite(lat)
                    or not math.isfinite(lon)
                    or not -90 <= lat <= 90
                    or not -180 <= lon <= 180
                    or coord.get("type") != 0
                    or type(coord.get("type")) is not int
                    or value.get("src")
                    not in {"Google", "Google_temp", "HERE", "MMI", "other", "Yandex"}
                    or value.get("lang") != 1
                    or type(value.get("lang")) is not int
                ):
                    raise ValueError
            except (AttributeError, KeyError, TypeError, ValueError):
                raise ValueError("Japan navigation destination is invalid") from None
            value["waypointID"] = 0
            destinations.append(value)
        return self._japan_command(
            token, vehicle, "location/routes", {"poiInfoList": destinations}, ccs2=False
        )

    def lock_action(
        self, token: Token, vehicle: Vehicle, action: VEHICLE_LOCK_ACTION
    ) -> str:
        if action not in (VEHICLE_LOCK_ACTION.LOCK, VEHICLE_LOCK_ACTION.UNLOCK):
            raise ValueError("Unsupported Japan door action")
        command = action.value
        return self._japan_command(token, vehicle, "control/door", {"command": command})

    def start_charge(self, token: Token, vehicle: Vehicle) -> str:
        return self._japan_command(
            token, vehicle, "control/charge", {"command": "start"}
        )

    def stop_charge(self, token: Token, vehicle: Vehicle) -> str:
        return self._japan_command(
            token, vehicle, "control/charge", {"command": "stop"}
        )

    def stop_climate(self, token: Token, vehicle: Vehicle) -> str:
        return self._japan_command(
            token, vehicle, "control/temperature", {"command": "stop"}
        )

    def charge_port_action(
        self, token: Token, vehicle: Vehicle, action: CHARGE_PORT_ACTION
    ) -> str:
        if not self._japan_capability(
            self._japan_profile_section(vehicle, "serviceOption").get(
                "chargePortDoorOption"
            )
        ):
            raise UnsupportedControlError("Japan charge port capability is absent")
        if action not in (CHARGE_PORT_ACTION.OPEN, CHARGE_PORT_ACTION.CLOSE):
            raise ValueError("Japan charge port action is invalid")
        command = action.value
        return self._japan_command(
            token, vehicle, "control/portdoor", {"command": command}
        )

    def start_hazard_lights(self, token: Token, vehicle: Vehicle) -> str:
        if not self._japan_capability(
            self._japan_profile_section(vehicle, "option").get("lightOnlyAvailable")
        ):
            raise UnsupportedControlError("Japan light control capability is absent")
        return self._japan_command(token, vehicle, "control/light", {"command": "on"})

    def start_hazard_lights_and_horn(self, token: Token, vehicle: Vehicle) -> str:
        if not self._japan_capability(
            self._japan_profile_section(vehicle, "option").get("hornLightAvailable")
        ):
            raise UnsupportedControlError("Japan horn and light capability is absent")
        option = self._japan_profile_section(vehicle, "serviceOption").get(
            "remoteLightControlOption"
        )
        command = "lowbeam" if option == 0 else "on"
        return self._japan_command(
            token, vehicle, "control/hornlight", {"command": command}
        )

    def set_charging_current(self, token: Token, vehicle: Vehicle, level: int) -> str:
        service = self._japan_profile_section(vehicle, "serviceOption")
        if not self._japan_capability(service.get("chargingCurrentSupport")):
            raise UnsupportedControlError("Japan charging current capability is absent")
        if type(level) is not int or level not in (1, 2, 3):
            raise ValueError("Japan charging current level is invalid")
        return self._japan_command(
            token, vehicle, "charge/chargingcurrent", {"chargingCurrent": level}
        )

    def set_vehicle_to_load_discharge_limit(
        self, token: Token, vehicle: Vehicle, limit: int
    ) -> str:
        service = self._japan_profile_section(vehicle, "serviceOption")
        if not self._japan_capability(service.get("v2LSocSetServiceOption")):
            raise UnsupportedControlError("Japan V2L limit capability is absent")
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 20 <= limit <= 80
            or limit % 10
        ):
            raise ValueError("Japan V2L limit is invalid")
        return self._japan_command(
            token, vehicle, "charge/dischargelimit", {"dischargingLimit": limit}
        )

    def set_windows_state(
        self, token: Token, vehicle: Vehicle, options: WindowRequestOptions
    ) -> str:
        """Send nullable app window/curtain fields with profile seat orientation."""
        service = self._japan_profile_section(vehicle, "serviceOption")
        if not self._japan_capability(service.get("windowControlOption")):
            raise UnsupportedControlError("Japan window control capability is absent")
        profile = self._japan_profile_section(vehicle, "option")
        values = (
            options.front_left,
            options.front_right,
            options.back_left,
            options.back_right,
        )
        curtains = (options.rear_left_curtain, options.rear_right_curtain)
        if not any(value is not None for value in values + curtains):
            raise ValueError("No Japan window changes requested")
        if any(
            isinstance(value, bool)
            or value
            not in (
                None,
                WINDOW_STATE.OPEN,
                WINDOW_STATE.CLOSED,
                WINDOW_STATE.VENTILATION,
            )
            for value in values
        ):
            raise ValueError("Japan window position is invalid")
        if any(
            isinstance(value, bool)
            or value not in (None, WINDOW_STATE.OPEN, WINDOW_STATE.CLOSED)
            for value in curtains
        ):
            raise ValueError("Japan curtain position is invalid")
        if (
            all(value is not None for value in values)
            and len(set(values)) == 1
            and not any(value is not None for value in curtains)
            and values[0] != WINDOW_STATE.VENTILATION
        ):
            command = "open" if values[0] == WINDOW_STATE.OPEN else "close"
            return self._japan_command(
                token, vehicle, "control/window", {"command": command}
            )
        location = profile.get("drvSeatLoc")
        if location not in ("L", "R"):
            raise UnsupportedControlError("Japan window seat orientation is unknown")
        front_left, front_right, rear_left, rear_right = values
        safety = profile.get("windowSafetyOption2")
        if safety == 4 and any(
            value is not None for value in (rear_left, rear_right) + curtains
        ):
            raise UnsupportedControlError("Japan rear window control is unavailable")
        if (
            any(value is not None for value in curtains)
            and profile.get("rearWindowCurtainOption") != 1
        ):
            raise UnsupportedControlError("Japan rear curtain capability is absent")
        payload: dict[str, Any] = {
            "drvSeatLoc": location,
            "drvSeatWindow": front_left if location == "L" else front_right,
            "psgSeatWindow": front_right if location == "L" else front_left,
        }
        if safety != 4:
            payload.update(
                {
                    "rlSeatWindow": rear_left,
                    "rrSeatWindow": rear_right,
                    "rlSeatWindowCurtain": curtains[0],
                    "rrSeatWindowCurtain": curtains[1],
                }
            )
        return self._japan_command(token, vehicle, "control/windowcurtain", payload)

    def schedule_charging_and_climate(
        self,
        token: Token,
        vehicle: Vehicle,
        options: ScheduleChargingClimateRequestOptions,
    ) -> str:
        """Preserve unspecified combined schedule fields from the cloud snapshot."""
        profile = self._vehicle_profiles.get(vehicle.id, {})
        flat = profile.get("appMode") == "EV5"
        requested = vars(options)
        if not any(
            value is not None
            for key, value in requested.items()
            if key != "temperature_unit"
        ):
            raise ValueError("No Japan schedule changes requested")
        vehicle_id = self._command_vehicle_id(vehicle)
        headers = self._control_headers(token, vehicle)
        current = self._payload(
            self._request(
                "GET",
                f"/api/v2/spa/vehicles/{vehicle_id}/reservation/charge",
                headers=headers,
            )
        )
        payload: dict[str, Any] = {
            "reservFlag": current.get("reservFlag"),
            "offPeakPowerInfo": copy.deepcopy(current.get("offPeakPowerInfo")),
        }
        for index, departure in enumerate(
            (options.first_departure, options.second_departure), 1
        ):
            source = current.get(
                "reservChargeInfo" if index == 1 else "reservChargeInfo2"
            )
            if not isinstance(source, dict) or not isinstance(
                source.get("reservChargeInfoDetail"), dict
            ):
                raise InvalidAPIResponseError(
                    "Japan current departure schedule is incomplete"
                )
            detail = copy.deepcopy(source["reservChargeInfoDetail"])
            if departure is not None:
                if departure.enabled is not None:
                    if not isinstance(departure.enabled, bool):
                        raise ValueError("Japan departure enabled must be boolean")
                    detail["reservChargeSet"] = departure.enabled
                info = detail.get("reservInfo")
                if not isinstance(info, dict):
                    raise InvalidAPIResponseError(
                        "Japan current departure schedule is incomplete"
                    )
                if departure.days is not None:
                    if not isinstance(departure.days, list) or any(
                        type(day) is not int or not 0 <= day <= 6
                        for day in departure.days
                    ):
                        raise ValueError("Japan departure days are invalid")
                    info["day"] = list(departure.days)
                if departure.time is not None:
                    info["time"] = self._japan_schedule_time(departure.time)
            fatc = detail.get("reservFatcSet")
            if isinstance(fatc, dict):
                air = fatc.get("airTemp")
                if not isinstance(air, dict):
                    raise InvalidAPIResponseError(
                        "Japan schedule temperature is incomplete"
                    )
                temperature = options.temperature
                if temperature is None:
                    temperature = self._legacy_temperature(vehicle, air)
                if temperature is None:
                    raise UnsupportedControlError(
                        "Japan current schedule temperature encoding is unknown"
                    )
                hvac_type = air.get(
                    "hvacTempType", profile.get("option", {}).get("hvacTempType")
                )
                valid = [
                    self._legacy_temperature(
                        vehicle,
                        {"unit": 0, "hvacTempType": hvac_type, "value": f"{code:02X}H"},
                    )
                    for code in range(0x40)
                ]
                if temperature not in valid:
                    raise ValueError(
                        "Japan schedule temperature is outside profile range"
                    )
                encoded = str(float(temperature))
                if flat:
                    encoded = next(
                        f"{code:02X}H"
                        for code, value in enumerate(valid)
                        if value == temperature
                    )
                air.update({"value": encoded, "unit": 0, "hvacTempType": hvac_type})
                if options.climate_enabled is not None:
                    if not isinstance(options.climate_enabled, bool):
                        raise ValueError(
                            "Japan schedule climate enabled must be boolean"
                        )
                    fatc["airCtrl"] = int(options.climate_enabled)
                if options.defrost is not None:
                    if not isinstance(options.defrost, bool):
                        raise ValueError("Japan schedule defrost must be boolean")
                    fatc["defrost"] = options.defrost
            elif any(
                value is not None
                for value in (
                    options.temperature,
                    options.climate_enabled,
                    options.defrost,
                )
            ):
                raise UnsupportedControlError(
                    "Japan current schedule has no climate configuration"
                )
            payload[f"reservChargeInfo{index}"] = detail
        if options.charging_enabled is not None:
            if not isinstance(options.charging_enabled, bool):
                raise ValueError("Japan charging enabled must be boolean")
            payload["reservFlag"] = int(options.charging_enabled)
        peak = payload["offPeakPowerInfo"]
        if (
            not isinstance(peak, dict)
            or not isinstance(peak.get("offPeakPowerTime1"), dict)
            or payload["reservFlag"] is None
        ):
            raise InvalidAPIResponseError(
                "Japan current off-peak schedule is incomplete"
            )
        for field, value in (
            ("starttime", options.off_peak_start_time),
            ("endtime", options.off_peak_end_time),
        ):
            if value is not None:
                peak["offPeakPowerTime1"][field] = self._japan_schedule_time(value)
        if options.off_peak_charge_only_enabled is not None:
            if not isinstance(options.off_peak_charge_only_enabled, bool):
                raise ValueError("Japan off-peak only enabled must be boolean")
            peak["offPeakPowerFlag"] = 2 if options.off_peak_charge_only_enabled else 1
        if not flat:
            return self._japan_command(
                token, vehicle, "reservation/chargehvac", payload
            )
        charge_active = any(
            value is not None
            for value in (
                options.charging_enabled,
                options.off_peak_start_time,
                options.off_peak_end_time,
                options.off_peak_charge_only_enabled,
            )
        )
        climate_active = any(
            value is not None
            for value in (
                options.first_departure,
                options.second_departure,
                options.temperature,
                options.climate_enabled,
                options.defrost,
            )
        )
        requests = []
        if charge_active:
            requests.append(
                (
                    "reservation/charge",
                    {
                        "reservFlag": payload["reservFlag"],
                        "offpeakPowerFlag": peak["offPeakPowerFlag"],
                        "reservStartTime": peak["offPeakPowerTime1"]["starttime"],
                        "reservEndTime": peak["offPeakPowerTime1"]["endtime"],
                    },
                )
            )
        if climate_active:
            climate = {}
            for index in (1, 2):
                detail = payload[f"reservChargeInfo{index}"]
                fatc = detail.get("reservFatcSet")
                if not isinstance(fatc, dict):
                    raise InvalidAPIResponseError(
                        "Japan flat climate schedule is incomplete"
                    )
                if "reservChargeSet" not in detail or any(
                    key not in fatc for key in ("airCtrl", "defrost", "airTemp")
                ):
                    raise InvalidAPIResponseError(
                        "Japan flat climate schedule is incomplete"
                    )
                climate[f"reservedHVACInfo{index}"] = {
                    "reservHVACflag": int(detail["reservChargeSet"]),
                    "reservInfo": detail["reservInfo"],
                    "reservHVACSet": {
                        key: fatc[key] for key in ("airCtrl", "defrost", "airTemp")
                    },
                }
            requests.append(("reservation/hvac", climate))
        # Validate both wire envelopes before either setting can be changed.
        actions = [
            self._japan_command(token, vehicle, endpoint, body)
            for endpoint, body in requests
        ]
        if len(actions) == 1:
            return actions[0]
        action_id = str(uuid.uuid4())
        self._remember_japan_action(action_id, vehicle_id, tuple(actions))
        self.check_action_status(token, vehicle, action_id)
        return action_id

    @staticmethod
    def _japan_schedule_time(value: object) -> dict[str, str | int]:
        if (
            not isinstance(value, dt.time)
            or value.tzinfo is not None
            or value.second
            or value.microsecond
        ):
            raise ValueError("Japan schedule time must contain hours and minutes only")
        return {
            "time": value.strftime("%I%M"),
            "timeSection": int(value >= dt.time(12)),
        }

    def start_climate(
        self, token: Token, vehicle: Vehicle, options: ClimateRequestOptions
    ) -> str:
        """Construct the supported numeric Celsius Japan climate envelope."""
        profile = self._japan_profile_section(vehicle, "option")
        hvac_type = (
            options.hvac_temp_type
            if options.hvac_temp_type is not None
            else profile.get("hvacTempType")
        )
        if type(hvac_type) is not int or hvac_type not in (1, 2, 4):
            raise UnsupportedControlError(
                "Japan HVAC temperature type is not established"
            )
        if options.temp_unit not in (None, 0):
            raise UnsupportedControlError("Japan climate supports Celsius only")
        temp = (
            options.set_temp
            if options.set_temp is not None
            else vehicle.air_temperature
        )
        if (
            isinstance(temp, bool)
            or not isinstance(temp, (int, float))
            or not math.isfinite(temp)
        ):
            raise ValueError("Japan climate temperature must be finite")
        # Verify that the app's profile-dependent code decoder accepts the setpoint.
        temperatures = [
            self._legacy_temperature(
                vehicle, {"unit": 0, "hvacTempType": hvac_type, "value": f"{code:02X}H"}
            )
            for code in range(0x40)
        ]
        if temp not in temperatures:
            raise ValueError(
                "Japan temperature is outside the established profile range"
            )
        if (
            options.driver_seat_location is not None
            and options.driver_seat_location.value != profile.get("drvSeatLoc")
        ):
            raise UnsupportedControlError(
                "Japan driver seat override differs from profile"
            )
        if options.climate is False:
            raise UnsupportedControlError(
                "Japan climate start requires climate enabled"
            )
        payload: dict[str, Any] = {
            "command": "start",
            "hvacTemp": str(float(temp)),
            "hvacTempType": hvac_type,
            "tempUnit": "C",
        }
        if options.duration is not None:
            if not self._japan_capability(profile.get("clmtCtrlOption")):
                raise UnsupportedControlError(
                    "Japan ignition duration capability is absent"
                )
            if (
                isinstance(options.duration, bool)
                or not isinstance(options.duration, int)
                or not 1 <= options.duration <= 10
            ):
                raise ValueError("Japan ignition duration is invalid")
            payload["ignitionDuration"] = options.duration
        seats = profile.get("seatHeaterVent", {})
        location = profile.get("drvSeatLoc")
        seat_values = (
            options.front_left_seat,
            options.front_right_seat,
            options.rear_left_seat,
            options.rear_right_seat,
        )
        if any(value is not None for value in seat_values):
            if not isinstance(seats, dict) or location not in ("L", "R"):
                raise UnsupportedControlError(
                    "Japan seat climate profile is incomplete"
                )
            mappings = (
                (
                    "drvSeatHeat" if location == "L" else "astSeatHeat",
                    "drvSeatClimateState" if location == "L" else "psgSeatClimateState",
                ),
                (
                    "astSeatHeat" if location == "L" else "drvSeatHeat",
                    "psgSeatClimateState" if location == "L" else "drvSeatClimateState",
                ),
                ("rlSeatHeat", "rlSeatClimateState"),
                ("rrSeatHeat", "rrSeatClimateState"),
            )
            seat_payload = {}
            for value, (capability, field) in zip(seat_values, mappings, strict=True):
                if value is None:
                    continue
                if type(seats.get(capability)) is not int or seats[capability] not in (
                    1,
                    2,
                    3,
                    4,
                    5,
                    6,
                ):
                    raise UnsupportedControlError(
                        "Japan seat climate capability is absent"
                    )
                if (
                    isinstance(value, bool)
                    or not isinstance(value, int)
                    or not 1 <= value <= 8
                ):
                    raise ValueError("Japan seat climate command is invalid")
                seat_payload[field] = value
            payload["drvSeatLoc"] = location
            payload["seatClimateInfo"] = seat_payload
        if options.defrost is not None:
            if not isinstance(options.defrost, bool):
                raise ValueError("Japan defrost must be boolean")
            payload["windshieldFrontDefogState"] = options.defrost
        if options.heating is not None:
            if type(options.heating) is not int or options.heating not in (0, 1, 2, 3):
                raise ValueError("Japan heating option is invalid")
            payload["heating1"] = options.heating
        step_type = profile.get("strgWhlHeatOption")
        if step_type in ("1", "2"):
            payload["heating1"] = None
            if options.heating is not None:
                payload["sideRearMirrorHeating"] = int(options.heating in (1, 2))
                payload["strgWhlHeating"] = int(options.heating in (1, 3))
            if options.steering_wheel is not None:
                if type(
                    options.steering_wheel
                ) is not int or not 0 <= options.steering_wheel <= int(step_type):
                    raise ValueError("Japan steering wheel heat step is invalid")
                payload["strgWhlHeating"] = options.steering_wheel
            if options.side_rear_mirror_heating is not None:
                if type(
                    options.side_rear_mirror_heating
                ) is not int or options.side_rear_mirror_heating not in (0, 1):
                    raise ValueError("Japan mirror heating is invalid")
                payload["sideRearMirrorHeating"] = options.side_rear_mirror_heating
        elif (
            options.steering_wheel is not None
            or options.side_rear_mirror_heating is not None
        ):
            raise UnsupportedControlError("Japan separate heater capability is absent")
        return self._japan_command(token, vehicle, "control/temperature", payload)

    def set_charge_limits(
        self, token: Token, vehicle: Vehicle, ac: int, dc: int
    ) -> str:
        """Store charge targets; HTTP success confirms the cloud setting write."""
        vehicle_id = self._command_vehicle_id(vehicle)
        if any(
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 50 <= value <= 100
            or value % 10
            for value in (ac, dc)
        ):
            raise ValueError("Japan charge targets must be 50–100 in steps of ten")
        cached = getattr(self, "_charge_target_cache", {}).get(vehicle_id)
        if cached is None:
            self.update_vehicle_with_charge_targets(token, vehicle)
            cached = getattr(self, "_charge_target_cache", {}).get(vehicle_id)
        targets = cached[1] if cached is not None else {}
        ac_types = [kind for kind in (1, 2) if kind in targets]
        if 0 not in targets or len(ac_types) != 1:
            raise InvalidAPIResponseError(
                "Japan charge target plug metadata is incomplete"
            )
        self._request(
            "POST",
            f"/api/v1/spa/vehicles/{vehicle_id}/charge/target",
            headers=self._vehicle_headers(token, vehicle),
            json={
                "targetSOClist": [
                    {"plugType": 0, "targetSOClevel": dc},
                    {"plugType": ac_types[0], "targetSOClevel": ac},
                ]
            },
        )
        getattr(self, "_charge_target_cache", {}).pop(vehicle_id, None)
        action_id = str(uuid.uuid4())
        self._remember_japan_action(action_id, vehicle_id)
        return action_id
