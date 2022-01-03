import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime

from .const import (BRAND_HYUNDAI, BRAND_KIA, BRANDS, REGION_CANADA,
                    REGION_EUROPE, REGION_USA, REGIONS, DOMAIN)
from .HyundaiBlueLinkAPIUSA import HyundaiBlueLinkAPIUSA
from .KiaUvoApiCA import KiaUvoApiCA
from .KiaUvoApiEU import KiaUvoApiEU
from .ApiImpl import ApiImpl
from .KiaUvoAPIUSA import KiaUvoAPIUSA

from .Token import Token
from .Vehicle import Vehicle

_LOGGER = logging.getLogger(__name__)

@dataclass
class VehicleManagerEntry:
    token: Token
    api: ApiImpl
    vehicle: Vehicle

class VehicleManager:
    def __init__(self):
        self.vehicles = {}

    def add(self, token, api):
        vehicle: Vehicle = Vehicle(token.vehicle_name, token.vehicle_model, token.vehicle_id, token.vehicle_registration_date, api.data_timezone, api.region, api.brand)
        entry: VehicleManagerEntry = VehicleManagerEntry(token=token, api=api, vehicle=vehicle)
        self.vehicles[token.vehicle_id] = entry
        return vehicle

    def get_vehicle(self, vehicle_id):
        return self.vehicles[vehicle_id].vehicle

    def get_token(self, vehicle_id):
        return self.vehicles[vehicle_id].token

    def update_vehicle(self, vehicle_id):
        entry: VehicleManagerEntry = self.vehicles[vehicle_id]
        entry.vehicle.set_state(entry.api.get_cached_vehicle_status(entry.token), entry.api.data_map)

    def force_update_vehicle(self, vehicle_id):
        entry: VehicleManagerEntry = self.vehicles[vehicle_id]
        entry.api.update_vehicle_status(entry.token)
        self.update_vehicle(vehicle_id)

    def check_and_refresh_token(self, vehicle_id):
        entry: VehicleManagerEntry = self.vehicles[vehicle_id]
        if entry.token.valid_until <= datetime.now().strftime(entry.api.data_date_format):
            _LOGGER.debug(f"{DOMAIN} - Refresh token expired")
            entry.token = entry.api.login()
            return True
        return False
    
    def get_implementation_by_region_brand(
        self,
        region: int,
        brand: int,
        username: str,
        password: str,
        pin: str = "",
    ) -> ApiImpl:  # pylint: disable=too-many-arguments
        if REGIONS[region] == REGION_CANADA:
            return KiaUvoApiCA(
                username, password, region, brand, pin
            )
        elif REGIONS[region] == REGION_EUROPE:
            return KiaUvoApiEU(
                username, password, region, brand, pin
            )
        elif REGIONS[region] == REGION_USA and BRANDS[brand] == BRAND_HYUNDAI:
            return HyundaiBlueLinkAPIUSA(
                username, password, region, brand, pin
            )
        elif REGIONS[region] == REGION_USA and BRANDS[brand] == BRAND_KIA:
            return KiaUvoAPIUSA(
                username, password, region, brand, pin
            )
