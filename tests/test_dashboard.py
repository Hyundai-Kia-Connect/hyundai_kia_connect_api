"""Tests for the HTML dashboard: trip collection, rendering and CLI prompting."""

import argparse
import datetime as dt
import json
import re
from types import SimpleNamespace
from typing import Any, ClassVar

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


class RecordingManager:
    """Stands in for VehicleManager so the CLI runs without the API."""

    seen: ClassVar[dict[str, Any]] = {}

    def __init__(self, **kwargs: Any) -> None:
        RecordingManager.seen = kwargs
        self.vehicles: dict[str, Any] = {}

    def check_and_refresh_token(self) -> None:
        pass

    def update_all_vehicles_with_cached_state(self) -> None:
        pass


def test_roadbook_takes_login_options_after_the_command(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        bluelink.hyundai_kia_connect_api, "VehicleManager", RecordingManager
    )
    out = tmp_path / "car.html"
    monkeypatch.setattr(
        "sys.argv",
        [
            "roadbook",
            "--region",
            "India",
            "--brand",
            "Kia",
            "--username",
            "u",
            "--password",
            "p",
            "--pin",
            "1234",
            "--no-open",
            "--output",
            str(out),
        ],
    )
    assert bluelink.roadbook() == 0
    assert RecordingManager.seen["region"] == 6
    assert RecordingManager.seen["brand"] == 1
    assert RecordingManager.seen["pin"] == "1234"
    assert out.read_text(encoding="utf-8").startswith("<!doctype html>")


def test_top_level_login_options_still_work(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        bluelink.hyundai_kia_connect_api, "VehicleManager", RecordingManager
    )
    out = tmp_path / "car.html"
    args = [
        "--region",
        "Europe",
        "--brand",
        "Hyundai",
        "--username",
        "u",
        "--password",
        "p",
        "--pin",
        "1",
        "dashboard",
        "--no-open",
        "--output",
        str(out),
    ]
    assert bluelink.main(args) == 0
    assert (RecordingManager.seen["region"], RecordingManager.seen["brand"]) == (1, 2)


def _request(url: str, method: str = "GET", host: str | None = None) -> tuple[int, str]:
    import urllib.error
    import urllib.request

    req = urllib.request.Request(
        url, method=method, data=b"" if method == "POST" else None
    )
    if host:
        req.add_header("Host", host)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as err:
        return err.code, ""


def test_server_serves_page_and_limits_refresh(monkeypatch) -> None:
    import threading

    builds: list[int] = []

    def build() -> dict[str, Any]:
        builds.append(1)
        return {"n": len(builds)}

    server, url = dashboard.make_server(build)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        status, page = _request(url)
        assert (
            status == 200
            and '"n":1' in page
            and f'"{url[len("http://127.0.0.1:") :].split("/", 1)[1]}refresh"'.replace(
                '"', ""
            )
            in page
        )

        # Within the minimum interval a refresh returns the cached data.
        status, body = _request(url + "refresh", "POST")
        assert (
            status == 200
            and json.loads(body)["data"] == {"n": 1}
            and json.loads(body)["wait"] > 0
        )

        monkeypatch.setattr(dashboard, "MIN_REFRESH_SECONDS", 0)
        assert json.loads(_request(url + "refresh", "POST")[1])["data"] == {"n": 2}

        # Wrong token or another Host header (DNS rebinding) gets nothing.
        assert _request(url.rsplit("/", 2)[0] + "/wrong/")[0] == 404
        assert _request(url, host="evil.example:80")[0] == 404
    finally:
        server.shutdown()
        server.server_close()


def test_saved_page_has_no_refresh_url() -> None:
    assert "const REFRESH_URL = null;" in dashboard.render({})


class FakeKeyring:
    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, account: str) -> str | None:
        return self.store.get((service, account))

    def set_password(self, service: str, account: str, secret: str) -> None:
        self.store[(service, account)] = secret

    def delete_password(self, service: str, account: str) -> None:
        del self.store[(service, account)]


def test_saved_login_keeps_secrets_out_of_the_file(monkeypatch, tmp_path) -> None:
    from hyundai_kia_connect_api import saved_login

    kr = FakeKeyring()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(saved_login, "_keyring", lambda: kr)

    assert saved_login.save("India", "Kia", "me@example.com", "s3cret", "1234")
    text = saved_login.path().read_text()
    assert "s3cret" not in text and "1234" not in text
    assert saved_login.path().stat().st_mode & 0o777 == 0o600
    assert saved_login.load() == {
        "region": "India",
        "brand": "Kia",
        "username": "me@example.com",
        "password": "s3cret",
        "pin": "1234",
    }
    assert saved_login.forget()
    assert saved_login.load() == {} and kr.store == {}


def test_saved_login_without_keyring_saves_no_secrets(monkeypatch, tmp_path) -> None:
    from hyundai_kia_connect_api import saved_login

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(saved_login, "_keyring", lambda: None)
    assert not saved_login.save("India", "Kia", "me@example.com", "s3cret", "1234")
    assert saved_login.load() == {
        "region": "India",
        "brand": "Kia",
        "username": "me@example.com",
    }
