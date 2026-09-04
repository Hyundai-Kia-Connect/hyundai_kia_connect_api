"""ApiImpl.py"""

# pylint:disable=unnecessary-pass,missing-class-docstring,invalid-name,missing-function-docstring,wildcard-import,unused-wildcard-import,unused-argument,logging-fstring-interpolation
import datetime as dt
import logging
from dataclasses import dataclass
from typing import TypeVar

import requests
from requests.exceptions import JSONDecodeError

try:
    from geopy.geocoders import GoogleV3
except ImportError:
    GoogleV3 = None

from .const import (
    CHARGE_PORT_ACTION,
    DOMAIN,
    GEO_LOCATION_PROVIDERS,
    GOOGLE,
    OPENSTREETMAP,
    ORDER_STATUS,
    OTP_NOTIFY_TYPE,
    TEMPERATURE_C,
    TEMPERATURE_F,
    VALET_MODE_ACTION,
    VEHICLE_LOCK_ACTION,
    WINDOW_STATE,
)
from .Token import Token
from .utils import get_child_value, to_int_enum
from .Vehicle import Vehicle

_LOGGER = logging.getLogger(__name__)


@dataclass
class ClimateRequestOptions:
    set_temp: float = None
    duration: int = None
    defrost: bool = None
    climate: bool = None
    heating: int = None
    front_left_seat: int = None
    front_right_seat: int = None
    rear_left_seat: int = None
    rear_right_seat: int = None
    steering_wheel: int = None


@dataclass
class WindowRequestOptions:
    back_left: WINDOW_STATE = None
    back_right: WINDOW_STATE = None
    front_left: WINDOW_STATE = None
    front_right: WINDOW_STATE = None

    def __post_init__(self):
        """Convert string/int values to WINDOW_STATE enums."""
        self.back_left = to_int_enum(WINDOW_STATE, self.back_left)
        self.back_right = to_int_enum(WINDOW_STATE, self.back_right)
        self.front_left = to_int_enum(WINDOW_STATE, self.front_left)
        self.front_right = to_int_enum(WINDOW_STATE, self.front_right)


@dataclass
class OTPRequest:
    request_id: str | None
    otp_key: str | None
    has_email: bool | None
    has_sms: bool | None
    email: str | None
    sms: str | None


@dataclass
class ScheduleChargingClimateRequestOptions:
    """Options for scheduled charging and climate writes.

    ``None`` on any field means "leave unchanged": the library resolves it
    from the vehicle's current settings. An explicit value (including
    ``False``) is written to the car. On the flat EV5 path a scope with all
    fields ``None`` skips its endpoint entirely; the combined
    ``/reservation/chargehvac`` path always sends both scopes.
    """

    @dataclass
    class DepartureOptions:
        enabled: bool = None
        days: list[int] = None  # Sun=0, Mon=1, ..., Sat=6
        time: dt.time = None

    first_departure: DepartureOptions = None
    second_departure: DepartureOptions = None
    charging_enabled: bool = None
    off_peak_start_time: dt.time = None
    off_peak_end_time: dt.time = None
    off_peak_charge_only_enabled: bool = None
    climate_enabled: bool = None
    temperature: float = None
    # Deprecated (no-op): the temperature unit is always resolved from the
    # vehicle's reported settings. The field is kept for backward
    # compatibility with callers that still pass it and will be removed in a
    # future version (#1303 review).
    temperature_unit: int = None
    defrost: bool = None

    @classmethod
    def from_vehicle(cls, vehicle: Vehicle) -> "ScheduleChargingClimateRequestOptions":
        """Options representing the vehicle's current schedule settings.

        Unknown vehicle state falls back to the documented defaults
        (with warnings) — see ``_fill_schedule_options_from_vehicle``.
        """
        options = cls()
        _fill_schedule_options_from_vehicle(
            options, vehicle, scopes=("charge", "climate")
        )
        return options


_T = TypeVar("_T")


def _schedule_charging_scopes(
    options: ScheduleChargingClimateRequestOptions,
) -> tuple[bool, bool]:
    """Return (charge_scope_active, climate_scope_active) from raw options.

    A scope is active when at least one of its fields was explicitly provided
    (not None). Departures count toward the climate scope.
    """
    charge_active = any(
        value is not None
        for value in (
            options.charging_enabled,
            options.off_peak_start_time,
            options.off_peak_end_time,
            options.off_peak_charge_only_enabled,
        )
    )
    climate_active = (
        any(
            value is not None
            for value in (
                options.climate_enabled,
                options.temperature,
                options.defrost,
            )
        )
        or options.first_departure is not None
        or options.second_departure is not None
    )
    return charge_active, climate_active


