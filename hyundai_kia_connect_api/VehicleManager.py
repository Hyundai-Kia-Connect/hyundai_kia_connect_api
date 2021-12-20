import logging

import asyncio
from dataclasses import dataclass
from datetime import datetime

from .KiaUvoApiImpl import KiaUvoApiImpl
from .Token import Token
from .Vehicle import Vehicle

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

@dataclass
class VehicleManagerEntry:
    token: Token
    api: KiaUvoApiImpl
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
        entry.vehicle.set_state(entry.api.get_cached_vehicle_status(entry.token))

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