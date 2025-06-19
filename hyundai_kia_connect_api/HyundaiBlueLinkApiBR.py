"""HyundaiBlueLinkAPIUSA.py"""

from datetime import date, datetime, timedelta
import pytz
import requests
import certifi
from urllib.parse import urljoin, urlparse

from .Vehicle import (
    Vehicle,
    CachedVehicleState,
    TripInfo,
    TripDayListItem,
    VehicleLocation,
    TripPeriodType,
    MonthTripInfo,
    DayTripCounts,
    DayTripInfo,
)

from requests.adapters import HTTPAdapter
from urllib3.util.ssl_ import create_urllib3_context

from hyundai_kia_connect_api.exceptions import APIError
from hyundai_kia_connect_api.lib.date import (
    date_string_to_datetime,
    date_to_year_month,
    date_to_year_month_day,
)
from hyundai_kia_connect_api.lib.logging import get_logger

from .const import (
    BRAND_HYUNDAI,
    BRANDS,
    DISTANCE_UNITS,
    DOMAIN,
    SEAT_STATUS,
    EngineType,
)
from .utils import get_child_value, parse_datetime
from .ApiImpl import ApiImpl
from .Token import Token


CIPHERS = "DEFAULT@SECLEVEL=1"

logger = get_logger(f"{DOMAIN}/BR")


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


