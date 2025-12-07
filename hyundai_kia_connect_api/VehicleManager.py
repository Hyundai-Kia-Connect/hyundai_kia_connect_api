"""VehicleManager.py"""

# pylint:disable=logging-fstring-interpolation,missing-class-docstring,missing-function-docstring,line-too-long,invalid-name

import datetime as dt
import logging
import typing as ty
from datetime import timedelta

from .ApiImpl import (
    ApiImpl,
    ClimateRequestOptions,
    ScheduleChargingClimateRequestOptions,
    WindowRequestOptions,
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
)
from .exceptions import APIError
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
        language: str = "en",
        otp_handler: ty.Callable[[dict], dict] | None = None,
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
        self.otp_handler = otp_handler

        self.api: ApiImpl = self.get_implementation_by_region_brand(
            self.region, self.brand, self.language
        )

        self.token: Token = None
        self.vehicles: dict = {}
        self.vehicles_valid = False

    def initialize(self) -> None:
        self.token: Token = self.api.login(
            self.username,
            self.password,
            token=self.token,
            otp_handler=self.otp_handler,
        )
        self.token.pin = self.pin
        self.initialize_vehicles()

    @property
    def supports_otp(self) -> bool:
        """Return whether the selected API implementation supports OTP."""
        return getattr(self.api, "supports_otp", False)

    def initialize_vehicles(self):
        vehicles = self.api.get_vehicles(self.token)
        self.vehicles_valid = True
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
            self.initialize()
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
            self.token: Token = self.api.login(
                self.username,
                self.password,
                token=self.token,
                otp_handler=self.otp_handler,
            )
            self.token.pin = self.pin
            self.vehicles = self.api.refresh_vehicles(self.token, self.vehicles)
            return True
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
