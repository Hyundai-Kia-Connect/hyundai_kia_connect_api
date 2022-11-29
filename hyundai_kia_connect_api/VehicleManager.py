import asyncio
import datetime as dt
import logging
from dataclasses import dataclass

import pytz

from .ApiImpl import ApiImpl
from .Vehicle import Vehicle
from .const import (
    BRAND_HYUNDAI,
    BRAND_KIA,
    BRANDS,
    DOMAIN,
    REGION_CANADA,
    REGION_EUROPE,
    REGION_USA,
    REGIONS,
    VEHICLE_LOCK_ACTION,
)
from .HyundaiBlueLinkAPIUSA import HyundaiBlueLinkAPIUSA
from .KiaUvoApiCA import KiaUvoApiCA
from .KiaUvoApiEU import KiaUvoApiEU
from .KiaUvoAPIUSA import KiaUvoAPIUSA
from .exceptions import VehicleNotFoundError

_LOGGER = logging.getLogger(__name__)


class VehicleManager:
    def __init__(self, region: int, brand: int, username: str, password: str, pin: str, geocode_api_enable: bool = False, geocode_api_use_email: bool = False):
        self.region: int = region
        self.brand: int = brand
        self.username: str = username
        self.password: str = password
        self.geocode_api_enable: bool = geocode_api_enable
        self.geocode_api_use_email: bool = geocode_api_use_email
        self.pin: str = pin

        self.api: ApiImpl = self.get_implementation_by_region_brand(
            self.region, self.brand
        )

        self.vehicles: list[Vehicle] = []

    def initialize(self) -> None:
        self.api.login(self.username, self.password, self.pin)
        vehicles = self.api.get_vehicles()
        for vehicle in vehicles:
            self.vehicles.append(vehicle)
        self.update_all_vehicles_with_cached_state()

    def get_vehicle(self, vehicle_id) -> Vehicle:
        for v in self.vehicles:
            if v.id == vehicle_id:
                return v
        raise VehicleNotFoundError("No vehicle found with this ID")

    def update_all_vehicles_with_cached_state(self) -> None:
        for vehicle in self.vehicles:
            vehicle.update_with_cached_state()


    def check_and_force_update_vehicles(self, force_refresh_interval: int) -> None:
        # Force refresh only if current data is older than the value bassed in seconds.  Otherwise runs a cached update.
        started_at_utc: dt = dt.datetime.now(pytz.utc)
        for vehicle in self.vehicles:
            _LOGGER.debug(
                f"{DOMAIN} - Time differential in seconds: {(started_at_utc - vehicle.last_updated_at).total_seconds()}"
            )
            if (
                started_at_utc - vehicle.last_updated_at
            ).total_seconds() > force_refresh_interval:
                vehicle.force_refresh_state()
            else:
                vehicle.update_with_cached_state()

    def force_refresh_all_vehicles_states(self) -> None:
        for vehicle in self.vehicles:
            vehicle.force_refresh_state()

    def check_and_refresh_token(self) -> bool:
        if self.api.token is None:
            self.initialize()
        if self.api.token.valid_until <= dt.datetime.now(pytz.utc):
            _LOGGER.debug(f"{DOMAIN} - Refresh token expired")
            self.api.refresh_vehicles(self.vehicles)
            return True
        return False


    @staticmethod
    def get_implementation_by_region_brand(region: int, brand: int) -> ApiImpl:
        if REGIONS[region] == REGION_CANADA:
            return KiaUvoApiCA(region, brand)
        elif REGIONS[region] == REGION_EUROPE:
            return KiaUvoApiEU(region, brand)
        elif REGIONS[region] == REGION_USA and BRANDS[brand] == BRAND_HYUNDAI:
            return HyundaiBlueLinkAPIUSA(region, brand)
        elif REGIONS[region] == REGION_USA and BRANDS[brand] == BRAND_KIA:
            return KiaUvoAPIUSA(region, brand)
