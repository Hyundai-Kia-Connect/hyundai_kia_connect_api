import datetime as dt
from unittest.mock import MagicMock, patch

from hyundai_kia_connect_api.ApiImpl import ApiImpl
from hyundai_kia_connect_api.Token import Token
from hyundai_kia_connect_api.Vehicle import Vehicle
from hyundai_kia_connect_api.VehicleManager import VehicleManager

WHEN = dt.datetime(2026, 1, 2, 3, 4, 5, tzinfo=dt.UTC)


class CachedStateApi(ApiImpl):
    def update_vehicle_with_cached_state(self, token, vehicle):
        pass


def _manager(monkeypatch, vehicles):
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
    )
    manager.token = Token(
        username="user",
        password="pass",
        valid_until=dt.datetime.now(dt.UTC) + dt.timedelta(hours=1),
    )
    manager.vehicles = {v.id: v for v in vehicles}
    return manager


def _osm_response(name):
    response = MagicMock()
    response.json.return_value = {"display_name": name, "address": {}}
    return response


def test_unchanged_coordinates_skip_geocode_per_vehicle(monkeypatch) -> None:
    first = Vehicle(id="vehicle-1")
    first.location = (10.0, 20.0, WHEN)
    second = Vehicle(id="vehicle-2")
    second.location = (30.0, 40.0, WHEN)
    manager = _manager(monkeypatch, [first, second])

    with patch(
        "hyundai_kia_connect_api.ApiImpl.requests.get",
        side_effect=lambda url, **kwargs: _osm_response(url),
    ) as get:
        manager.update_all_vehicles_with_cached_state()
        assert get.call_count == 2

        get.reset_mock()
        manager.update_all_vehicles_with_cached_state()
        get.assert_not_called()

        second.location = (31.0, 41.0, WHEN)
        manager.update_all_vehicles_with_cached_state()
        assert get.call_count == 1
        assert "lat=31.0" in get.call_args.args[0]

    assert first.geocode[0] is not None and "lat=10.0" in first.geocode[0]
    assert second.geocode[0] is not None and "lat=31.0" in second.geocode[0]
