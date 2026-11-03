"""Japan command contracts observed in Bluelink Android 1.0.17.

Commands require an explicit PIN and an acknowledged MQTT subscription. HTTP
acceptance alone never establishes that a vehicle command completed.
"""

import copy
import datetime as dt
import math
import re
import time
import uuid

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
    UnsupportedControlError,
)


class JapanCommandsMixin:
    """Vehicle commands and stored charge settings for the Japan adapter.

    Wire commands are RemoteRepository$Command.command values, not enum names.
    The app constructor must be checked in bytecode: Java decompilation can
    silently omit its Object command argument and display misleading names.
    """

    @staticmethod
    def _japan_capability(value):
        return type(value) is int and value == 1

    def _japan_profile_section(self, vehicle, section):
        profile = self._vehicle_profiles.get(vehicle.id, {})
        value = profile.get(section) if isinstance(profile, dict) else None
        return value if isinstance(value, dict) else {}

    def _command_vehicle_id(self, vehicle):
        if not isinstance(vehicle.id, str) or not re.fullmatch(
            r"[A-Za-z0-9_-]+", vehicle.id
        ):
            raise InvalidAPIResponseError("Japan vehicle identifier is invalid")
        return vehicle.id

    @staticmethod
    def _require_mqtt_identity(token):
        identity = getattr(token, "mqtt_registration_uuid", None)
        if not isinstance(identity, str) or not re.fullmatch(
            r"[A-Za-z0-9_-]{1,128}", identity
        ):
            raise AuthenticationError(
                "Sign in again to register Japan vehicle commands"
            )

    def _control_headers(self, token, vehicle=None):
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
        headers["Authorization"] = token.control_token
        return headers

    def _remember_japan_action(self, action_id, vehicle_id, helper=None):
        if not hasattr(self, "_japan_actions"):
            self._japan_actions = {}
        if action_id not in self._japan_actions and len(self._japan_actions) >= 128:
            evicted = self._japan_actions.pop(next(iter(self._japan_actions)))
            if (
                evicted[1] is not None
                and not isinstance(evicted[1], tuple)
                and not any(
                    entry[1] is evicted[1] and entry[2] is None
                    for entry in self._japan_actions.values()
                )
            ):
                evicted[1].close()
        self._japan_actions[action_id] = (vehicle_id, helper, None)

    def _record_japan_command_failure(self, error):
        category = "other"
        if isinstance(error, AuthenticationError):
            category = "authentication"
        elif isinstance(error, InvalidAPIResponseError):
            category = "invalid_response"
        elif isinstance(error, ValueError):
            category = "invalid_input"
        diagnostic = getattr(self, "_japan_command_diagnostics", {})
        diagnostic["failed_stage"] = diagnostic.get("stage", "unknown")
        diagnostic["stage"] = "failed"
        diagnostic["failure_category"] = category
        self._japan_command_diagnostics = diagnostic

    def _japan_command(self, token, vehicle, endpoint, payload, *, ccs2=True):
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
            )
            self._japan_command_diagnostics["stage"] = "http_submitted"
            action_id = result.get("msgId")
            if not isinstance(action_id, str) or not action_id:
                raise InvalidAPIResponseError(
                    "Japan command tracking identifier is missing"
                )
            helper.track(action_id)
            self._japan_diagnostic_action_id = action_id
            self._japan_command_diagnostics["stage"] = "waiting"
        except Exception as error:
            self._record_japan_command_failure(error)
            helper.close()
            raise
        self._remember_japan_action(action_id, vehicle_id, helper)
        return action_id

    def check_action_status(
        self, token, vehicle, action_id, synchronous=False, timeout=0
    ):
        """Match a tracked HTTP message ID to its MQTT SID completion response."""
        vehicle_id = self._command_vehicle_id(vehicle)
        tracked = getattr(self, "_japan_actions", {}).get(action_id)
        if tracked is None or tracked[0] != vehicle_id:
            return ORDER_STATUS.UNKNOWN
        helper = tracked[1]
        if tracked[2] is not None:
            return tracked[2]
        if helper is None:
            # Stored charge settings have completed at the authenticated HTTP layer.
            return ORDER_STATUS.SUCCESS
        if synchronous and (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or timeout <= 0
        ):
            raise ValueError("Japan synchronous status requires a positive timeout")
        if isinstance(helper, tuple):
            deadline = time.monotonic() + min(timeout, 120) if synchronous else None
            statuses = []
            for child in helper:
                remaining = max(0, deadline - time.monotonic()) if synchronous else 0
                statuses.append(
                    self.check_action_status(
                        token, vehicle, child, synchronous and remaining > 0, remaining
                    )
                )
            if ORDER_STATUS.FAILED in statuses:
                status = ORDER_STATUS.FAILED
            elif ORDER_STATUS.TIMEOUT in statuses:
                status = ORDER_STATUS.TIMEOUT
            elif all(value == ORDER_STATUS.SUCCESS for value in statuses):
                status = ORDER_STATUS.SUCCESS
            elif synchronous and time.monotonic() >= deadline:
                status = ORDER_STATUS.TIMEOUT
            else:
                return ORDER_STATUS.PENDING
            self._japan_actions[action_id] = (vehicle_id, helper, status)
            if status in (ORDER_STATUS.TIMEOUT, ORDER_STATUS.FAILED):
                # A terminal aggregate result will not poll its children again.
                # Retire unresolved children so they cannot retain a shared
                # broker session forever; unrelated pending actions still own it.
                unresolved_status = (
                    ORDER_STATUS.TIMEOUT
                    if status == ORDER_STATUS.TIMEOUT
                    else ORDER_STATUS.UNKNOWN
                )
                retired_helpers = {}
                for child, child_status in zip(helper, statuses, strict=True):
                    if child_status not in (ORDER_STATUS.PENDING, ORDER_STATUS.UNKNOWN):
                        continue
                    entry = self._japan_actions.get(child)
                    if (
                        entry is None
                        or entry[0] != vehicle_id
                        or entry[2] is not None
                        or entry[1] is None
                        or isinstance(entry[1], tuple)
                    ):
                        continue
                    self._japan_actions[child] = (
                        vehicle_id,
                        entry[1],
                        unresolved_status,
                    )
                    retired_helpers[id(entry[1])] = entry[1]
                    if child == getattr(self, "_japan_diagnostic_action_id", None):
                        self._japan_command_diagnostics["stage"] = (
                            "timeout"
                            if status == ORDER_STATUS.TIMEOUT
                            else "unconfirmed"
                        )
                for session in retired_helpers.values():
                    if not any(
                        entry[1] is session and entry[2] is None
                        for entry in self._japan_actions.values()
                    ):
                        session.close()
            return status
        try:
            status = helper.status(
                action_id, timeout=min(timeout, 120) if synchronous else 0
            )
        except Exception as error:
            if action_id == getattr(self, "_japan_diagnostic_action_id", None):
                self._record_japan_command_failure(error)
            helper.close()
            raise
        stages = {
            ORDER_STATUS.SUCCESS: "success",
            ORDER_STATUS.FAILED: "failed",
            ORDER_STATUS.TIMEOUT: "timeout",
            ORDER_STATUS.PENDING: "waiting",
        }
        if status in stages and action_id == getattr(
            self, "_japan_diagnostic_action_id", None
        ):
            self._japan_command_diagnostics["stage"] = stages[status]
        if status in (ORDER_STATUS.SUCCESS, ORDER_STATUS.FAILED, ORDER_STATUS.TIMEOUT):
            self._japan_actions[action_id] = (vehicle_id, helper, status)
            if not any(
                other_id != action_id and entry[1] is helper and entry[2] is None
                for other_id, entry in self._japan_actions.items()
            ):
                helper.close()
        return status

    def valet_mode_action(self, token, vehicle, action):
        service = self._japan_profile_section(vehicle, "serviceOption")
        if not self._japan_capability(service.get("remoteValetActOption")):
            raise UnsupportedControlError("Japan valet control capability is absent")
        if action not in (VALET_MODE_ACTION.ACTIVATE, VALET_MODE_ACTION.DEACTIVATE):
            raise ValueError("Japan valet action is invalid")
        return self._japan_command(
            token, vehicle, "control/valet", {"action": action.value}, ccs2=False
        )

    def set_navigation(self, token, vehicle, poi_list):
        """Send the app's route envelope using validated existing POI models."""
        if not isinstance(poi_list, list) or not 1 <= len(poi_list) <= 10:
            raise ValueError("Japan navigation requires one to ten destinations")
        destinations = []
        for poi in poi_list:
            try:
                value = poi.to_dict()
                coord = value["coord"]
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

    def lock_action(self, token, vehicle, action):
        if action not in (VEHICLE_LOCK_ACTION.LOCK, VEHICLE_LOCK_ACTION.UNLOCK):
            raise ValueError("Unsupported Japan door action")
        command = action.value
        return self._japan_command(token, vehicle, "control/door", {"command": command})

    def start_charge(self, token, vehicle):
        return self._japan_command(
            token, vehicle, "control/charge", {"command": "start"}
        )

    def stop_charge(self, token, vehicle):
        return self._japan_command(
            token, vehicle, "control/charge", {"command": "stop"}
        )

    def stop_climate(self, token, vehicle):
        return self._japan_command(
            token, vehicle, "control/temperature", {"command": "stop"}
        )

    def charge_port_action(self, token, vehicle, action):
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

    def start_hazard_lights(self, token, vehicle):
        if not self._japan_capability(
            self._japan_profile_section(vehicle, "option").get("lightOnlyAvailable")
        ):
            raise UnsupportedControlError("Japan light control capability is absent")
        return self._japan_command(token, vehicle, "control/light", {"command": "on"})

    def start_hazard_lights_and_horn(self, token, vehicle):
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

    def set_charging_current(self, token, vehicle, level):
        service = self._japan_profile_section(vehicle, "serviceOption")
        if not self._japan_capability(service.get("chargingCurrentSupport")):
            raise UnsupportedControlError("Japan charging current capability is absent")
        if type(level) is not int or level not in (1, 2, 3):
            raise ValueError("Japan charging current level is invalid")
        return self._japan_command(
            token, vehicle, "charge/chargingcurrent", {"chargingCurrent": level}
        )

    def set_vehicle_to_load_discharge_limit(self, token, vehicle, limit):
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

    def set_windows_state(self, token, vehicle, options):
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
        payload = {
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

    def schedule_charging_and_climate(self, token, vehicle, options):
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
        payload = {
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
        return action_id

    @staticmethod
    def _japan_schedule_time(value):
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

    def start_climate(self, token, vehicle, options):
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
        payload = {
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

    def set_charge_limits(self, token, vehicle, ac, dc):
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
