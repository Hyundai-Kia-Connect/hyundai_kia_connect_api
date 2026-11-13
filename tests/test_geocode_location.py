import datetime as dt
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlsplit

import pytest
import requests

from hyundai_kia_connect_api.ApiImpl import ApiImpl
from hyundai_kia_connect_api.Token import Token
from hyundai_kia_connect_api.Vehicle import Vehicle
from hyundai_kia_connect_api.VehicleManager import VehicleManager

WHEN = dt.datetime(2026, 1, 2, 3, 4, 5, tzinfo=dt.UTC)


class CachedStateApi(ApiImpl):
    def update_vehicle_with_cached_state(self, token, vehicle):
        pass


def _manager(monkeypatch, vehicles, username="user", use_email=False):
    monkeypatch.setattr(
        VehicleManager,
        "get_implementation_by_region_brand",
        lambda *args, **kwargs: CachedStateApi(),
    )
    manager = VehicleManager(
        region=3,
        brand=1,
        username="user",
        password="pass",
        pin="1234",
        geocode_api_enable=True,
        geocode_provider=1,
        geocode_api_use_email=use_email,
    )
    manager.token = Token(
        username=username,
        password="pass",
        valid_until=dt.datetime.now(dt.UTC) + dt.timedelta(hours=1),
    )
    manager.vehicles = {v.id: v for v in vehicles}
    return manager


def _sent_query(url, params=None, **kwargs):
    prepared = requests.Request("GET", url, params=params).prepare()
    return parse_qs(urlsplit(prepared.url).query)


def _osm_response(url, **kwargs):
    response = MagicMock()
    lat = _sent_query(url, **kwargs)["lat"][0]
    response.json.return_value = {"display_name": f"lat={lat}", "address": {}}
    return response


def test_unchanged_coordinates_skip_geocode_per_vehicle(monkeypatch) -> None:
    first = Vehicle(id="vehicle-1")
    first.location = (10.0, 20.0, WHEN)
    second = Vehicle(id="vehicle-2")
    second.location = (30.0, 40.0, WHEN)
    manager = _manager(monkeypatch, [first, second])

    with patch(
        "hyundai_kia_connect_api.ApiImpl.requests.get",
        side_effect=_osm_response,
    ) as get:
        manager.update_all_vehicles_with_cached_state()
        assert get.call_count == 2

        get.reset_mock()
        manager.update_all_vehicles_with_cached_state()
        get.assert_not_called()

        second.location = (31.0, 41.0, WHEN)
        manager.update_all_vehicles_with_cached_state()
        assert get.call_count == 1
        assert _sent_query(*get.call_args.args, **get.call_args.kwargs)["lat"] == [
            "31.0"
        ]

    assert first.geocode[0] is not None and "lat=10.0" in first.geocode[0]
    assert second.geocode[0] is not None and "lat=31.0" in second.geocode[0]


@pytest.mark.parametrize(
    "username", ["me+kia@example.com", "a&b@example.com", "x#1@example.com"]
)
def test_nominatim_email_is_url_encoded(monkeypatch, username) -> None:
    vehicle = Vehicle(id="vehicle-1")
    vehicle.location = (10.0, 20.0, WHEN)
    manager = _manager(monkeypatch, [vehicle], username=username, use_email=True)

    with patch(
        "hyundai_kia_connect_api.ApiImpl.requests.get", side_effect=_osm_response
    ) as get:
        manager.update_all_vehicles_with_cached_state()

    query = _sent_query(*get.call_args.args, **get.call_args.kwargs)
    assert query["email"] == [username]
    assert query["lat"] == ["10.0"] and query["lon"] == ["20.0"]
