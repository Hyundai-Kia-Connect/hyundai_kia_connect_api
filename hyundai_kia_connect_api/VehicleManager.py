"""VehicleManager.py"""

# pylint:disable=logging-fstring-interpolation,missing-class-docstring,missing-function-docstring,line-too-long,invalid-name

import datetime as dt
import logging
from datetime import timedelta

from .ApiImpl import (
    ApiImpl,
    ClimateRequestOptions,
    ScheduleChargingClimateRequestOptions,
    WindowRequestOptions,
    OTPRequest,
)
from .const import (
    BRAND_GENESIS,
    BRAND_HYUNDAI,
    BRAND_KIA,
    BRANDS,
    CHARGE_PORT_ACTION,
    DOMAIN,
    ORDER_STATUS,
    REGION_AUSTRALIA,
    REGION_BRAZIL,
    REGION_CANADA,
    REGION_CHINA,
    REGION_EUROPE,
    REGION_INDIA,
    REGION_NZ,
    REGION_USA,
    REGIONS,
    VALET_MODE_ACTION,
    VEHICLE_LOCK_ACTION,
    OTP_NOTIFY_TYPE,
)
from .exceptions import APIError, AuthenticationOTPRequired
from .HyundaiBlueLinkApiBR import HyundaiBlueLinkApiBR
from .HyundaiBlueLinkApiUSA import HyundaiBlueLinkApiUSA
from .KiaUvoApiAU import KiaUvoApiAU
from .KiaUvoApiCA import KiaUvoApiCA
from .KiaUvoApiCN import KiaUvoApiCN
from .KiaUvoApiEU import KiaUvoApiEU
from .KiaUvoApiIN import KiaUvoApiIN
from .KiaUvoApiUSA import KiaUvoApiUSA
from .Token import Token
from .Vehicle import Vehicle

_LOGGER = logging.getLogger(__name__)