class HyundaiBlueLinkApiBR(ApiImpl):
    # initialize with a timestamp which will allow the first fetch to occur
    last_loc_timestamp = datetime.now(pytz.utc) - timedelta(hours=3)

    def __init__(self, region: int, brand: int, language: str | None = "en-US"):
        if BRANDS[brand] != BRAND_HYUNDAI:
            raise APIError(f"Unknown brand {BRANDS[brand]} for region {region}.")

        self.language: str = language
        self.base_url: str = "br-ccapi.hyundai.com.br"
        self.api_url: str = "https://" + self.base_url + "/api/v1/"
        self.ccsp_device_id = "c6e5815b-3057-4e5e-95d5-e3d5d1d2093e"
        self.ccsp_service_id = "03f7df9b-7626-4853-b7bd-ad1e8d722bd5"
        self.ccsp_application_id = "513a491a-0d7c-4d6a-ac03-a2df127d73b0"
        # NOTE: this is a base64 encoded string with the service_id and some unknown string (service_id:{unknown})
        self.basic_authorization_header = "Basic MDNmN2RmOWItNzYyNi00ODUzLWI3YmQtYWQxZThkNzIyYmQ1OnlRejJiYzZDbjhPb3ZWT1I3UkRXd3hUcVZ3V0czeUtCWUZEZzBIc09Yc3l4eVBsSA=="
        self.api_headers = {
            "Content-Type": "application/json; charset=UTF-8",
            "Accept": "application/json, text/plain, */*",
            "Accept-Encoding": "br;q=1.0, gzip;q=0.9, deflare;q=0.8",
            "Accept-Language": "en-BR;q=1.0",
            "User-Agent": "BR_BlueLink/1.0.14 (com.hyundai.bluelink.br; build:10132; iOS 18.4.0) Alamofire/5.9.1",
            "Host": self.base_url,
            "offset": "-3",
            "ccuCCS2ProtocolSupport": "0",
        }
        self.session = requests.Session()
        self.session.mount(f"https://{self.base_url}", cipherAdapter())
        logger.debug("Initial API headers: %s", self.api_headers)

        self.temperature_range = range(62, 82)

    def _get_authenticated_headers(self, token: Token) -> dict:
        headers = dict(self.api_headers)
        headers["ccsp-device-id"] = self.ccsp_device_id
        headers["ccsp-application-id"] = self.ccsp_application_id
        headers["Authorization"] = f"Bearer {token.access_token}"
        return headers

    def _get_vehicle_headers(self, token: Token, vehicle: Vehicle) -> dict:
        headers = self._get_authenticated_headers(token)
        headers["registrationId"] = vehicle.id
        headers["gen"] = vehicle.generation
        headers["vin"] = vehicle.VIN
        return headers

    def login(self, username: str, password: str) -> Token:
        """
        The Brazilian Hyundai login API is divided in two parts.

        It first requires us to start the login process by sending a POST request to `/user/signin`,
        then, it will return a `redirectUrl` property that we'll need to use to request the access token.

        Because of that, we do 2 API requests and do not use the default headers.
        """
        cookies = self._get_cookies()
        authorization_code = self._get_authorization_code(cookies, username, password)
        auth_response = self._get_auth_response(authorization_code)
        expires_in_seconds = auth_response["expires_in"]
        expires_at = datetime.now(pytz.utc) + timedelta(seconds=expires_in_seconds)

        return Token(
            access_token=auth_response["access_token"],
            refresh_token=auth_response["refresh_token"],
            valid_until=expires_at,
            username=username,
            password=password,
            pin=None,  # will be set later
        )

    def _get_authorization_code(
        self, cookies: dict, username: str, password: str
    ) -> str:
        """
        Request a redirect URL from the API, which will contain the
        authorization code for the remaining steps of the login process.
        """
        url = self._build_api_url("/user/signin")
        data = {"email": username, "password": password}

        response = self.session.post(
            url,
            json=data,
            cookies=cookies,
            # TODO: we might not need all these headers
            headers={
                "Referer": "https://br-ccapi.hyundai.com.br/web/v1/user/signin",
                "Accept-Encoding": "gzip, deflate, br",
                "Accept": "*/*",
                "Connection": "keep-alive",
                "Content-Type": "text/plain;charset=UTF-8",
                "Host": self.api_headers["Host"],
                "Accept-Language": "en-GB,en-US;q=0.9,en;q=0.8",
                "Origin": "https://br-ccapi.hyundai.com.br",
                "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148_CCS_APP_iOS",
            },
        )
        response.raise_for_status()
        response = response.json()
        logger.debug("Got redirect URL: %s", response["redirectUrl"])
        parsed_redirect_url = urlparse(response["redirectUrl"])
        authorization_code = parsed_redirect_url.query.split("=")[1]
        return authorization_code

    def _get_auth_response(self, authorization_code: str) -> dict:
        """
        Request an access token from the API, which will be used to authenticate the user.
        """
        url = self._build_api_url("/user/oauth2/token")
        body = {
            "client_id": self.ccsp_service_id,
            "grant_type": "authorization_code",
            "code": authorization_code,
            "redirect_uri": urljoin(self.api_url, "/user/oauth2/redirect"),
        }
        headers = {
            "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
            "User-Agent": self.api_headers["User-Agent"],
            "Authorization": self.basic_authorization_header,
        }

        response = requests.post(url, data=body, headers=headers)
        response.raise_for_status()
        response = response.json()
        logger.debug("Got response from API: %s", response)
        return response

    def _build_api_url(self, path: str) -> str:
        return self.api_url + path.removeprefix("/")

    def _get_cookies(self) -> dict:
        """Request cookies from the API, which will be used for authentication."""
        params = {
            "response_type": "code",
            "client_id": self.ccsp_service_id,
            "redirect_uri": self._build_api_url("/user/oauth2/redirect"),
        }

        url = self._build_api_url("/user/oauth2/authorize")
        logger.debug("Requesting cookies from %s with params %s", url, params)
        response = requests.get(url, params=params)
        response.raise_for_status()
        cookies = response.cookies.get_dict()
        logger.debug("Got cookies from response: %s", cookies)
        return cookies

    def _get_vehicle_details(self, token: Token, vehicle: Vehicle):
        """
        Query a single vehicle's profile.
        """
        url = self._build_api_url(f"/spa/vehicles/{vehicle.id}/profile")
        headers = self._get_authenticated_headers(token)
        response = self.session.get(url, headers=headers)
        response.raise_for_status()
        data = response.json()["resMsg"]
        return data

    def _request_vehicle_state(
        self, token: Token, vehicle: Vehicle, force_refresh: bool
    ) -> dict:
        """
        Checks for "cached" vehicle state (i.e., state from the API server, without
        having to wake up the car to request the state).
        """
        url = self._build_api_url(f"/spa/vehicles/{vehicle.id}")
        if not vehicle.ccu_ccs2_protocol_support:
            url = url + "/status/latest"
        else:  # NOTE: ccs2 is untested
            url = url + "/ccs2/carstatus/latest"

        headers = self._get_authenticated_headers(token)
        if force_refresh:
            headers["REFRESH"] = "true"

        logger.debug("Getting car status from %s", url)
        response = self.session.get(url, headers=headers)
        response = response.json()
        logger.debug("Car status response: %s", response)
        return response["resMsg"]

    def _get_ev_trip_details(self, token: Token, vehicle: Vehicle):
        """
        Get the EV trip details for the vehicle.
        """
        if vehicle.engine_type != EngineType.EV:
            return {}

        # NOTE: I don't have a vehicle with an EV engine to test this
        return NotImplementedError

    def _get_ev_driving_info(self, token: Token, vehicle: Vehicle):
        """
        Get the driving info for the vehicle.
        """
        if vehicle.engine_type != EngineType.EV:
            return {}
        # TODO: I don't have a vehicle with an EV engine to test this
        return NotImplementedError

    def _update_vehicle_properties(self, vehicle: Vehicle, state: CachedVehicleState):
        vehicle.last_updated_at = parse_datetime(
            state.current_state.get("time"), self.data_timezone
        )

        if drive_range := state.current_state.get("dte"):
            vehicle.fuel_driving_range = (
                drive_range["value"],
                DISTANCE_UNITS[drive_range["unit"]],
            )

        vehicle.engine_is_running = state.current_state.get("engine")
        vehicle.washer_fluid_warning_is_on = state.current_state.get(
            "washerFluidStatus"
        )
        vehicle.fuel_level = state.current_state.get("fuelLevel")
        vehicle.fuel_level_is_low = state.current_state.get("lowFuelLight")
        vehicle.defrost_is_on = state.current_state.get("defrost")
        vehicle.steering_wheel_heater_is_on = state.current_state.get("steerWheelHeat")
        vehicle.front_left_seat_status = SEAT_STATUS[
            get_child_value(state, "seatHeaterVentState.flSeatHeatState")
        ]
        vehicle.front_right_seat_status = SEAT_STATUS[
            get_child_value(state.current_state, "seatHeaterVentState.frSeatHeatState")
        ]
        vehicle.rear_left_seat_status = SEAT_STATUS[
            get_child_value(state.current_state, "seatHeaterVentState.rlSeatHeatState")
        ]
        vehicle.rear_right_seat_status = SEAT_STATUS[
            get_child_value(state.current_state, "seatHeaterVentState.rrSeatHeatState")
        ]
        vehicle.tire_pressure_rear_left_warning_is_on = bool(
            get_child_value(
                state.current_state, "tirePressureLamp.tirePressureWarningLampRearLeft"
            )
        )
        vehicle.tire_pressure_front_left_warning_is_on = bool(
            get_child_value(
                state.current_state, "tirePressureLamp.tirePressureWarningLampFrontLeft"
            )
        )
        vehicle.tire_pressure_front_right_warning_is_on = bool(
            get_child_value(
                state.current_state,
                "tirePressureLamp.tirePressureWarningLampFrontRight",
            )
        )
        vehicle.tire_pressure_rear_right_warning_is_on = bool(
            get_child_value(
                state.current_state,
                "tirePressureLamp.tirePressureWarningLampRearRight",
            )
        )
        vehicle.tire_pressure_all_warning_is_on = bool(
            get_child_value(
                state.current_state, "tirePressureLamp.tirePressureWarningLampAll"
            )
        )
        vehicle.front_left_window_is_open = get_child_value(
            state.current_state, "windowOpen.frontLeft"
        )
        vehicle.front_right_window_is_open = get_child_value(
            state.current_state, "windowOpen.frontRight"
        )
        vehicle.back_left_window_is_open = get_child_value(
            state.current_state, "windowOpen.backLeft"
        )
        vehicle.back_right_window_is_open = get_child_value(
            state.current_state, "windowOpen.backRight"
        )
        vehicle.is_locked = get_child_value(state.current_state, "doorLock")
        vehicle.front_left_door_is_open = get_child_value(
            state.current_state, "doorOpen.frontLeft"
        )
        vehicle.front_right_door_is_open = get_child_value(
            state.current_state, "doorOpen.frontRight"
        )
        vehicle.back_left_door_is_open = get_child_value(
            state.current_state, "doorOpen.backLeft"
        )
        vehicle.back_right_door_is_open = get_child_value(
            state.current_state, "doorOpen.backRight"
        )
        vehicle.hood_is_open = get_child_value(state.current_state, "hoodOpen")
        vehicle.trunk_is_open = get_child_value(state.current_state, "trunkOpen")
        return vehicle

    def _get_trip_info(
        self,
        token: Token,
        vehicle: Vehicle,
        date_string: str | None = None,
        trip_period_type: TripPeriodType = TripPeriodType.MONTH,
    ) -> TripInfo:
        url = self._build_api_url(f"/spa/vehicles/{vehicle.id}/tripinfo")

        # Use provided date or default to current month
        if date_string is None:
            date_string = date_to_year_month(date.today())

        if trip_period_type == TripPeriodType.MONTH:
            data = {"tripPeriodType": trip_period_type, "setTripMonth": date_string}
        else:
            data = {"tripPeriodType": trip_period_type, "setTripDay": date_string}
        response = self.session.post(
            url, json=data, headers=self._get_authenticated_headers(token)
        )
        try:
            response.raise_for_status()
            data = response.json()["resMsg"]
        except Exception:
            logger.exception("_get_trip_info request failed")

        trip_day_list = []
        for day in data["tripDayList"]:
            trip_day_list.append(
                TripDayListItem(
                    date=date_string_to_datetime(day["tripDayInMonth"]),
                    count=day["tripCntDay"],
                )
            )

        # Map API response field names to TripInfo field names
        return TripInfo(
            trip_day_list=trip_day_list,
            trip_period_type=TripPeriodType(data["tripPeriodType"]),
            month_trip_day_cnt=data["monthTripDayCnt"],
            # Map API field names to TripInfo field names
            drive_time=data["tripDrvTime"],
            idle_time=data["tripIdleTime"],
            distance=data["tripDist"],
            avg_speed=data["tripAvgSpeed"],
            max_speed=data["tripMaxSpeed"],
        )

    def update_vehicle_with_cached_state(self, token: Token, vehicle: Vehicle) -> None:
        state = CachedVehicleState()
        state.details = self._get_vehicle_details(token, vehicle)
        state.current_state = self._request_vehicle_state(token, vehicle, False)
        state.location = self._get_vehicle_location(token, vehicle)
        self._update_vehicle_properties(vehicle, state)
        self._update_vehicle_location(vehicle, state.location)

    def _get_vehicle_location(self, token: Token, vehicle: Vehicle) -> VehicleLocation:
        """
        Query the API for the vehicle location.
        """
        url = self._build_api_url(f"/spa/vehicles/{vehicle.id}/location/park")
        headers = self._get_authenticated_headers(token)

        try:
            response = self.session.get(url, headers=headers)
            response.raise_for_status()
            logger.debug("Get vehicle location response: %s", response.text)
            response = response.json()
            return VehicleLocation(
                lat=response["resMsg"]["coord"]["lat"],
                long=response["resMsg"]["coord"]["lng"],
                time=date_string_to_datetime(response["resMsg"]["time"]),
            )
        except Exception as e:
            logger.exception("Get vehicle location failed: %s", e)
            return None

    def _update_vehicle_location(self, vehicle: Vehicle, location: VehicleLocation):
        vehicle.location = (location.lat, location.long, location.time)

    def force_refresh_vehicle_state(self, token: Token, vehicle: Vehicle) -> None:
        """
        Force a refresh of the vehicle state.

        This means that the vehicle state will wake up the vehicle to get the latest state.
        This might take longer to complete and slightly consume the vehicle's battery.
        """
        state = CachedVehicleState(
            current_state=self._request_vehicle_state(token, vehicle, True),
            location=self._get_vehicle_location(token, vehicle),
        )
        self._update_vehicle_properties(vehicle, state)

    def get_vehicles(self, token: Token):
        url = self._build_api_url("/spa/vehicles")
        headers = self._get_authenticated_headers(token)
        response = self.session.get(url, headers=headers)
        response = response.json()
        logger.debug("Response from vehicles endpoint: %s", response)

        result = []
        for entry in response["resMsg"]["vehicles"]:
            entry_engine_type = self._get_vehicle_engine_type(entry["type"])
            vehicle: Vehicle = Vehicle(
                id=entry["vehicleId"],
                name=entry["nickname"],
                model=entry["vehicleName"],
                registration_date=entry["regDate"],
                VIN=entry["vin"],
                timezone=self.data_timezone,
                engine_type=entry_engine_type,
                ccu_ccs2_protocol_support=entry["ccuCCS2ProtocolSupport"],
            )
            result.append(vehicle)

        return result

    def update_month_trip_info(
        self,
        token: Token,
        vehicle: Vehicle,
        month_date: date,
    ) -> None:
        """
        Updates the vehicle.month_trip_info for the specified month.

        This feature provides monthly driving statistics.
        """
        vehicle.month_trip_info = None

        yyyymm_string = date_to_year_month(month_date)

        trip_info = self._get_trip_info(
            token,
            vehicle,
            date_string=yyyymm_string,
            trip_period_type=TripPeriodType.MONTH,
        )

        if trip_info.month_trip_day_cnt > 0:
            result = MonthTripInfo(
                yyyymm=yyyymm_string,
                day_list=[],
                summary=TripInfo(
                    drive_time=trip_info.drive_time,
                    idle_time=trip_info.idle_time,
                    distance=trip_info.distance,
                    avg_speed=trip_info.avg_speed,
                    max_speed=trip_info.max_speed,
                ),
            )

            for day in trip_info.trip_day_list:
                processed_day = DayTripCounts(
                    yyyymmdd=date_to_year_month_day(day.date.date()),
                    trip_count=day.count,
                )
                result.day_list.append(processed_day)

            vehicle.month_trip_info = result

    def update_day_trip_info(
        self,
        token: Token,
        vehicle: Vehicle,
        day_date: date,
    ) -> None:
        """
        Updates the vehicle.day_trip_info information for the specified day.

        This feature provides detailed trip information for a specific day.
        """
        vehicle.day_trip_info = None

        yyyymmdd_string = date_to_year_month_day(day_date)

        try:
            trip_info = self._get_trip_info(
                token,
                vehicle,
                date_string=yyyymmdd_string,
                trip_period_type=TripPeriodType.DAY,
            )

            if hasattr(trip_info, "trip_day_list") and trip_info.trip_day_list:
                result = DayTripInfo(
                    yyyymmdd=yyyymmdd_string,
                    trip_list=[],
                    summary=TripInfo(
                        drive_time=trip_info.drive_time,
                        idle_time=trip_info.idle_time,
                        distance=trip_info.distance,
                        avg_speed=trip_info.avg_speed,
                        max_speed=trip_info.max_speed,
                    ),
                )

                vehicle.day_trip_info = result
        except Exception as e:
            logger.exception("Failed to get day trip info: %s", e)

    def _get_vehicle_engine_type(self, vehicle_type: str) -> EngineType:
        match vehicle_type:
            case "GN":
                return EngineType.ICE
            case "EV":
                return EngineType.EV
            case "PHEV":
                return EngineType.PHEV
            case "HV":
                return EngineType.HEV
            case "PE":
                return EngineType.PHEV
            case _:
                raise APIError(f"Invalid vehicle type: {vehicle_type}")