def _vehicle_temperature_unit(vehicle: Vehicle) -> int | None:
    """Wire temperature unit (0=°C, 1=°F) from the vehicle's reported setting.

    ``None`` when the vehicle does not report a departure-climate unit (the
    CCS2 status does not expose it yet — see the parsing TODO in
    ``ApiImplType1``).
    """
    unit_map = {TEMPERATURE_C: 0, TEMPERATURE_F: 1}
    source_unit = vehicle.ev_first_departure_climate_temperature_unit
    if source_unit in unit_map:
        return unit_map[source_unit]
    return None


def _guard_schedule_temperature(
    options: ScheduleChargingClimateRequestOptions, vehicle: Vehicle
) -> None:
    """Refuse to write the climate scope with an unresolvable temperature.

    The apps keep the car's stored temperature when writing the schedule, but
    the payload always carries ``airTemp``. When neither the caller nor the
    vehicle provides a temperature, writing the documented default (21 °C)
    would silently reset the car's setting — so raise instead (Lekensteyn,
    #1303 review; affects CCS2 models, which do not report the departure
    climate temperature yet).
    """
    if (
        options.temperature is None
        and vehicle.ev_first_departure_climate_temperature is None
    ):
        raise ValueError(
            f"{DOMAIN} - {vehicle.id}: vehicle does not report the departure "
            "climate temperature; pass an explicit temperature to avoid "
            "resetting the vehicle's setting"
        )


def _fill_option_value(  # noqa: UP047  # TypeVar required for py3.10 floor
    current: _T | None,
    source: _T | None,
    default: _T,
    label: str,
    missing: list[str],
) -> _T:
    """None = leave unchanged: explicit value, else vehicle value, else default.

    Falls back to ``default`` and records ``label`` in ``missing`` when the
    vehicle does not report the value (the caller warns once for all of them).
    """
    if current is not None:
        return current
    if source is not None:
        return source
    missing.append(label)
    return default


def _fill_departure_options(
    departure: ScheduleChargingClimateRequestOptions.DepartureOptions,
    vehicle: Vehicle,
    number: int,
    *,
    missing: list[str],
) -> None:
    """Fill a departure's None fields from the matching vehicle state."""
    if number == 1:
        v_enabled = vehicle.ev_first_departure_enabled
        v_days = vehicle.ev_first_departure_days
        v_time = vehicle.ev_first_departure_time
        prefix = "first_departure"
    else:
        v_enabled = vehicle.ev_second_departure_enabled
        v_days = vehicle.ev_second_departure_days
        v_time = vehicle.ev_second_departure_time
        prefix = "second_departure"
    departure.enabled = _fill_option_value(
        departure.enabled, v_enabled, False, f"{prefix}.enabled", missing
    )
    # Default is the [9] "no days selected" sentinel, not [0]: in the apps a
    # departure without repeat days carries day 9 (NOT_SELECTED_DAY), and [0]
    # would select Sunday instead (Lekensteyn, #1303 review — confirmed in the
    # apps' payload round-trip).
    departure.days = _fill_option_value(
        departure.days, v_days, [9], f"{prefix}.days", missing
    )
    # The apps normalize an empty day selection to the same [9] sentinel
    # rather than sending an empty list.
    if departure.days == []:
        departure.days = [9]
    departure.time = _fill_option_value(
        departure.time, v_time, dt.time(), f"{prefix}.time", missing
    )


