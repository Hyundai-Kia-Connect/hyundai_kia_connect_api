"""ApiImplType1.py"""

import datetime as dt
import requests
import logging
from typing import Optional

from .ApiImpl import (
    ApiImpl,
)
from .Token import Token
from .Vehicle import Vehicle

from .utils import (
    get_child_value,
    parse_datetime,
)

from .const import (
    DOMAIN,
    DISTANCE_UNITS,
    ENGINE_TYPES,
    SEAT_STATUS,
    TEMPERATURE_UNITS,
)

from .exceptions import (
    APIError,
    DuplicateRequestError,
    RequestTimeoutError,
    ServiceTemporaryUnavailable,
    NoDataFound,
    InvalidAPIResponseError,
    RateLimitingError,
    DeviceIDError,
)

USER_AGENT_OK_HTTP: str = "okhttp/3.12.0"

_LOGGER = logging.getLogger(__name__)


def _check_response_for_errors(response: dict) -> None:
    """
    Checks for errors in the API response.
    If an error is found, an exception is raised.
    retCode known values:
    - S: success
    - F: failure
    resCode / resMsg known values:
    - 0000: no error
    - 4002:  "Invalid request body - invalid deviceId",
             relogin will resolve but a bandaid.
    - 4004: "Duplicate request"
    - 4081: "Request timeout"
    - 5031: "Unavailable remote control - Service Temporary Unavailable"
    - 5091: "Exceeds number of requests"
    - 5921: "No Data Found v2 - No Data Found v2"
    - 9999: "Undefined Error - Response timeout"
    :param response: the API's JSON response
    """

    error_code_mapping = {
        "4002": DeviceIDError,
        "4004": DuplicateRequestError,
        "4081": RequestTimeoutError,
        "5031": ServiceTemporaryUnavailable,
        "5091": RateLimitingError,
        "5921": NoDataFound,
        "9999": RequestTimeoutError,
    }

    if not any(x in response for x in ["retCode", "resCode", "resMsg"]):
        _LOGGER.error(f"Unknown API response format: {response}")
        raise InvalidAPIResponseError()

    if response["retCode"] == "F":
        if response["resCode"] in error_code_mapping:
            raise error_code_mapping[response["resCode"]](response["resMsg"])
        raise APIError(
            f"Server returned:  '{response['resCode']}' '{response['resMsg']}'"
        )