class VehicleManager:
    def __init__(
        self,
        region: int,
        brand: int,
        username: str,
        password: str,
        pin: str,
        geocode_api_enable: bool = False,
        geocode_api_use_email: bool = False,
        geocode_provider: int = 1,
        geocode_api_key: str = None,
        token: Token = None,
        language: str = "en",
    ):
        self.region: int = region
        self.brand: int = brand
        self.username: str = username
        self.password: str = password
        self.geocode_api_enable: bool = geocode_api_enable
        self.geocode_api_use_email: bool = geocode_api_use_email
        self.geocode_provider: int = geocode_provider
        self.pin: str = pin
        self.language: str = language
        self.geocode_api_key: str = geocode_api_key

        self.api: ApiImpl = self.get_implementation_by_region_brand(
            self.region, self.brand, self.language
        )

        self.token: Token = token
        self.vehicles: dict = {}
        self.otp_request: OTPRequest = None

    @DeprecationWarning
    def initialize(self) -> None:
        self.token: Token = self.api.login(
            username=self.username,
            password=self.password,
            pin=self.pin,
        )
        self.initialize_vehicles()

    def login(self) -> bool | OTPRequest:
        """Returns True if login successful, or OTPOptions if OTP is required"""
        result = self.api.login(
            username=self.username,
            password=self.password,
            pin=self.pin,
        )
        if isinstance(result, Token):
            self.token: Token = result
            self.initialize_vehicles()
            return True
        if isinstance(result, OTPRequest):
            self.otp_request = result
            return result

    def send_otp(self, notify_type: OTP_NOTIFY_TYPE) -> None:
        self.api.send_otp(self.otp_request, notify_type)

    def verify_otp_and_complete_login(self, otp_code: str) -> None:
        self.token = self.api.verify_otp_and_complete_login(
            username=self.username,
            password=self.password,
            otp_code=otp_code,
            otp_request=self.otp_request,
            pin=self.pin,
        )
        self.initialize_vehicles()

    def initialize_vehicles(self):
        if len(self.vehicles) > 0:
            _LOGGER.warning(
                "Vehicles already initialized, this will re-initialize and cause data loss mapping errors"
            )
        vehicles = self.api.get_vehicles(self.token)
        for vehicle in vehicles:
            self.vehicles[vehicle.id] = vehicle

    def get_vehicle(self, vehicle_id: str) -> Vehicle:
        return self.vehicles[vehicle_id]

    def update_all_vehicles_with_cached_state(self) -> None:
        for vehicle_id in self.vehicles.keys():
            self.update_vehicle_with_cached_state(vehicle_id)

    def update_vehicle_with_cached_state(self, vehicle_id: str) -> None:
        vehicle = self.get_vehicle(vehicle_id)
        if vehicle.enabled:
            self.api.update_vehicle_with_cached_state(self.token, vehicle)
            if self.geocode_api_enable is True:
                self.api.update_geocoded_location(
                    token=self.token,
                    vehicle=vehicle,
                    use_email=self.geocode_api_use_email,
                    provider=self.geocode_provider,
                    API_KEY=self.geocode_api_key,
                )
        else:
            _LOGGER.debug(f"{DOMAIN} - Vehicle Disabled, skipping.")

    def check_and_force_update_vehicles(self, force_refresh_interval: int) -> None:
        for vehicle_id in self.vehicles.keys():
            self.check_and_force_update_vehicle(force_refresh_interval, vehicle_id)

    def check_and_force_update_vehicle(
        self, force_refresh_interval: int, vehicle_id: str
    ) -> None:
        # Force refresh only if current data is older than the value bassed in seconds.
        # Otherwise runs a cached update.
        started_at_utc: dt.datetime = dt.datetime.now(dt.timezone.utc)
        vehicle = self.get_vehicle(vehicle_id)
        if vehicle.last_updated_at is not None:
            _LOGGER.debug(
                f"{DOMAIN} - Time differential in seconds: {(started_at_utc - vehicle.last_updated_at).total_seconds()}"  # noqa
            )
            if (
                started_at_utc - vehicle.last_updated_at
            ).total_seconds() > force_refresh_interval:
                self.force_refresh_vehicle_state(vehicle_id)
            else:
                self.update_vehicle_with_cached_state(vehicle_id)
        else:
            self.update_vehicle_with_cached_state(vehicle_id)

    def force_refresh_all_vehicles_states(self) -> None:
        for vehicle_id in self.vehicles.keys():
            self.force_refresh_vehicle_state(vehicle_id)

    def force_refresh_vehicle_state(self, vehicle_id: str) -> None:
        vehicle = self.get_vehicle(vehicle_id)
        if vehicle.enabled:
            self.api.force_refresh_vehicle_state(self.token, vehicle)
        else:
            _LOGGER.debug(f"{DOMAIN} - Vehicle Disabled, skipping.")

    def check_and_refresh_token(self) -> bool:
        if self.token is None:
            if self.login() is True:
                if len(self.vehicles) == 0:
                    self.initialize_vehicles()
                return True
            else:
                raise AuthenticationOTPRequired("OTP required to refresh token")
        now_utc = dt.datetime.now(dt.timezone.utc)
        grace_period = timedelta(seconds=10)
        min_supported_datetime = dt.datetime.min.replace(tzinfo=dt.timezone.utc)
        valid_until = self.token.valid_until
        token_expired = False
        if not isinstance(valid_until, dt.datetime):
            token_expired = True
        else:
            if valid_until.tzinfo is None:
                valid_until = valid_until.replace(tzinfo=dt.timezone.utc)
            if valid_until <= min_supported_datetime + grace_period:
                token_expired = True
            else:
                token_expired = valid_until - grace_period <= now_utc
        if token_expired or self.api.test_token(self.token) is False:
            _LOGGER.debug(f"{DOMAIN} - Refresh token expired")
            result = self.api.refresh_access_token(
                self.token,
            )
            if isinstance(result, Token):
                self.token: Token = result
                # Temp correction to fix bad data due to a bug.
                if self.token.pin != self.pin:
                    self.token.pin = self.pin
                if len(self.vehicles) == 0:
                    self.initialize_vehicles()
            if isinstance(result, OTPRequest):
                raise AuthenticationOTPRequired("OTP required to refresh token")
            self.api.refresh_vehicles(self.token, self.vehicles)
            return True
        if len(self.vehicles) == 0:
            self.initialize_vehicles()
        return False

    def start_climate(self, vehicle_id: str, options: ClimateRequestOptions) -> str:
        return self.api.start_climate(self.token, self.get_vehicle(vehicle_id), options)

    def stop_climate(self, vehicle_id: str) -> str:
        return self.api.stop_climate(self.token, self.get_vehicle(vehicle_id))

    def lock(self, vehicle_id: str) -> str:
        return self.api.lock_action(
            self.token, self.get_vehicle(vehicle_id), VEHICLE_LOCK_ACTION.LOCK
        )

    def unlock(self, vehicle_id: str) -> str:
        return self.api.lock_action(
            self.token,
            self.get_vehicle(vehicle_id),
            VEHICLE_LOCK_ACTION.UNLOCK,
        )

    def start_charge(self, vehicle_id: str) -> str:
        return self.api.start_charge(self.token, self.get_vehicle(vehicle_id))

    def stop_charge(self, vehicle_id: str) -> str:
        return self.api.stop_charge(self.token, self.get_vehicle(vehicle_id))

    def start_hazard_lights(self, vehicle_id: str) -> str:
        return self.api.start_hazard_lights(self.token, self.get_vehicle(vehicle_id))

    def start_hazard_lights_and_horn(self, vehicle_id: str) -> str:
        return self.api.start_hazard_lights_and_horn(
            self.token, self.get_vehicle(vehicle_id)
        )

    def set_charge_limits(self, vehicle_id: str, ac: int, dc: int) -> str:
        return self.api.set_charge_limits(
            self.token, self.get_vehicle(vehicle_id), ac, dc
        )

    def set_charging_current(self, vehicle_id: str, level: int) -> str:
        return self.api.set_charging_current(
            self.token, self.get_vehicle(vehicle_id), level
        )

    def set_windows_state(self, vehicle_id: str, options: WindowRequestOptions) -> str:
        return self.api.set_windows_state(
            self.token, self.get_vehicle(vehicle_id), options
        )

    def check_action_status(
        self,
        vehicle_id: str,
        action_id: str,
        synchronous: bool = False,
        timeout: int = 120,
    ) -> ORDER_STATUS:
        """
        Check for the status of a sent action/command.

        Actions can have 4 states:
        - pending: request sent to vehicle, waiting for response
        - success: vehicle confirmed that the action was performed
        - fail: vehicle could not perform the action
                (most likely because a condition was not met)
        - vehicle timeout: request sent to vehicle, no response received.

        In case of timeout, the API can return "pending" for up to 2 minutes before
        it returns a final state.

        :param vehicle_id: ID of the vehicle
        :param action_id: ID of the action
        :param synchronous: Whether to wait for pending actions to reach a final
                            state (success/fail/timeout)
        :param timeout:
            Time in seconds to wait for pending actions to reach a final state.
        :return: status of the order
        """
        return self.api.check_action_status(
            self.token, self.get_vehicle(vehicle_id), action_id, synchronous, timeout
        )

    def open_charge_port(self, vehicle_id: str) -> str:
        return self.api.charge_port_action(
            self.token, self.get_vehicle(vehicle_id), CHARGE_PORT_ACTION.OPEN
        )

    def close_charge_port(self, vehicle_id: str) -> str:
        return self.api.charge_port_action(
            self.token, self.get_vehicle(vehicle_id), CHARGE_PORT_ACTION.CLOSE
        )

    def update_month_trip_info(self, vehicle_id: str, yyyymm_string: str) -> None:
        """
        feature only available for some regions.
        Updates the vehicle.month_trip_info for the specified month.

        Default this information is None:

        month_trip_info: MonthTripInfo = None
        """
        vehicle = self.get_vehicle(vehicle_id)
        self.api.update_month_trip_info(self.token, vehicle, yyyymm_string)

    def update_day_trip_info(self, vehicle_id: str, yyyymmdd_string: str) -> None:
        """
        feature only available for some regions.
        Updates the vehicle.day_trip_info information for the specified day.

        Default this information is None:

        day_trip_info: DayTripInfo = None
        """
        vehicle = self.get_vehicle(vehicle_id)
        self.api.update_day_trip_info(self.token, vehicle, yyyymmdd_string)

    def disable_vehicle(self, vehicle_id: str) -> None:
        self.get_vehicle(vehicle_id).enabled = False

    def enable_vehicle(self, vehicle_id: str) -> None:
        self.get_vehicle(vehicle_id).enabled = True

    def schedule_charging_and_climate(
        self, vehicle_id: str, options: ScheduleChargingClimateRequestOptions
    ) -> str:
        return self.api.schedule_charging_and_climate(
            self.token, self.get_vehicle(vehicle_id), options
        )

    def start_valet_mode(self, vehicle_id: str) -> str:
        return self.api.valet_mode_action(
            self.token, self.get_vehicle(vehicle_id), VALET_MODE_ACTION.ACTIVATE
        )

    def stop_valet_mode(self, vehicle_id: str) -> str:
        return self.api.valet_mode_action(
            self.token, self.get_vehicle(vehicle_id), VALET_MODE_ACTION.DEACTIVATE
        )

    def set_vehicle_to_load_discharge_limit(self, vehicle_id: str, limit: int) -> str:
        return self.api.set_vehicle_to_load_discharge_limit(
            self.token, self.get_vehicle(vehicle_id), limit
        )

    @staticmethod
    def get_implementation_by_region_brand(
        region: int, brand: int, language: str
    ) -> ApiImpl:
        if REGIONS[region] == REGION_CANADA:
            return KiaUvoApiCA(region, brand, language)
        elif REGIONS[region] == REGION_EUROPE:
            return KiaUvoApiEU(region, brand, language)
        elif REGIONS[region] == REGION_USA and (
            BRANDS[brand] == BRAND_HYUNDAI or BRANDS[brand] == BRAND_GENESIS
        ):
            return HyundaiBlueLinkApiUSA(region, brand, language)
        elif REGIONS[region] == REGION_USA and BRANDS[brand] == BRAND_KIA:
            return KiaUvoApiUSA(region, brand, language)
        elif REGIONS[region] == REGION_CHINA:
            return KiaUvoApiCN(region, brand, language)
        elif REGIONS[region] == REGION_AUSTRALIA:
            return KiaUvoApiAU(region, brand, language)
        elif REGIONS[region] == REGION_NZ:
            if BRANDS[brand] == BRAND_KIA:
                return KiaUvoApiAU(region, brand, language)
            else:
                raise APIError(
                    f"Unknown brand {BRANDS[brand]} for region {REGIONS[region]}"
                )
        elif REGIONS[region] == REGION_INDIA:
            return KiaUvoApiIN(brand)
        elif REGIONS[region] == REGION_BRAZIL:
            return HyundaiBlueLinkApiBR(region, brand, language)
        else:
            raise APIError(f"Unknown region {region}")
