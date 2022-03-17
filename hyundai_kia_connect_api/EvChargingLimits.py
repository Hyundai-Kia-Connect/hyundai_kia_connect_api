import logging

from .const import *

_LOGGER = logging.getLogger(__name__)

class EvChargingLimits:
    def __init__(self, dc_charging_limit: int, ac_charging_limit: int):
        self.dc_charging_limit = dc_charging_limit
        self.ac_charging_limit = ac_charging_limit

    @property
    def dc_charging_limit(self) -> int:
        return self._dc_charging_limit

    @dc_charging_limit.setter
    def dc_charging_limit(self, value: int):
        if value < 50 or value > 100 or value % 10 != 0:
            raise ValueError("Charging limit must be between 50 and 100 and divisible by 10.")
        self._dc_charging_limit = value
        
    @property
    def ac_charging_limit(self) -> int:
        return self._ac_charging_limit

    @ac_charging_limit.setter
    def ac_charging_limit(self, value: int):
        if value < 50 or value > 100 or value % 10 != 0:
            raise ValueError("Charging limit must be between 50 and 100 and divisible by 10.")
        self._ac_charging_limit = value
        