def _fill_schedule_options_from_vehicle(
    options: ScheduleChargingClimateRequestOptions,
    vehicle: Vehicle,
    *,
    scopes: tuple[str, ...],
) -> None:
    """Fill None option fields with the vehicle's current settings.

    Implements "None = leave unchanged": fields already set are preserved,
    unset fields take the vehicle's reported value, and unknown vehicle state
    falls back to the documented default (so a known value is never silently
    reset). Only the requested scopes are filled; an inactive scope is
    skipped (its endpoint is not called). Missing vehicle state is reported
    with a single warning listing all affected options.

    Region coverage: departure-climate fields are currently only reported by
    the Kia EU API; charge/off-peak fields are reported by Kia EU, AU, CN and
    the shared CCS2 status parser. For other regions the defaults apply.
    """
    missing: list[str] = []
    if "charge" in scopes:
        options.charging_enabled = _fill_option_value(
            options.charging_enabled,
            vehicle.ev_schedule_charge_enabled,
            False,
            "charging_enabled",
            missing,
        )
        options.off_peak_start_time = _fill_option_value(
            options.off_peak_start_time,
            vehicle.ev_off_peak_start_time,
            dt.time(),
            "off_peak_start_time",
            missing,
        )
        options.off_peak_end_time = _fill_option_value(
            options.off_peak_end_time,
            vehicle.ev_off_peak_end_time,
            dt.time(),
            "off_peak_end_time",
            missing,
        )
        options.off_peak_charge_only_enabled = _fill_option_value(
            options.off_peak_charge_only_enabled,
            vehicle.ev_off_peak_charge_only_enabled,
            False,
            "off_peak_charge_only_enabled",
            missing,
        )
    if "climate" in scopes:
        for number, departure in (
            (1, options.first_departure),
            (2, options.second_departure),
        ):
            if departure is None:
                departure = ScheduleChargingClimateRequestOptions.DepartureOptions()
                if number == 1:
                    options.first_departure = departure
                else:
                    options.second_departure = departure
            _fill_departure_options(departure, vehicle, number, missing=missing)
        # One climate set models both departures (known limitation, #1302).
        options.climate_enabled = _fill_option_value(
            options.climate_enabled,
            vehicle.ev_first_departure_climate_enabled,
            False,
            "climate_enabled",
            missing,
        )
        options.temperature = _fill_option_value(
            options.temperature,
            vehicle.ev_first_departure_climate_temperature,
            21.0,
            "temperature",
            missing,
        )
        options.defrost = _fill_option_value(
            options.defrost,
            vehicle.ev_first_departure_climate_defrost,
            False,
            "defrost",
            missing,
        )
    if missing:
        _LOGGER.warning(
            f"{DOMAIN} - {vehicle.id}: schedule options not reported by "
            f"vehicle; using defaults for: {', '.join(missing)}"
        )


@dataclass
class POICoord:
    lat: float = None
    lon: float = None
    alt: int = 0
    type: int = 0


@dataclass
class POIInfo:
    phone: str = ""
    waypoint_id: int = 1
    lang: int = 1
    src: str = "HERE"
    coord: POICoord = None
    addr: str = ""
    zip: str = ""
    place_id: str = ""
    name: str = ""

    def to_dict(self) -> dict:
        return {
            "phone": self.phone,
            "waypointID": self.waypoint_id,
            "lang": self.lang,
            "src": self.src,
            "coord": {
                "lat": self.coord.lat,
                "alt": self.coord.alt,
                "lon": self.coord.lon,
                "type": self.coord.type,
            },
            "addr": self.addr,
            "zip": self.zip,
            "placeid": self.place_id,
            "name": self.name,
        }


class ApiImplSession(requests.Session):
    """Shared HTTP session with default timeout and connection pooling.

    All regions should use this session (or a subclass) for HTTP calls.
    Override class attributes per region in __init__ if needed.

    Retry is intentionally NOT configured here. Pre-PR behavior retried only
    CA connection errors (#857, login error 104), and not all requests are
    safe to replay (e.g. non-idempotent control POSTs). If connection-reset
    issues reappear, re-add a login-only connection retry. See PR #1160.
    """

    HTTP_CONNECT_TIMEOUT = 10
    HTTP_READ_TIMEOUT = 30

    def request(self, method, url, **kwargs):
        kwargs.setdefault(
            "timeout", (self.HTTP_CONNECT_TIMEOUT, self.HTTP_READ_TIMEOUT)
        )
        try:
            return super().request(method, url, **kwargs)
        except requests.exceptions.Timeout as exc:
            from .exceptions import RequestTimeoutError

            raise RequestTimeoutError(str(exc)) from exc