class ApiImplType1(ApiImpl):
    """ApiImplType1"""

    def __init__(self) -> None:
        """Initialize."""

    def _get_authenticated_headers(
        self, token: Token, ccs2_support: Optional[int] = None
    ) -> dict:
        return {
            "Authorization": token.access_token,
            "ccsp-service-id": self.CCSP_SERVICE_ID,
            "ccsp-application-id": self.APP_ID,
            "Stamp": self._get_stamp(),
            "ccsp-device-id": token.device_id,
            "Host": self.BASE_URL,
            "Connection": "Keep-Alive",
            "Accept-Encoding": "gzip",
            "Ccuccs2protocolsupport": str(ccs2_support or 0),
            "User-Agent": USER_AGENT_OK_HTTP,
        }

    def _update_vehicle_properties_ccs2(self, vehicle: Vehicle, state: dict) -> None:
        if get_child_value(state, "Date"):
            vehicle.last_updated_at = parse_datetime(
                get_child_value(state, "Date"), self.data_timezone
            )
        else:
            vehicle.last_updated_at = dt.datetime.now(self.data_timezone)

        vehicle.odometer = (
            get_child_value(state, "Drivetrain.Odometer"),
            DISTANCE_UNITS[1],
        )
        vehicle.car_battery_percentage = get_child_value(
            state, "Electronics.Battery.Level"
        )

        vehicle.engine_is_running = get_child_value(state, "DrivingReady")

        air_temp = get_child_value(
            state,
            "Cabin.HVAC.Row1.Driver.Temperature.Value",
        )

        if air_temp != "OFF":
            vehicle.air_temperature = (air_temp, TEMPERATURE_UNITS[1])

        defrost_is_on = get_child_value(state, "Body.Windshield.Front.Defog.State")
        if defrost_is_on in [0, 2]:
            vehicle.defrost_is_on = False
        elif defrost_is_on == 1:
            vehicle.defrost_is_on = True

        steer_wheel_heat = get_child_value(state, "Cabin.SteeringWheel.Heat.State")
        if steer_wheel_heat in [0, 2]:
            vehicle.steering_wheel_heater_is_on = False
        elif steer_wheel_heat == 1:
            vehicle.steering_wheel_heater_is_on = True

        defrost_rear_is_on = get_child_value(state, "Body.Windshield.Rear.Defog.State")
        if defrost_rear_is_on in [0, 2]:
            vehicle.back_window_heater_is_on = False
        elif defrost_rear_is_on == 1:
            vehicle.back_window_heater_is_on = True

        # TODO: status.sideMirrorHeat

        vehicle.front_left_seat_status = SEAT_STATUS[
            get_child_value(state, "Cabin.Seat.Row1.Driver.Climate.State")
        ]

        vehicle.front_right_seat_status = SEAT_STATUS[
            get_child_value(state, "Cabin.Seat.Row1.Passenger.Climate.State")
        ]

        vehicle.rear_left_seat_status = SEAT_STATUS[
            get_child_value(state, "Cabin.Seat.Row2.Left.Climate.State")
        ]

        vehicle.rear_right_seat_status = SEAT_STATUS[
            get_child_value(state, "Cabin.Seat.Row2.Right.Climate.State")
        ]

        # TODO: status.doorLock

        vehicle.front_left_door_is_open = get_child_value(
            state, "Cabin.Door.Row1.Driver.Open"
        )
        vehicle.front_right_door_is_open = get_child_value(
            state, "Cabin.Door.Row1.Passenger.Open"
        )
        vehicle.back_left_door_is_open = get_child_value(
            state, "Cabin.Door.Row2.Left.Open"
        )
        vehicle.back_right_door_is_open = get_child_value(
            state, "Cabin.Door.Row2.Right.Open"
        )

        # TODO: should the windows and trunc also be checked?
        vehicle.is_locked = not (
            vehicle.front_left_door_is_open
            or vehicle.front_right_door_is_open
            or vehicle.back_left_door_is_open
            or vehicle.back_right_door_is_open
        )

        vehicle.hood_is_open = get_child_value(state, "Body.Hood.Open")
        vehicle.front_left_window_is_open = get_child_value(
            state, "Cabin.Window.Row1.Driver.Open"
        )
        vehicle.front_right_window_is_open = get_child_value(
            state, "Cabin.Window.Row1.Passenger.Open"
        )
        vehicle.back_left_window_is_open = get_child_value(
            state, "Cabin.Window.Row2.Left.Open"
        )
        vehicle.back_right_window_is_open = get_child_value(
            state, "Cabin.Window.Row2.Right.Open"
        )
        vehicle.tire_pressure_rear_left_warning_is_on = bool(
            get_child_value(state, "Chassis.Axle.Row2.Left.Tire.PressureLow")
        )
        vehicle.tire_pressure_front_left_warning_is_on = bool(
            get_child_value(state, "Chassis.Axle.Row1.Left.Tire.PressureLow")
        )
        vehicle.tire_pressure_front_right_warning_is_on = bool(
            get_child_value(state, "Chassis.Axle.Row1.Right.Tire.PressureLow")
        )
        vehicle.tire_pressure_rear_right_warning_is_on = bool(
            get_child_value(state, "Chassis.Axle.Row2.Right.Tire.PressureLow")
        )
        vehicle.tire_pressure_all_warning_is_on = bool(
            get_child_value(state, "Chassis.Axle.Tire.PressureLow")
        )
        vehicle.trunk_is_open = get_child_value(state, "Body.Trunk.Open")

        vehicle.ev_battery_percentage = get_child_value(
            state, "Green.BatteryManagement.BatteryRemain.Ratio"
        )
        vehicle.ev_battery_remain = get_child_value(
            state, "Green.BatteryManagement.BatteryRemain.Value"
        )
        vehicle.ev_battery_capacity = get_child_value(
            state, "Green.BatteryManagement.BatteryCapacity.Value"
        )
        vehicle.ev_battery_soh_percentage = get_child_value(
            state, "Green.BatteryManagement.SoH.Ratio"
        )
        vehicle.ev_battery_is_plugged_in = get_child_value(
            state, "Green.ChargingInformation.ElectricCurrentLevel.State"
        )
        vehicle.ev_battery_is_plugged_in = get_child_value(
            state, "Green.ChargingInformation.ConnectorFastening.State"
        )
        charging_door_state = get_child_value(state, "Green.ChargingDoor.State")
        if charging_door_state in [0, 2]:
            vehicle.ev_charge_port_door_is_open = False
        elif charging_door_state == 1:
            vehicle.ev_charge_port_door_is_open = True

        vehicle.total_driving_range = (
            float(
                get_child_value(
                    state,
                    "Drivetrain.FuelSystem.DTE.Total",  # noqa
                )
            ),
            DISTANCE_UNITS[
                get_child_value(
                    state,
                    "Drivetrain.FuelSystem.DTE.Unit",  # noqa
                )
            ],
        )

        if vehicle.engine_type == ENGINE_TYPES.EV:
            # ev_driving_range is the same as total_driving_range for pure EV
            vehicle.ev_driving_range = (
                vehicle.total_driving_range,
                vehicle.total_driving_range_unit,
            )
        # TODO: vehicle.ev_driving_range for non EV

        vehicle.washer_fluid_warning_is_on = get_child_value(
            state, "Body.Windshield.Front.WasherFluid.LevelLow"
        )

        vehicle.ev_estimated_current_charge_duration = (
            get_child_value(state, "Green.ChargingInformation.Charging.RemainTime"),
            "m",
        )
        vehicle.ev_estimated_fast_charge_duration = (
            get_child_value(state, "Green.ChargingInformation.EstimatedTime.Standard"),
            "m",
        )
        vehicle.ev_estimated_portable_charge_duration = (
            get_child_value(state, "Green.ChargingInformation.EstimatedTime.ICCB"),
            "m",
        )
        vehicle.ev_estimated_station_charge_duration = (
            get_child_value(state, "Green.ChargingInformation.EstimatedTime.Quick"),
            "m",
        )
        vehicle.ev_charge_limits_ac = get_child_value(
            state, "Green.ChargingInformation.TargetSoC.Standard"
        )
        vehicle.ev_charge_limits_dc = get_child_value(
            state, "Green.ChargingInformation.TargetSoC.Quick"
        )
        vehicle.ev_charging_current = get_child_value(
            state, "Green.ChargingInformation.ElectricCurrentLevel.State"
        )
        vehicle.ev_v2l_discharge_limit = get_child_value(
            state, "Green.Electric.SmartGrid.VehicleToLoad.DischargeLimitation.SoC"
        )
        vehicle.ev_target_range_charge_AC = (
            get_child_value(
                state,
                "Green.ChargingInformation.DTE.TargetSoC.Standard",  # noqa
            ),
            DISTANCE_UNITS[
                get_child_value(
                    state,
                    "Drivetrain.FuelSystem.DTE.Unit",  # noqa
                )
            ],
        )
        vehicle.ev_target_range_charge_DC = (
            get_child_value(
                state,
                "Green.ChargingInformation.DTE.TargetSoC.Quick",  # noqa
            ),
            DISTANCE_UNITS[
                get_child_value(
                    state,
                    "Drivetrain.FuelSystem.DTE.Unit",  # noqa
                )
            ],
        )
        vehicle.ev_first_departure_enabled = bool(
            get_child_value(state, "Green.Reservation.Departure.Schedule1.Enable")
        )

        vehicle.ev_second_departure_enabled = bool(
            get_child_value(state, "Green.Reservation.Departure.Schedule2.Enable")
        )

        # TODO: vehicle.ev_first_departure_days --> Green.Reservation.Departure.Schedule1.(Mon,Tue,Wed,Thu,Fri,Sat,Sun) # noqa
        # TODO: vehicle.ev_second_departure_days --> Green.Reservation.Departure.Schedule2.(Mon,Tue,Wed,Thu,Fri,Sat,Sun) # noqa
        # TODO: vehicle.ev_first_departure_time --> Green.Reservation.Departure.Schedule1.(Min,Hour) # noqa
        # TODO: vehicle.ev_second_departure_time --> Green.Reservation.Departure.Schedule2.(Min,Hour) # noqa
        # TODO: vehicle.ev_off_peak_charge_only_enabled --> unknown settings are in  --> Green.Reservation.OffPeakTime and OffPeakTime2 # noqa

        vehicle.washer_fluid_warning_is_on = get_child_value(
            state, "Body.Windshield.Front.WasherFluid.LevelLow"
        )
        vehicle.brake_fluid_warning_is_on = get_child_value(
            state, "Chassis.Brake.Fluid.Warning"
        )

        vehicle.fuel_level = get_child_value(state, "Drivetrain.FuelSystem.FuelLevel")
        vehicle.fuel_level_is_low = get_child_value(
            state, "Drivetrain.FuelSystem.LowFuelWarning"
        )
        vehicle.air_control_is_on = get_child_value(
            state, "Cabin.HVAC.Row1.Driver.Blower.SpeedLevel"
        )
        vehicle.smart_key_battery_warning_is_on = bool(
            get_child_value(state, "Electronics.FOB.LowBattery")
        )

        if get_child_value(state, "Location.GeoCoord.Latitude"):
            location_last_updated_at = dt.datetime(
                2000, 1, 1, tzinfo=self.data_timezone
            )
            timestamp = get_child_value(state, "Location.TimeStamp")
            if timestamp is not None:
                location_last_updated_at = dt.datetime(
                    year=int(get_child_value(timestamp, "Year")),
                    month=int(get_child_value(timestamp, "Mon")),
                    day=int(get_child_value(timestamp, "Day")),
                    hour=int(get_child_value(timestamp, "Hour")),
                    minute=int(get_child_value(timestamp, "Min")),
                    second=int(get_child_value(timestamp, "Sec")),
                    tzinfo=self.data_timezone,
                )

            vehicle.location = (
                get_child_value(state, "Location.GeoCoord.Latitude"),
                get_child_value(state, "Location.GeoCoord.Longitude"),
                location_last_updated_at,
            )

        vehicle.data = state

    def start_charge(self, token: Token, vehicle: Vehicle) -> str:
        if not vehicle.ccu_ccs2_protocol_support:
            url = self.SPA_API_URL + "vehicles/" + vehicle.id + "/control/charge"

            payload = {"action": "start", "deviceId": token.device_id}
            headers = self._get_authenticated_headers(
                token, vehicle.ccu_ccs2_protocol_support
            )

        else:
            url = (
                self.SPA_API_URL_V2 + "vehicles/" + vehicle.id + "/ccs2/control/charge"
            )

            payload = {"command": "start"}
            headers = self._get_control_headers(token, vehicle)

        _LOGGER.debug(f"{DOMAIN} - Start Charge Action Request: {payload}")
        response = requests.post(url, json=payload, headers=headers).json()
        _LOGGER.debug(f"{DOMAIN} - Start Charge Action Response: {response}")
        _check_response_for_errors(response)
        token.device_id = self._get_device_id(self._get_stamp())
        return response["msgId"]

    def stop_charge(self, token: Token, vehicle: Vehicle) -> str:
        if not vehicle.ccu_ccs2_protocol_support:
            url = self.SPA_API_URL + "vehicles/" + vehicle.id + "/control/charge"

            payload = {"action": "stop", "deviceId": token.device_id}
            headers = self._get_authenticated_headers(
                token, vehicle.ccu_ccs2_protocol_support
            )

        else:
            url = (
                self.SPA_API_URL_V2 + "vehicles/" + vehicle.id + "/ccs2/control/charge"
            )

            payload = {"command": "stop"}
            headers = self._get_control_headers(token, vehicle)

        _LOGGER.debug(f"{DOMAIN} - Stop Charge Action Request: {payload}")
        response = requests.post(url, json=payload, headers=headers).json()
        _LOGGER.debug(f"{DOMAIN} - Stop Charge Action Response: {response}")
        _check_response_for_errors(response)
        token.device_id = self._get_device_id(self._get_stamp())
        return response["msgId"]

    def set_charging_current(self, token: Token, vehicle: Vehicle, level: int) -> str:
        url = (
            self.SPA_API_URL + "vehicles/" + vehicle.id + "/ccs2/charge/chargingcurrent"
        )

        body = {"chargingCurrent": level}
        response = requests.post(
            url,
            json=body,
            headers=self._get_authenticated_headers(
                token, vehicle.ccu_ccs2_protocol_support
            ),
        ).json()
        _LOGGER.debug(f"{DOMAIN} - Set Charging Current Response: {response}")
        _check_response_for_errors(response)
        return response["msgId"]
