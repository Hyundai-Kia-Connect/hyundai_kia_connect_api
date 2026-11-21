"""Tests for the HTML dashboard: trip collection, rendering and CLI prompting."""

import argparse
import datetime as dt
import json
import re
from types import SimpleNamespace
from typing import Any

from hyundai_kia_connect_api import bluelink, const, dashboard
from hyundai_kia_connect_api.exceptions import APIError
from hyundai_kia_connect_api.Vehicle import (
    DayTripCounts,
    DayTripInfo,
    MonthTripInfo,
    TripInfo,
)


class FakeManager:
    """Answers month and day trip calls like VehicleManager, without the API."""

    def __init__(self, fail_month: str | None = None) -> None:
        self.vehicle = SimpleNamespace(month_trip_info=None, day_trip_info=None)
        self.fail_month = fail_month
        self.day_calls: list[str] = []

    def get_vehicle(self, vehicle_id: str) -> Any:
        return self.vehicle

    def update_month_trip_info(self, vehicle_id: str, yyyymm: str) -> None:
        if yyyymm == self.fail_month:
            raise APIError("no trips")
        self.vehicle.month_trip_info = MonthTripInfo(
            yyyymm=yyyymm,
            summary=TripInfo(distance=10),
            day_list=[DayTripCounts(yyyymmdd=yyyymm + "05", trip_count=1)],
        )

    def update_day_trip_info(self, vehicle_id: str, yyyymmdd: str) -> None:
        self.day_calls.append(yyyymmdd)
        self.vehicle.day_trip_info = DayTripInfo(
            yyyymmdd=yyyymmdd, trip_list=[TripInfo(hhmmss="101500", distance=10)]
        )


def test_months_back_crosses_year() -> None:
    assert dashboard._months_back(dt.date(2026, 2, 14), 3) == [
        "202602",
        "202601",
        "202512",
    ]


def test_collect_trips_fetches_every_day() -> None:
    vm = FakeManager()
    history = dashboard.collect_trips(vm, "v1", 2)  # type: ignore[arg-type]
    assert len(history) == 2
    assert len(vm.day_calls) == 2
    assert history[0]["days"][0].trip_list[0].hhmmss == "101500"


def test_collect_trips_stops_when_region_has_no_trips() -> None:
    month = dt.date.today().strftime("%Y%m")
    vm = FakeManager(fail_month=month)
    assert dashboard.collect_trips(vm, "v1", 3) == []  # type: ignore[arg-type]
    assert vm.day_calls == []


def test_render_embeds_data_safely() -> None:
    html = dashboard.render(
        {"name": "</script><b>", "when": dt.datetime(2026, 11, 21, 10, 23)}
    )
    assert dashboard.DATA_PLACEHOLDER not in html
    embedded = re.search(r"const DATA = (.*?);\n", html)
    assert embedded is not None
    assert "</script><b>" not in embedded.group(1)
    data = json.loads(embedded.group(1).replace("<\\/", "</"))
    assert data == {"name": "</script><b>", "when": "2026-11-21T10:23:00"}


def test_missing_region_and_brand_keep_old_defaults_without_terminal() -> None:
    args = argparse.Namespace(
        func=bluelink.cmd_info,
        region=None,
        brand=None,
        username="u",
        password="p",
        pin="1",
    )
    bluelink._ask_missing(args)
    assert (args.region, args.brand) == (const.REGION_CANADA, const.BRAND_HYUNDAI)
