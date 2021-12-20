import logging

from datetime import datetime
import re
import traceback

from .const import *
from .utils import get_child_value

_LOGGER = logging.getLogger(__name__)

class Vehicle:
    def __init__(self, name, model, id, registration_date, data_time_zone, region, brand):
        # Init fields
        self.name = name
        self.model = model
        self.id = id
        self.registration_date = registration_date
        self.region = region
        self.brand = brand
        self.data_time_zone = data_time_zone

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


    def set_state(self, state):
        self.odometer = get_child_value(state, "odometer.value")
        self.set_last_updated(get_child_value(state, "vehicleStatus.time"))

    def set_last_updated(self, value):
        m = re.match(r"(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})", value)
        last_updated = datetime(
            year=int(m.group(1)),
            month=int(m.group(2)),
            day=int(m.group(3)),
            hour=int(m.group(4)),
            minute=int(m.group(5)),
            second=int(m.group(6)),
            tzinfo=self.data_time_zone,
        )

        _LOGGER.debug(f"{DOMAIN} - LastUpdated {last_updated} - Timezone {self.data_time_zone}")

        self.last_updated = last_updated