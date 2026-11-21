"""Build a local HTML dashboard from a VehicleManager's vehicles."""

import dataclasses
import datetime as dt
import json
import logging
from importlib.resources import files
from typing import Any

from .exceptions import HyundaiKiaException
from .VehicleManager import VehicleManager

_LOGGER = logging.getLogger(__name__)

TEMPLATE = "dashboard.html"
DATA_PLACEHOLDER = "__DASHBOARD_DATA__"


class DashboardEncoder(json.JSONEncoder):
    def default(self, o: Any) -> Any:
        if dataclasses.is_dataclass(o) and not isinstance(o, type):
            return dataclasses.asdict(o)
        if isinstance(o, (dt.date, dt.datetime, dt.time)):
            return o.isoformat()
        # Enums, timezones and other library values: the page only displays them.
        return str(o)


def _months_back(today: dt.date, count: int) -> list[str]:
    months, first = [], today.replace(day=1)
    for _ in range(count):
        months.append(first.strftime("%Y%m"))
        first = (first - dt.timedelta(days=1)).replace(day=1)
    return months


def collect_trips(vm: VehicleManager, vehicle_id: str, months: int) -> list[Any]:
    """Month summaries with every day's trips, newest month first.

    Regions without trip info return an empty list.
    """
    vehicle = vm.get_vehicle(vehicle_id)
    history: list[Any] = []
    for yyyymm in _months_back(dt.date.today(), months):
        try:
            vm.update_month_trip_info(vehicle_id, yyyymm)
        except (NotImplementedError, HyundaiKiaException) as err:
            _LOGGER.info("Trip info unavailable for %s: %s", yyyymm, err)
            break
        month = vehicle.month_trip_info
        if month is None:
            continue
        days = []
        for day in month.day_list:
            if day.yyyymmdd is None:
                continue
            try:
                vm.update_day_trip_info(vehicle_id, day.yyyymmdd)
            except (NotImplementedError, HyundaiKiaException) as err:
                _LOGGER.info("Day trip info unavailable for %s: %s", day.yyyymmdd, err)
                continue
            if vehicle.day_trip_info is not None:
                days.append(vehicle.day_trip_info)
        history.append({"month": month, "days": days})
    return history


def render(data: dict[str, Any]) -> str:
    """Return the dashboard page with ``data`` embedded."""
    template = files(__package__).joinpath(TEMPLATE).read_text(encoding="utf-8")
    payload = json.dumps(data, cls=DashboardEncoder, separators=(",", ":"))
    # "</" inside the JSON would end the <script> element early.
    return template.replace(DATA_PLACEHOLDER, payload.replace("</", "<\\/"))
