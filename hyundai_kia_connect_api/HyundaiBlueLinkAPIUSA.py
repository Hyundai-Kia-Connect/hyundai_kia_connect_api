"""HyundaiBlueLinkAPIUSA.py"""

# pylint:disable=logging-fstring-interpolation,deprecated-method,invalid-name,broad-exception-caught,unused-argument,missing-function-docstring

import logging
import time
import datetime as dt
import pytz
import requests
import certifi

from requests.adapters import HTTPAdapter
from urllib3.util.ssl_ import create_urllib3_context

from .const import (
    DOMAIN,
    VEHICLE_LOCK_ACTION,
    SEAT_STATUS,
    DISTANCE_UNITS,
    TEMPERATURE_UNITS,
    ENGINE_TYPES,
)
from .utils import get_child_value, get_float, parse_datetime
from .ApiImpl import ApiImpl, ClimateRequestOptions
from .Token import Token
from .Vehicle import (
    DailyDrivingStats,
    DayTripCounts,
    DayTripInfo,
    MonthTripInfo,
    TripInfo,
    Vehicle,
)


CIPHERS = "DEFAULT@SECLEVEL=1"

_LOGGER = logging.getLogger(__name__)


class cipherAdapter(HTTPAdapter):
    """
    A HTTPAdapter that re-enables poor ciphers required by Hyundai.
    """

    def _setup_ssl_context(self):
        context = create_urllib3_context(ciphers=CIPHERS)
        context.options |= 0x4

        return context

    def init_poolmanager(self, *args, **kwargs):
        kwargs["ssl_context"] = self._setup_ssl_context()
        kwargs["ca_certs"] = certifi.where()
        return super().init_poolmanager(*args, **kwargs)

    def proxy_manager_for(self, *args, **kwargs):
        kwargs["ssl_context"] = self._setup_ssl_context()

        return super().proxy_manager_for(*args, **kwargs)


