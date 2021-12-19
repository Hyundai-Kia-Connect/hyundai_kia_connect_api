import logging

from datetime import datetime
import re
import traceback

from .const import *

_LOGGER = logging.getLogger(__name__)

class Vehicle:
    def __init__(self, name, model, id, registration_date):
        # Init fields
        self.name = name
        self.model = model
        self.id = id
        self.registration_date = registration_date

        # Shared
        self.engine_type = None
        self.total_driving_distance = None
        self.odometer = None
        self.car_battery = None

        # EV fields
        self.ev_battery_percentage = None
        self.ev_driving_distance = None
        self.estimated_current_charge_duration = None
        self.estimated_fast_charge_duration = None
        self.estimated_portable_charge_duration = None
        self.estimated_station_charge_duration = None

        # IC fields
        self.fuel_driving_distance = None

        self.last_updated: datetime = datetime.min

    def get_child_value(self, data, key):
        value = data
        for x in key.split("."):
            try:
                value = value[x]
            except:
                try:
                    value = value[int(x)]
                except:
                    value = None
        return

    def set_state(self, state):
        self.odometer = get_child_value(state, "odometer.value")
        self.set_last_updated(get_child_value(state, "vehicleStatus.time"))