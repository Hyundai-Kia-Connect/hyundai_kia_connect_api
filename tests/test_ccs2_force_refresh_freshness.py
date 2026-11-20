"""CCS2 force refresh (EU, AU): apply /latest only when the car reported after the wake."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from hyundai_kia_connect_api.exceptions import (
    APIError,
    RequestTimeoutError,
    ServiceTemporaryUnavailable,
)
from hyundai_kia_connect_api.KiaUvoApiAU import KiaUvoApiAU
from hyundai_kia_connect_api.KiaUvoApiEU import KiaUvoApiEU
from hyundai_kia_connect_api.Vehicle import Vehicle

OLD = "20261107114418.000"
NEW = "20261107150214.000"
WAKE_ACK = {"retCode": "S", "resCode": "0000", "msgId": "m1"}
TMU_TIMEOUT = {
    "retCode": "F",
    "resCode": "4006",
    "resMsg": "Remote control unavailable(tmu timeout)",
}


def _snapshot(date: str | None) -> dict[str, Any]:
    vehicle = {"Date": date} if date else {}
    return {
        "retCode": "S",
        "resCode": "0000",
        "resMsg": {"state": {"Vehicle": vehicle}},
    }


def _make_api(cls: type) -> Any:
    api = cls.__new__(cls)
    api.SPA_API_URL = "https://test.invalid/api/v1/spa/"
    api.session = MagicMock()
    api._get_authenticated_headers = MagicMock(
        return_value={"Authorization": "Bearer x"}
    )
    api._update_vehicle_properties_ccs2 = MagicMock()
    # The post-refresh location read differs per region; stub both.
    api._set_cached_location_park = MagicMock()
    api._get_location = MagicMock(return_value=None)
    return api


@pytest.fixture(params=[KiaUvoApiEU, KiaUvoApiAU], ids=["eu", "au"])
def api(request: pytest.FixtureRequest) -> Any:
    return _make_api(request.param)


@pytest.fixture
def vehicle() -> Vehicle:
    v = Vehicle(id="vid-123")
    v.ccu_ccs2_protocol_support = 1
    return v


TOKEN = SimpleNamespace(access_token="t", device_id="d")


def _run(
    api: Any,
    vehicle: Vehicle,
    wake: Any,
    latest: list[Any],
    sleeps: list[float] | None = None,
) -> tuple[list[str], list[float]]:
    """Run the force refresh; return (GET urls, sleep calls)."""
    urls: list[str] = []
    reads = iter(latest)

    def mock_get(url: str, headers: Any = None) -> MagicMock:
        urls.append(url)
        resp = MagicMock()
        body = wake if url.endswith("/ccs2/carstatus") else next(reads)
        if isinstance(body, Exception):
            resp.json.side_effect = body
        else:
            resp.json.return_value = body
        return resp

    sleeps = [] if sleeps is None else sleeps
    with (
        patch.object(api.session, "get", side_effect=mock_get),
        patch(
            "hyundai_kia_connect_api.ApiImplType1.sleep",
            side_effect=sleeps.append,
        ),
    ):
        api._force_refresh_vehicle_state_ccs2(TOKEN, vehicle)
    return urls, sleeps


def _location_read(api: Any) -> MagicMock:
    if isinstance(api, KiaUvoApiEU):
        return api._set_cached_location_park
    return api._get_location


def test_wakes_waits_25_s_and_applies_a_newer_snapshot(api, vehicle):
    urls, sleeps = _run(api, vehicle, WAKE_ACK, [_snapshot(OLD), _snapshot(NEW)])

    assert [u.rsplit("/", 1)[-1] for u in urls] == ["latest", "carstatus", "latest"]
    assert sleeps == [25]
    api._update_vehicle_properties_ccs2.assert_called_once_with(vehicle, {"Date": NEW})
    _location_read(api).assert_called_once_with(TOKEN, vehicle)


def test_raises_instead_of_applying_a_stale_snapshot(api, vehicle):
    # A car that didn't report must not look like a successful refresh.
    with pytest.raises(RequestTimeoutError):
        _run(api, vehicle, WAKE_ACK, [_snapshot(OLD), _snapshot(OLD)])

    api._update_vehicle_properties_ccs2.assert_not_called()
    _location_read(api).assert_not_called()


def test_raises_when_the_snapshot_loses_its_date(api, vehicle):
    with pytest.raises(RequestTimeoutError):
        _run(api, vehicle, WAKE_ACK, [_snapshot(OLD), _snapshot(None)])

    api._update_vehicle_properties_ccs2.assert_not_called()


def test_wake_error_code_raises_without_waiting(api, vehicle):
    sleeps: list[float] = []
    with pytest.raises(APIError, match="4006"):
        _run(api, vehicle, TMU_TIMEOUT, [_snapshot(OLD), _snapshot(NEW)], sleeps)

    assert sleeps == []

    api._update_vehicle_properties_ccs2.assert_not_called()
    _location_read(api).assert_not_called()


def test_wake_non_json_propagates(api, vehicle):
    with pytest.raises(ValueError, match="not JSON"):
        _run(api, vehicle, ValueError("not JSON"), [_snapshot(OLD), _snapshot(NEW)])

    api._update_vehicle_properties_ccs2.assert_not_called()


def test_applies_the_read_when_there_was_no_baseline(api, vehicle):
    _, sleeps = _run(api, vehicle, WAKE_ACK, [_snapshot(None), _snapshot(NEW)])

    assert sleeps == [25]
    api._update_vehicle_properties_ccs2.assert_called_once_with(vehicle, {"Date": NEW})


@pytest.mark.parametrize("date", [None, ""])
def test_snapshots_without_a_date_still_refresh(api, vehicle, date):
    undated = {"retCode": "S", "resMsg": {"state": {"Vehicle": {"Date": date}}}}
    _, sleeps = _run(api, vehicle, WAKE_ACK, [undated, undated])

    assert sleeps == [25]
    api._update_vehicle_properties_ccs2.assert_called_once()


def test_baseline_read_error_raises_before_the_wake(api, vehicle):
    unavailable = {"retCode": "F", "resCode": "5031", "resMsg": "unavailable"}
    # A wake would raise ValueError, so ServiceTemporaryUnavailable proves none.
    with pytest.raises(ServiceTemporaryUnavailable):
        _run(api, vehicle, ValueError("woke"), [unavailable])
