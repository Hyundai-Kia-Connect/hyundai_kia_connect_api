import asyncio
import datetime as dt
import logging
from dataclasses import dataclass

import pytz

from .ApiImpl import ApiImpl
from .const import (BRAND_HYUNDAI, BRAND_KIA, BRANDS, DOMAIN, REGION_CANADA,
                    REGION_EUROPE, REGION_USA, REGIONS)
from .HyundaiBlueLinkAPIUSA import HyundaiBlueLinkAPIUSA
from .KiaUvoApiCA import KiaUvoApiCA
from .KiaUvoApiEU import KiaUvoApiEU
from .KiaUvoAPIUSA import KiaUvoAPIUSA
from .Vehicle import Vehicle

_LOGGER = logging.getLogger(__name__)


class VehicleManager:
    def __init__(self, region: int, brand: int, username: str, password: str, pin: str):
        self.region: int = region
        self.brand: int = brand
        self.username: str = username
        self.password: str = password
        self.pin: str = pin

        self.api: ApiImpl = self.get_implementation_by_region_brand(
            self.region, self.brand
        )

        self.token: token = None
        self.vehicles: dict = {}

    def initialize(self) -> None:
        self.token: Token = self.api.login(self.username, self.password)
        vehicles = self.api.get_vehicles(self.token)
        for vehicle in vehicles:
            self.vehicles[vehicle.id] = vehicle

    def get_vehicle(self, vehicle_id) -> Vehicle:
        return self.vehicles[vehicle_id]

    def update_all_vehicles_with_cached_state(self) -> None:
        for vehicle_id in self.vehicles.keys():
            self.update_vehicle_with_cached_state(vehicle_id)

    def update_vehicle_with_cached_state(self, vehicle_id) -> None:
        self.api.update_vehicle_with_cached_state(
            self.token, self.get_vehicle(vehicle_id)
        )

    def force_refresh_all_vehicles_states(self, vehicle_id) -> None:
        for vehicle_id in self.vehicles.keys():
            self.force_refresh_vehicle_state(vehicle_id)
        self.update_all_vehicles_with_cached_state()

    def force_refresh_vehicle_state(self, vehicle_id) -> None:
        self.api.force_refresh_vehicle_state(token, vehicle_id)

    def check_and_refresh_token(self) -> bool:
        if self.token is None:
            self.initialize()
        if self.token.valid_until <= dt.datetime.now(pytz.utc):
            _LOGGER.debug(f"{DOMAIN} - Refresh token expired")
            self.token = self.api.login(self.username, self.password)
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