class ApiImpl:
    data_timezone = dt.UTC
    temperature_range = None
    previous_latitude: float = None
    previous_longitude: float = None
    supports_window_control: bool = False
    supports_valet_mode: bool = False

    def __init__(self) -> None:
        """Initialize."""

    def login(
        self,
        username: str,
        password: str,
        pin: str | None = None,
    ) -> Token | OTPRequest:
        """Login into cloud endpoints and return Token or OTP Details if OTP is triggered"""
        raise NotImplementedError("login is not implemented for this region")

    def send_otp(self, otp_request: OTPRequest, notify_type: OTP_NOTIFY_TYPE) -> None:
        """Sends OTP to the user via selected destination and via"""
        raise NotImplementedError("send_otp is not implemented for this region")

    def verify_otp_and_complete_login(
        self,
        username: str,
        password: str,
        otp_code: str,
        otp_request: OTPRequest,
        pin: str | None = None,
    ) -> Token:
        """Confirms OTP code sent to the user"""
        raise NotImplementedError(
            "verify_otp_and_complete_login is not implemented for this region"
        )

    def get_vehicles(self, token: Token) -> list[Vehicle]:
        """Return all Vehicle instances for a given Token"""
        raise NotImplementedError("get_vehicles is not implemented for this region")

    def refresh_vehicles(self, token: Token, vehicles: list[Vehicle]) -> None:
        """Refresh the vehicle data provided in get_vehicles.
        Required for Kia USA as key is session specific"""
        return

    def update_vehicle_with_cached_state(self, token: Token, vehicle: Vehicle) -> None:
        """Get cached vehicle data and update Vehicle instance with it"""
        raise NotImplementedError(
            "update_vehicle_with_cached_state is not implemented for this region"
        )

    def test_token(self, token: Token) -> bool:
        """Test if token is valid
        Use any dummy request to test if token is still valid"""
        return True

    def check_action_status(
        self,
        token: Token,
        vehicle: Vehicle,
        action_id: str,
        synchronous: bool = False,
        timeout: int = 0,
    ) -> ORDER_STATUS:
        pass

    def force_refresh_vehicle_state(self, token: Token, vehicle: Vehicle) -> None:
        """Triggers the system to contact the car and get fresh data"""
        raise NotImplementedError(
            "force_refresh_vehicle_state is not implemented for this region"
        )

    def update_geocoded_location(
        self,
        token: Token,
        vehicle: Vehicle,
        use_email: bool,
        provider: int = 1,
        API_KEY: str | None = None,
    ) -> None:
        if vehicle.location_latitude and vehicle.location_longitude:
            if (
                vehicle.geocode
                and vehicle.location_latitude == self.previous_latitude
                and vehicle.location_longitude == self.previous_longitude
            ):  # previous coordinates are the same, so keep last valid vehicle.geocode
                _LOGGER.debug(f"{DOMAIN} - Keeping last geocode location")
            elif GEO_LOCATION_PROVIDERS[provider] == OPENSTREETMAP:
                email_parameter = ""
                if use_email is True:
                    email_parameter = "&email=" + token.username

                url = (
                    "https://nominatim.openstreetmap.org/reverse?lat="
                    + str(vehicle.location_latitude)
                    + "&lon="
                    + str(vehicle.location_longitude)
                    + "&format=json&addressdetails=1&zoom=18"
                    + email_parameter
                )
                headers = {"user-agent": "curl/7.81.0"}
                response = requests.get(url, headers=headers, timeout=(5, 15))
                try:
                    response = response.json()
                except JSONDecodeError:
                    _LOGGER.warning(f"{DOMAIN} - failed geocode openstreetmap")
                    vehicle.geocode = None
                else:
                    vehicle.geocode = (
                        get_child_value(response, "display_name"),
                        get_child_value(response, "address"),
                    )
                    self.previous_latitude = vehicle.location_latitude
                    self.previous_longitude = vehicle.location_longitude
                    _LOGGER.debug(f"{DOMAIN} - geocode openstreetmap")
            elif GEO_LOCATION_PROVIDERS[provider] == GOOGLE:
                if not API_KEY:
                    _LOGGER.warning(f"{DOMAIN} - missing API KEY for geocode Google")
                    vehicle.geocode = None
                elif GoogleV3 is None:
                    _LOGGER.warning(f"{DOMAIN} - geopy is required for geocode Google")
                    vehicle.geocode = None
                else:
                    latlong = (vehicle.location_latitude, vehicle.location_longitude)
                    try:
                        geolocator = GoogleV3(api_key=API_KEY)
                        locations = geolocator.reverse(latlong)
                        if locations:
                            vehicle.geocode = locations
                            self.previous_latitude = vehicle.location_latitude
                            self.previous_longitude = vehicle.location_longitude
                            _LOGGER.debug(f"{DOMAIN} - geocode google")
                    except Exception as ex:  # pylint: disable=broad-except
                        _LOGGER.warning(f"{DOMAIN} - failed geocode Google: {ex}")
                        vehicle.geocode = None

    def lock_action(
        self, token: Token, vehicle: Vehicle, action: VEHICLE_LOCK_ACTION
    ) -> str:
        """Lock or unlocks a vehicle.  Returns the tracking ID"""
        raise NotImplementedError("lock_action is not implemented for this region")

    def start_climate(
        self, token: Token, vehicle: Vehicle, options: ClimateRequestOptions
    ) -> str:
        """Starts climate or remote start.  Returns the tracking ID"""
        raise NotImplementedError("start_climate is not implemented for this region")

    def stop_climate(self, token: Token, vehicle: Vehicle) -> str:
        """Stops climate or remote start.  Returns the tracking ID"""
        raise NotImplementedError("stop_climate is not implemented for this region")

    def start_charge(self, token: Token, vehicle: Vehicle) -> str:
        """Starts charge. Returns the tracking ID"""
        raise NotImplementedError("start_charge is not implemented for this region")

    def stop_charge(self, token: Token, vehicle: Vehicle) -> str:
        """Stops charge. Returns the tracking ID"""
        raise NotImplementedError("stop_charge is not implemented for this region")

    def set_charge_limits(
        self, token: Token, vehicle: Vehicle, ac: int, dc: int
    ) -> str:
        """Sets charge limits. Returns the tracking ID"""
        raise NotImplementedError(
            "set_charge_limits is not implemented for this region"
        )

    def set_charging_current(self, token: Token, vehicle: Vehicle, level: int) -> str:
        """
        feature only available for some regions.
        Sets charge current level (1=100%, 2=90%, 3=60%). Returns the tracking ID
        """
        raise NotImplementedError(
            "set_charging_current is not implemented for this region"
        )

    def set_windows_state(
        self, token: Token, vehicle: Vehicle, options: WindowRequestOptions
    ) -> str:
        """Opens or closes a particular window. Returns the tracking ID"""
        raise NotImplementedError(
            "set_windows_state is not implemented for this region"
        )

    def charge_port_action(
        self, token: Token, vehicle: Vehicle, action: CHARGE_PORT_ACTION
    ) -> str:
        """Opens or closes the charging port of the car. Returns the tracking ID"""
        raise NotImplementedError(
            "charge_port_action is not implemented for this region"
        )

    def update_month_trip_info(
        self, token: Token, vehicle: Vehicle, yyyymm_string: str
    ) -> None:
        """
        feature only available for some regions.
        Updates the vehicle.month_trip_info for the specified month.

        Default this information is None:

        month_trip_info: MonthTripInfo = None
        """
        raise NotImplementedError(
            "update_month_trip_info is not implemented for this region"
        )

    def update_day_trip_info(
        self, token: Token, vehicle: Vehicle, yyyymmdd_string: str
    ) -> None:
        """
        feature only available for some regions.
        Updates the vehicle.day_trip_info information for the specified day.

        Default this information is None:

        day_trip_info: DayTripInfo = None
        """
        raise NotImplementedError(
            "update_day_trip_info is not implemented for this region"
        )

    def schedule_charging_and_climate(
        self,
        token: Token,
        vehicle: Vehicle,
        options: ScheduleChargingClimateRequestOptions,
    ) -> str:
        """
        feature only available for some regions.
        Schedule charging and climate control. Returns the tracking ID
        """
        raise NotImplementedError(
            "schedule_charging_and_climate is not implemented for this region"
        )

    def start_hazard_lights(self, token: Token, vehicle: Vehicle) -> str:
        """Turns on the hazard lights for 30 seconds"""
        raise NotImplementedError(
            "start_hazard_lights is not implemented for this region"
        )

    def start_hazard_lights_and_horn(self, token: Token, vehicle: Vehicle) -> str:
        """Turns on the hazard lights and horn for 30 seconds"""
        raise NotImplementedError(
            "start_hazard_lights_and_horn is not implemented for this region"
        )

    def valet_mode_action(
        self, token: Token, vehicle: Vehicle, action: VALET_MODE_ACTION
    ) -> str:
        """
        feature only available for some regions.
        Activate or Deactivate valet mode. Returns the tracking ID
        """
        raise NotImplementedError(
            "valet_mode_action is not implemented for this region"
        )

    def set_vehicle_to_load_discharge_limit(
        self, token: Token, vehicle: Vehicle, limit: int
    ) -> str:
        """
        feature only available for some regions.
        Set the vehicle to load limit. Returns the tracking ID
        """
        raise NotImplementedError(
            "set_vehicle_to_load_discharge_limit is not implemented for this region"
        )

    def set_navigation(
        self, token: Token, vehicle: Vehicle, poi_list: list[POIInfo]
    ) -> str:
        """Send navigation destinations to the vehicle. Returns the tracking ID."""
        raise NotImplementedError("set_navigation is not implemented for this region")

    def refresh_access_token(self, token: Token) -> Token | OTPRequest:
        """Refresh the token using the refresh token"""
        # By default, just call login again, ideally use the refresh token flow
        # Pass the pin explicitly as a keyword to avoid positional
        # argument mis-binding in subclasses that accept different
        # login() signatures (some accept a `token` positional arg).
        return self.login(
            username=token.username, password=token.password, pin=token.pin
        )
