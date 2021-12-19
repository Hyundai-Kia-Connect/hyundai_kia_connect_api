import asyncio
from typing import TypedDict


from .KiaUvoApiImpl import KiaUvoApiImpl
from .Token import Token
from .Vehicle import Vehicle



class VehicleManagerEntry(TypedDict):
    token: Token
    kia_uvo_api: KiaUvoApiImpl
    vehicle: Vehicle

class VehicleManager:
    def __init__(self):
        self.vehicles = {}

    def add(self, token, kia_uvo_api):
        vehicle: Vehicle = Vehicle(token.vehicle_name, token.vehicle_model, token.vehicle_id, token.vehicle_registration_date)
        vehicle_manager_entry: VehicleManagerEntry = VehicleManagerEntry(token, kia_uvo_api, vehicle)
        self.vehicles[token.vehicle_id] = vehicle_manager_entry

    def get_vehicle(self, vehicle_id):
        return self.vehicles[vehicle_id].vehicle

    def get_token(self, vehicle_id):
        return self.vehicles[vehicle_id].token

    def update_vehicle(self, vehicle_id):
        vehicle_manager_entry: VehicleManagerEntry = self.vehicles[vehicle_id]  
        vehicle.set_state(vehicle_manager_entry.kia_uvo_api.get_cached_vehicle_status(vehicle_manager_entry.token))

    def force_update_vehicle(self, vehicle_id):
        vehicle_manager_entry: VehicleManagerEntry = self.vehicles[vehicle_id]
        vehicle_manager_entry.kia_uvo_api.update_vehicle_status(vehicle_manager_entry.token)
        self.update_vehicle(vehicle_id)

    def check_and_refresh_token(self, vehicle_id):
        vehicle_manager_entry: VehicleManagerEntry = self.vehicles[vehicle_id]
        if vehicle_manager_entry.token.valid_until <= datetime.now().strftime(DATE_FORMAT):
            _LOGGER.debug(f"{DOMAIN} - Refresh token expired")
            vehicle_manager_entry.token = vehicle_manager_entry.kia_uvo_api.login()
            return True
        return False

        