class HyundaiBlueLinkAPIUSA(ApiImpl):
    """HyundaiBlueLinkAPIUSA"""

    # initialize with a timestamp which will allow the first fetch to occur
    last_loc_timestamp = dt.datetime.now(pytz.utc) - dt.timedelta(hours=3)

    def __init__(self, region: int, brand: int, language: str):
        self.LANGUAGE: str = language
        self.BASE_URL: str = "api.telematics.hyundaiusa.com"
        self.LOGIN_API: str = "https://" + self.BASE_URL + "/v2/ac/"
        self.API_URL: str = "https://" + self.BASE_URL + "/ac/v2/"
        self.temperature_range = range(62, 82)

        ts = time.time()
        utc_offset = (
            dt.datetime.fromtimestamp(ts) - dt.datetime.utcfromtimestamp(ts)
        ).total_seconds()
        utc_offset_hours = int(utc_offset / 60 / 60)

        origin: str = "https://" + self.BASE_URL
        referer: str = origin + "/login"

        self.API_HEADERS = {
            "content-type": "application/json;charset=UTF-8",
            "accept": "application/json, text/plain, */*",
            "accept-encoding": "gzip, deflate, br",
            "accept-language": "en-US,en;q=0.9",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/75.0.3770.142 Safari/537.36",  # noqa
            "host": self.BASE_URL,
            "origin": origin,
            "referer": referer,
            "from": "SPA",
            "to": "ISS",
            "language": "0",
            "offset": str(utc_offset_hours),
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "refresh": "false",
            "encryptFlag": "false",
            "brandIndicator": "H",
            "gen": "2",
            "client_id": "m66129Bb-em93-SPAHYN-bZ91-am4540zp19920",
            "clientSecret": "v558o935-6nne-423i-baa8",
        }
        self.sessions = requests.Session()
        self.sessions.mount(origin, cipherAdapter())

        _LOGGER.debug(f"{DOMAIN} - initial API headers: {self.API_HEADERS}")

    def _get_authenticated_headers(self, token: Token) -> dict:
        headers = dict(self.API_HEADERS)
        headers["username"] = token.username
        headers["accessToken"] = token.access_token
        headers["blueLinkServicePin"] = token.pin
        return headers

    def _get_vehicle_headers(self, token: Token, vehicle: Vehicle) -> dict:
        headers = self._get_authenticated_headers(token)
        headers["registrationId"] = vehicle.id
        headers["vin"] = vehicle.VIN
        return headers

    def login(self, username: str, password: str) -> Token:
        # Sign In with Email and Password and Get Authorization Code
        url = self.LOGIN_API + "oauth/token"
        data = {"username": username, "password": password}

        response = self.sessions.post(url, json=data, headers=self.API_HEADERS)
        _LOGGER.debug(f"{DOMAIN} - Sign In Response {response.text}")
        response = response.json()
        access_token = response["access_token"]
        refresh_token = response["refresh_token"]
        expires_in = float(response["expires_in"])
        _LOGGER.debug(f"{DOMAIN} - Access Token Value {access_token}")
        _LOGGER.debug(f"{DOMAIN} - Refresh Token Value {refresh_token}")

        valid_until = dt.datetime.now(pytz.utc) + dt.timedelta(seconds=expires_in)

        return Token(
            username=username,
            password=password,
            access_token=access_token,
            refresh_token=refresh_token,
            valid_until=valid_until,
        )

    def _get_vehicle_details(self, token: Token, vehicle: Vehicle):
        url = self.API_URL + "enrollment/details/" + token.username
        headers = self._get_authenticated_headers(token)
        response = self.sessions.get(url, headers=headers)
        _LOGGER.debug(f"{DOMAIN} - Get Vehicles Response {response.text}")
        response = response.json()
        for entry in response["enrolledVehicleDetails"]:
            entry = entry["vehicleDetails"]
            if entry["regid"] == vehicle.id:
                return entry

    def _get_vehicle_status(
        self, token: Token, vehicle: Vehicle, refresh: bool
    ) -> dict:
        # Vehicle Status Call
        url = self.API_URL + "rcs/rvs/vehicleStatus"
        headers = self._get_vehicle_headers(token, vehicle)
        if refresh:
            headers["REFRESH"] = "true"

        _LOGGER.debug(f"{DOMAIN} - using API headers: {headers}")

        response = self.sessions.get(url, headers=headers)
        response = response.json()
        _LOGGER.debug(f"{DOMAIN} - get_vehicle_status response {response}")

        status = dict(response["vehicleStatus"])

        status["dateTime"] = (
            status["dateTime"]
            .replace("-", "")
            .replace("T", "")
            .replace(":", "")
            .replace("Z", "")
        )

        return status

    def _get_ev_trip_details(self, token: Token, vehicle: Vehicle) -> dict:
        if vehicle.engine_type != ENGINE_TYPES.EV:
            return {}

        url = self.API_URL + "ts/alerts/maintenance/evTripDetails"
        headers = self._get_vehicle_headers(token, vehicle)
        headers["userId"] = headers["username"]
        # This header is sent by the MyHyundai app, but doesn't seem to do anything
        # headers["offset"] = "-5"

        _LOGGER.debug(f"{DOMAIN} - using API headers: {headers}")

        response = self.sessions.get(url, headers=headers)
        response = response.json()
        _LOGGER.debug(f"{DOMAIN} - get_ev_trip_details response {response}")

        return response

    def _get_vehicle_location(self, token: Token, vehicle: Vehicle):
        """
        Get the location of the vehicle
        This logic only checks odometer move in the update.
        This call doesn't protect from overlimit as per:
        Only update the location if the odometer moved AND if the last location
        update was over an hour ago.
        Note that the "last updated" time is initially set to three hours ago.
        This will help to prevent too many calls to the API
        """
        url = self.API_URL + "rcs/rfc/findMyCar"
        headers = self._get_vehicle_headers(token, vehicle)
        try:
            response = self.sessions.get(url, headers=headers)
            response_json = response.json()
            _LOGGER.debug(f"{DOMAIN} - Get Vehicle Location {response_json}")
            if response_json.get("coord") is not None:
                return response_json
            else:
                if (
                    response_json.get("errorCode", 0) == 502
                    and response_json.get("errorSubCode", "") == "HT_534"
                ):
                    _LOGGER.warn(
                        f"{DOMAIN} - get vehicle location rate limit exceeded."
                    )
                else:
                    _LOGGER.warn(
                        f"{DOMAIN} - Unable to get vehicle location: {response_json}"
                    )

        except Exception as e:
            _LOGGER.warning(
                f"{DOMAIN} - Get vehicle location failed: {e}", exc_info=True
            )

        _LOGGER.debug(f"{DOMAIN} - Get Vehicle Location result is None")
        return None

    def _update_vehicle_properties(self, vehicle: Vehicle, state: dict) -> None:
        vehicle.last_updated_at = parse_datetime(
            get_child_value(state, "vehicleStatus.dateTime"), self.data_timezone
        )
        vehicle.total_driving_range = (
            get_child_value(
                state,
                "vehicleStatus.evStatus.drvDistance.0.rangeByFuel.totalAvailableRange.value",  # noqa
            ),
            DISTANCE_UNITS[
                get_child_value(
                    state,
                    "vehicleStatus.evStatus.drvDistance.0.rangeByFuel.totalAvailableRange.unit",  # noqa
                )
            ],
        )
        if get_child_value(
            state,
            "vehicleStatus.dte.value",
        ):
            vehicle.fuel_driving_range = (
                get_child_value(
                    state,
                    "vehicleStatus.dte.value",
                ),
                DISTANCE_UNITS[
                    get_child_value(
                        state,
                        "vehicleStatus.dte.unit",
                    )
                ],
            )
        vehicle.odometer = (
            get_child_value(state, "vehicleDetails.odometer"),
            DISTANCE_UNITS[3],
        )
        vehicle.car_battery_percentage = get_child_value(
            state, "vehicleStatus.battery.batSoc"
        )
        vehicle.engine_is_running = get_child_value(state, "vehicleStatus.engine")
        vehicle.washer_fluid_warning_is_on = get_child_value(
            state, "vehicleStatus.washerFluidStatus"
        )
        vehicle.brake_fluid_warning_is_on = get_child_value(
            state, "vehicleStatus.breakOilStatus"
        )
        vehicle.smart_key_battery_warning_is_on = get_child_value(
            state, "vehicleStatus.smartKeyBatteryWarning"
        )

        air_temp = get_child_value(state, "vehicleStatus.airTemp.value")

        if air_temp == "LO":
            air_temp = self.temperature_range[0]
        if air_temp == "HI":
            air_temp = self.temperature_range[-1]
        if air_temp:
            vehicle.air_temperature = (air_temp, TEMPERATURE_UNITS[1])
        vehicle.defrost_is_on = get_child_value(state, "vehicleStatus.defrost")
        vehicle.steering_wheel_heater_is_on = get_child_value(
            state, "vehicleStatus.steerWheelHeat"
        )
        vehicle.back_window_heater_is_on = get_child_value(
            state, "vehicleStatus.sideBackWindowHeat"
        )
        vehicle.side_mirror_heater_is_on = get_child_value(
            state, "vehicleStatus.sideMirrorHeat"
        )
        vehicle.front_left_seat_status = SEAT_STATUS[
            get_child_value(state, "vehicleStatus.seatHeaterVentState.flSeatHeatState")
        ]
        vehicle.front_right_seat_status = SEAT_STATUS[
            get_child_value(state, "vehicleStatus.seatHeaterVentState.frSeatHeatState")
        ]
        vehicle.rear_left_seat_status = SEAT_STATUS[
            get_child_value(state, "vehicleStatus.seatHeaterVentState.rlSeatHeatState")
        ]
        vehicle.rear_right_seat_status = SEAT_STATUS[
            get_child_value(state, "vehicleStatus.seatHeaterVentState.rrSeatHeatState")
        ]
        vehicle.tire_pressure_rear_left_warning_is_on = bool(
            get_child_value(
                state, "vehicleStatus.tirePressureLamp.tirePressureWarningLampRearLeft"
            )
        )
        vehicle.tire_pressure_front_left_warning_is_on = bool(
            get_child_value(
                state, "vehicleStatus.tirePressureLamp.tirePressureWarningLampFrontLeft"
            )
        )
        vehicle.tire_pressure_front_right_warning_is_on = bool(
            get_child_value(
                state,
                "vehicleStatus.tirePressureLamp.tirePressureWarningLampFrontRight",
            )
        )
        vehicle.tire_pressure_rear_right_warning_is_on = bool(
            get_child_value(
                state, "vehicleStatus.tirePressureLamp.tirePressureWarningLampRearRight"
            )
        )
        vehicle.tire_pressure_all_warning_is_on = bool(
            get_child_value(
                state, "vehicleStatus.tirePressureLamp.tirePressureWarningLampAll"
            )
        )
        vehicle.front_left_window_is_open = get_child_value(
            state, "vehicleStatus.windowOpen.frontLeft"
        )
        vehicle.front_right_window_is_open = get_child_value(
            state, "vehicleStatus.windowOpen.frontRight"
        )
        vehicle.back_left_window_is_open = get_child_value(
            state, "vehicleStatus.windowOpen.backLeft"
        )
        vehicle.back_right_window_is_open = get_child_value(
            state, "vehicleStatus.windowOpen.backRight"
        )
        vehicle.is_locked = get_child_value(state, "vehicleStatus.doorLock")
        vehicle.front_left_door_is_open = get_child_value(
            state, "vehicleStatus.doorOpen.frontLeft"
        )
        vehicle.front_right_door_is_open = get_child_value(
            state, "vehicleStatus.doorOpen.frontRight"
        )
        vehicle.back_left_door_is_open = get_child_value(
            state, "vehicleStatus.doorOpen.backLeft"
        )
        vehicle.back_right_door_is_open = get_child_value(
            state, "vehicleStatus.doorOpen.backRight"
        )
        vehicle.hood_is_open = get_child_value(state, "vehicleStatus.hoodOpen")
        vehicle.trunk_is_open = get_child_value(state, "vehicleStatus.trunkOpen")
        vehicle.ev_battery_percentage = get_child_value(
            state, "vehicleStatus.evStatus.batteryStatus"
        )
        vehicle.ev_battery_is_charging = get_child_value(
            state, "vehicleStatus.evStatus.batteryCharge"
        )
        vehicle.ev_battery_is_plugged_in = get_child_value(
            state, "vehicleStatus.evStatus.batteryPlugin"
        )
        ChargeDict = get_child_value(
            state, "vehicleStatus.evStatus.reservChargeInfos.targetSOClist"
        )
        try:
            vehicle.ev_charge_limits_ac = [
                x["targetSOClevel"] for x in ChargeDict if x["plugType"] == 1
            ][-1]
            vehicle.ev_charge_limits_dc = [
                x["targetSOClevel"] for x in ChargeDict if x["plugType"] == 0
            ][-1]
        except Exception:
            _LOGGER.debug(f"{DOMAIN} - SOC Levels couldn't be found. May not be an EV.")

        vehicle.ev_driving_range = (
            get_child_value(
                state,
                "vehicleStatus.evStatus.drvDistance.0.rangeByFuel.evModeRange.value",
            ),
            DISTANCE_UNITS[
                get_child_value(
                    state,
                    "vehicleStatus.evStatus.drvDistance.0.rangeByFuel.evModeRange.unit",
                )
            ],
        )
        vehicle.ev_estimated_current_charge_duration = (
            get_child_value(state, "vehicleStatus.evStatus.remainTime2.atc.value"),
            "m",
        )
        vehicle.ev_estimated_fast_charge_duration = (
            get_child_value(state, "vehicleStatus.evStatus.remainTime2.etc1.value"),
            "m",
        )
        vehicle.ev_estimated_portable_charge_duration = (
            get_child_value(state, "vehicleStatus.evStatus.remainTime2.etc2.value"),
            "m",
        )
        vehicle.ev_estimated_station_charge_duration = (
            get_child_value(state, "vehicleStatus.evStatus.remainTime2.etc3.value"),
            "m",
        )
        if get_child_value(
            state,
            "vehicleStatus.evStatus.drvDistance.0.rangeByFuel.gasModeRange.value",
        ):
            vehicle.fuel_driving_range = (
                get_child_value(
                    state,
                    "vehicleStatus.evStatus.drvDistance.0.rangeByFuel.gasModeRange.value",  # noqa
                ),
                DISTANCE_UNITS[
                    get_child_value(
                        state,
                        "vehicleStatus.evStatus.drvDistance.0.rangeByFuel.gasModeRange.unit",  # noqa
                    )
                ],
            )
        vehicle.fuel_level_is_low = get_child_value(state, "vehicleStatus.lowFuelLight")

        vehicle.fuel_level = get_child_value(state, "vehicleStatus.fuelLevel")

        if get_child_value(state, "vehicleStatus.vehicleLocation.coord.lat"):
            vehicle.location = (
                get_child_value(state, "vehicleStatus.vehicleLocation.coord.lat"),
                get_child_value(state, "vehicleStatus.vehicleLocation.coord.lon"),
                parse_datetime(
                    get_child_value(state, "vehicleStatus.vehicleLocation.time"),
                    self.data_timezone,
                ),
            )
        vehicle.air_control_is_on = get_child_value(state, "vehicleStatus.airCtrlOn")

        # fill vehicle.daily_stats
        tripStats = []
        tripDetails = get_child_value(state, "evTripDetails.tripdetails") or {}
        for trip in tripDetails:
            processedTrip = DailyDrivingStats(
                date=dt.datetime.strptime(trip["startdate"], "%Y-%m-%d %H:%M:%S.%f"),
                total_consumed=get_child_value(trip, "totalused"),
                engine_consumption=get_child_value(trip, "drivetrain"),
                climate_consumption=get_child_value(trip, "climate"),
                onboard_electronics_consumption=get_child_value(trip, "accessories"),
                battery_care_consumption=get_child_value(trip, "batterycare"),
                regenerated_energy=get_child_value(trip, "regen"),
                distance=get_child_value(trip, "distance"),
                distance_unit=vehicle.odometer_unit,
            )
            tripStats.append(processedTrip)

        vehicle.daily_stats = tripStats

        # remember trips, store state
        trips = []
        for trip in tripDetails:
            yyyymmdd_hhmmss = trip["startdate"]  # remember full date
            drive_time = int(get_child_value(trip["mileagetime"], "value"))
            idle_time = int(get_child_value(trip["duration"], "value")) - drive_time
            processed_trip = TripInfo(
                hhmmss=yyyymmdd_hhmmss,
                drive_time=int(drive_time / 60),  # convert seconds to minutes
                idle_time=int(idle_time / 60),  # convert seconds to minutes
                distance=int(trip["distance"]),
                avg_speed=get_child_value(trip["avgspeed"], "value"),
                max_speed=int(get_child_value(trip["maxspeed"], "value")),
            )
            trips.append(processed_trip)

        _LOGGER.debug(f"_update_vehicle_properties filled_trips: {trips}")
        if len(trips) > 0:
            state["filled_trips"] = trips

        vehicle.data = state

    def update_month_trip_info(
        self,
        token,
        vehicle,
        yyyymm_string,
    ) -> None:
        """
        feature only available for some regions.
        Updates the vehicle.month_trip_info for the specified month.

        Default this information is None:

        month_trip_info: MonthTripInfo = None
        """
        _LOGGER.debug(f"update_month_trip_info: {yyyymm_string}")
        vehicle.month_trip_info = None

        if vehicle.data is None or "filled_trips" not in vehicle.data:
            _LOGGER.debug(f"filled_trips is empty: {vehicle.data}")
            return  # nothing to fill

        trips = vehicle.data["filled_trips"]

        month_trip_info: MonthTripInfo = None
        month_trip_info_count = 0

        for trip in trips:
            date_str = trip.hhmmss
            yyyymm = date_str[0:4] + date_str[5:7]
            if yyyymm == yyyymm_string:
                if month_trip_info_count == 0:
                    month_trip_info = MonthTripInfo(
                        yyyymm=yyyymm_string,
                        summary=TripInfo(
                            drive_time=trip.drive_time,
                            idle_time=trip.idle_time,
                            distance=trip.distance,
                            avg_speed=trip.avg_speed,
                            max_speed=trip.max_speed,
                        ),
                        day_list=[],
                    )
                    month_trip_info_count = 1
                else:
                    # increment totals for month (for the few trips available)
                    month_trip_info_count += 1
                    summary = month_trip_info.summary
                    summary.drive_time += trip.drive_time
                    summary.idle_time += trip.idle_time
                    summary.distance += trip.distance
                    summary.avg_speed += trip.avg_speed
                    summary.max_speed = max(trip.max_speed, summary.max_speed)

                month_trip_info.summary.avg_speed /= month_trip_info_count
                month_trip_info.summary.avg_speed = round(
                    month_trip_info.summary.avg_speed, 1
                )

                # also fill DayTripCount
                yyyymmdd = yyyymm + date_str[8:10]
                day_trip_found = False
                for day in month_trip_info.day_list:
                    if day.yyyymmdd == yyyymmdd:
                        day.trip_count += 1
                        day_trip_found = True

                if not day_trip_found:
                    month_trip_info.day_list.append(
                        DayTripCounts(yyyymmdd=yyyymmdd, trip_count=1)
                    )

        vehicle.month_trip_info = month_trip_info

    def update_day_trip_info(
        self,
        token,
        vehicle,
        yyyymmdd_string,
    ) -> None:
        """
        feature only available for some regions.
        Updates the vehicle.day_trip_info information for the specified day.

        Default this information is None:

        day_trip_info: DayTripInfo = None
        """
        _LOGGER.debug(f"update_day_trip_info: {yyyymmdd_string}")
        vehicle.day_trip_info = None

        if vehicle.data is None or "filled_trips" not in vehicle.data:
            _LOGGER.debug(f"filled_trips is empty: {vehicle.data}")
            return  # nothing to fill

        trips = vehicle.data["filled_trips"]
        _LOGGER.debug(f"filled_trips: {trips}")

        day_trip_info: DayTripInfo = None
        day_trip_info_count = 0

        for trip in trips:
            date_str = trip.hhmmss
            yyyymmdd = date_str[0:4] + date_str[5:7] + date_str[8:10]
            _LOGGER.debug(f"update_day_trip_info: {yyyymmdd} trip: {trip}")
            if yyyymmdd == yyyymmdd_string:
                if day_trip_info_count == 0:
                    day_trip_info = DayTripInfo(
                        yyyymmdd=yyyymmdd_string,
                        summary=TripInfo(
                            drive_time=trip.drive_time,
                            idle_time=trip.idle_time,
                            distance=trip.distance,
                            avg_speed=trip.avg_speed,
                            max_speed=trip.max_speed,
                        ),
                        trip_list=[],
                    )
                    day_trip_info_count = 1
                else:
                    # increment totals for month (for the few trips available)
                    day_trip_info_count += 1
                    summary = day_trip_info.summary
                    summary.drive_time += trip.drive_time
                    summary.idle_time += trip.idle_time
                    summary.distance += trip.distance
                    summary.avg_speed += trip.avg_speed
                    summary.max_speed = max(trip.max_speed, summary.max_speed)

                day_trip_info.summary.avg_speed /= day_trip_info_count
                day_trip_info.summary.avg_speed = round(
                    day_trip_info.summary.avg_speed, 1
                )

                # also fill TripInfo
                hhmmss = date_str[11:13] + date_str[14:16] + date_str[17:19]
                day_trip_info.trip_list.append(
                    TripInfo(
                        hhmmss=hhmmss,
                        drive_time=trip.drive_time,
                        idle_time=trip.idle_time,
                        distance=trip.distance,
                        avg_speed=trip.avg_speed,
                        max_speed=trip.max_speed,
                    )
                )
                _LOGGER.debug(
                    f"update_day_trip_info: trip_list result: {day_trip_info.trip_list}"
                )

        vehicle.day_trip_info = day_trip_info

    def update_vehicle_with_cached_state(self, token: Token, vehicle: Vehicle) -> None:
        state = {}
        state["vehicleDetails"] = self._get_vehicle_details(token, vehicle)
        state["vehicleStatus"] = self._get_vehicle_status(token, vehicle, False)
        state["evTripDetails"] = self._get_ev_trip_details(token, vehicle)

        if state["vehicleStatus"] is not None:
            vehicle_location_result = None
            if vehicle.odometer:
                if vehicle.odometer < get_float(
                    get_child_value(state["vehicleDetails"], "odometer")
                ):
                    vehicle_location_result = self._get_vehicle_location(token, vehicle)
                else:
                    cached_location = state["vehicleStatus"]["vehicleLocation"]
                    _LOGGER.debug(
                        f"{DOMAIN} - update_vehicle_with_cached_state keep Location fallback {cached_location}"  # noqa
                    )
            else:
                vehicle_location_result = self._get_vehicle_location(token, vehicle)

            if vehicle_location_result is not None:
                state["vehicleStatus"]["vehicleLocation"] = vehicle_location_result
            else:
                cached_location = state["vehicleStatus"]["vehicleLocation"]
                _LOGGER.debug(
                    f"{DOMAIN} - update_vehicle_with_cached_state Location fallback {cached_location}"  # noqa
                )

        self._update_vehicle_properties(vehicle, state)

    def force_refresh_vehicle_state(self, token: Token, vehicle: Vehicle) -> None:
        state = {}
        state["vehicleDetails"] = self._get_vehicle_details(token, vehicle)
        state["vehicleStatus"] = self._get_vehicle_status(token, vehicle, True)
        state["evTripDetails"] = self._get_ev_trip_details(token, vehicle)

        if state["vehicleStatus"] is not None:
            vehicle_location_result = self._get_vehicle_location(token, vehicle)
            if vehicle_location_result is not None:
                state["vehicleStatus"]["vehicleLocation"] = vehicle_location_result
            else:
                cached_location = state["vehicleStatus"]["vehicleLocation"]
                _LOGGER.debug(
                    f"{DOMAIN} - force_refresh_vehicle_state Location fallback {cached_location}"  # noqa
                )

        self._update_vehicle_properties(vehicle, state)

    def get_vehicles(self, token: Token):
        url = self.API_URL + "enrollment/details/" + token.username
        headers = self._get_authenticated_headers(token)
        response = self.sessions.get(url, headers=headers)
        _LOGGER.debug(f"{DOMAIN} - Get Vehicles Response {response.text}")
        response = response.json()
        result = []
        for entry in response["enrolledVehicleDetails"]:
            entry = entry["vehicleDetails"]
            entry_engine_type = None
            if entry["evStatus"] == "N":
                entry_engine_type = ENGINE_TYPES.ICE
            elif entry["evStatus"] == "E":
                entry_engine_type = ENGINE_TYPES.EV
            vehicle: Vehicle = Vehicle(
                id=entry["regid"],
                name=entry["nickName"],
                VIN=entry["vin"],
                engine_type=entry_engine_type,
                model=entry["modelCode"],
                registration_date=["enrollmentDate"],
                timezone=self.data_timezone,
            )
            result.append(vehicle)

        return result

    def lock_action(self, token: Token, vehicle: Vehicle, action) -> None:
        _LOGGER.debug(f"{DOMAIN} - Action for lock is: {action}")

        if action == VEHICLE_LOCK_ACTION.LOCK:
            url = self.API_URL + "rcs/rdo/off"
            _LOGGER.debug(f"{DOMAIN} - Calling Lock")
        elif action == VEHICLE_LOCK_ACTION.UNLOCK:
            url = self.API_URL + "rcs/rdo/on"
            _LOGGER.debug(f"{DOMAIN} - Calling unlock")

        headers = self._get_vehicle_headers(token, vehicle)
        headers["APPCLOUD-VIN"] = vehicle.VIN

        data = {"userName": token.username, "vin": vehicle.VIN}
        response = self.sessions.post(url, headers=headers, json=data)
        # response_headers = response.headers
        # response = response.json()
        # action_status = self.check_action_status(token, headers["pAuth"], response_headers["transactionId"])  # noqa

        # _LOGGER.debug(f"{DOMAIN} - Received lock_action response {action_status}")
        _LOGGER.debug(
            f"{DOMAIN} - Received lock_action response status code: {response.status_code}"  # noqa
        )
        _LOGGER.debug(f"{DOMAIN} - Received lock_action response: {response.text}")

    def start_climate(
        self, token: Token, vehicle: Vehicle, options: ClimateRequestOptions
    ) -> str:
        _LOGGER.debug(f"{DOMAIN} - Start engine..")
        if vehicle.engine_type == ENGINE_TYPES.EV:
            url = self.API_URL + "evc/fatc/start"
        else:
            url = self.API_URL + "rcs/rsc/start"

        headers = self._get_vehicle_headers(token, vehicle)
        _LOGGER.debug(f"{DOMAIN} - Start engine headers: {headers}")

        if options.climate is None:
            options.climate = True
        if options.set_temp is None:
            options.set_temp = 70
        if options.duration is None:
            options.duration = 5
        if options.heating is None:
            options.heating = 0
        if options.defrost is None:
            options.defrost = False
        if options.front_left_seat is None:
            options.front_left_seat = 0
        if options.front_right_seat is None:
            options.front_right_seat = 0
        if options.rear_left_seat is None:
            options.rear_left_seat = 0
        if options.rear_right_seat is None:
            options.rear_right_seat = 0

        if vehicle.engine_type == ENGINE_TYPES.EV:
            data = {
                "airCtrl": int(options.climate),
                "airTemp": {"value": str(options.set_temp), "unit": 1},
                "defrost": options.defrost,
                "heating1": int(options.heating),
            }
        else:
            data = {
                "Ims": 0,
                "airCtrl": int(options.climate),
                "airTemp": {"unit": 1, "value": options.set_temp},
                "defrost": options.defrost,
                "heating1": int(options.heating),
                "igniOnDuration": options.duration,
                "seatHeaterVentInfo": {
                    "drvSeatHeatState": options.front_left_seat,
                    "astSeatHeatState": options.front_right_seat,
                    "rlSeatHeatState": options.rear_left_seat,
                    "rrSeatHeatState": options.rear_right_seat,
                },
                "username": token.username,
                "vin": vehicle.id,
            }
        _LOGGER.debug(f"{DOMAIN} - Start engine data: {data}")

        response = self.sessions.post(url, json=data, headers=headers)
        _LOGGER.debug(
            f"{DOMAIN} - Start engine response status code: {response.status_code}"
        )
        _LOGGER.debug(f"{DOMAIN} - Start engine response: {response.text}")

    def stop_climate(self, token: Token, vehicle: Vehicle) -> None:
        _LOGGER.debug(f"{DOMAIN} - Stop engine..")

        if vehicle.engine_type == ENGINE_TYPES.EV:
            url = self.API_URL + "evc/fatc/stop"
        else:
            url = self.API_URL + "rcs/rsc/stop"

        headers = self._get_vehicle_headers(token, vehicle)

        _LOGGER.debug(f"{DOMAIN} - Stop engine headers: {headers}")

        response = self.sessions.post(url, headers=headers)
        _LOGGER.debug(
            f"{DOMAIN} - Stop engine response status code: {response.status_code}"
        )
        _LOGGER.debug(f"{DOMAIN} - Stop engine response: {response.text}")

    def start_charge(self, token: Token, vehicle: Vehicle) -> None:
        if vehicle.engine_type != ENGINE_TYPES.EV:
            return {}

        _LOGGER.debug(f"{DOMAIN} - Start charging..")

        url = self.API_URL + "evc/charge/start"
        headers = self._get_vehicle_headers(token, vehicle)
        _LOGGER.debug(f"{DOMAIN} - Start charging headers: {headers}")

        response = self.sessions.post(url, headers=headers)
        _LOGGER.debug(
            f"{DOMAIN} - Start charge response status code: {response.status_code}"
        )
        _LOGGER.debug(f"{DOMAIN} - Start charge response: {response.text}")

    def stop_charge(self, token: Token, vehicle: Vehicle) -> None:
        if vehicle.engine_type != ENGINE_TYPES.EV:
            return {}

        _LOGGER.debug(f"{DOMAIN} - Stop charging..")

        url = self.API_URL + "evc/charge/stop"
        headers = self._get_vehicle_headers(token, vehicle)
        _LOGGER.debug(f"{DOMAIN} - Stop charging headers: {headers}")

        response = self.sessions.post(url, headers=headers)
        _LOGGER.debug(
            f"{DOMAIN} - Stop charge response status code: {response.status_code}"
        )
        _LOGGER.debug(f"{DOMAIN} - Stop charge response: {response.text}")

    def set_charge_limits(
        self, token: Token, vehicle: Vehicle, ac: int, dc: int
    ) -> str:
        if vehicle.engine_type != ENGINE_TYPES.EV:
            return {}

        _LOGGER.debug(f"{DOMAIN} - Setting charge limits..")
        url = self.API_URL + "evc/charge/targetsoc/set"
        headers = self._get_vehicle_headers(token, vehicle)
        _LOGGER.debug(f"{DOMAIN} - Setting charge limits: {headers}")

        data = {
            "targetSOClist": [
                {
                    "plugType": 0,
                    "targetSOClevel": int(dc),
                },
                {
                    "plugType": 1,
                    "targetSOClevel": int(ac),
                },
            ]
        }

        _LOGGER.debug(f"{DOMAIN} - Setting charge limits body: {data}")

        response = self.sessions.post(url, json=data, headers=headers)
        _LOGGER.debug(
            f"{DOMAIN} - Setting charge limits response status code: {response.status_code}"  # noqa
        )
        _LOGGER.debug(f"{DOMAIN} - Setting charge limits: {response.text}")
