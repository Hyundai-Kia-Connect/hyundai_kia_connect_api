"""Regression tests for the C1 stamp-region fix (final review, PR #1284).

The library region id (REGIONS enum, 9 = Europe CCI) is a different
namespace from the stamp-region code the SDK cipher expects (EU = 1).
These tests pin the production wiring: an API instance constructed with
region 9 (as VehicleManager does) must compute GSPA stamps with the EU
stamp region (1) — not 9, which selects the JP IV.

Note: brand is passed as an int (2 = Hyundai, 1 = Kia) matching
const.BRANDS keys.
"""

import datetime as dt
from types import SimpleNamespace

from hyundai_kia_connect_api import HyundaiCciApiEU
from hyundai_kia_connect_api.gspa.cipher_keys import compute_x_stamp
from hyundai_kia_connect_api.Token import Token


def _make_token(**overrides) -> Token:
    """Create a Token instance with sensible CCI defaults for testing."""
    defaults = {
        "username": "user@test.com",
        "password": "MyPassword123!",
        "access_token": "Bearer ccs-token",
        "refresh_token": "REFRESHTOKEN1234567890123456789012345678901234567890",
        "device_id": "12345678-1234-1234-1234-123456789abc",
        "valid_until": dt.datetime.now(dt.UTC) + dt.timedelta(hours=1),
        "user_id": "stamp-uid",
    }
    defaults.update(overrides)
    return Token(**defaults)


def _capture_region(api):
    """Patch the instance cipher's compute_x_stamp to capture the region
    kwarg while delegating to the real implementation.

    ``api._cipher`` is a module-level singleton; monkeypatch (used by the
    caller) restores the original method.
    """
    real_compute = api._get_cipher().compute_x_stamp
    captured = {}

    def spy(region=1, tsid=None, epoch_seconds=None, user_id=""):
        captured["region"] = region
        return real_compute(
            region=region, tsid=tsid, epoch_seconds=epoch_seconds, user_id=user_id
        )

    return spy, captured


# ── Production wiring: region 9 -> stamp region 1 ──────────────


def test_region9_instance_stamps_with_region_1(monkeypatch):
    """A HyundaiCciApiEU built with region 9 (Europe CCI, the value
    VehicleManager passes) computes stamps with the EU stamp region 1
    (regression for C1: region 9 selected the JP IV)."""
    api = HyundaiCciApiEU(9, 2, "en")
    spy, captured = _capture_region(api)
    monkeypatch.setattr(api._get_cipher(), "compute_x_stamp", spy)

    stamp, tsid = api._get_stamp(_make_token())

    assert captured["region"] == 1
    assert stamp is not None
    assert tsid is not None


def test_vehicle_manager_region9_stamps_with_region_1(monkeypatch):
    """Exact production path (reviewer Rec. 5): VehicleManager routing
    (region 9, Hyundai) -> API impl -> _get_stamp computes with the EU
    stamp region 1."""
    from hyundai_kia_connect_api.VehicleManager import VehicleManager

    api = VehicleManager.get_implementation_by_region_brand(9, 2, "en")
    assert isinstance(api, HyundaiCciApiEU)
    spy, captured = _capture_region(api)
    monkeypatch.setattr(api._get_cipher(), "compute_x_stamp", spy)

    stamp, tsid = api._get_stamp(_make_token())

    assert captured["region"] == 1
    assert stamp is not None
    assert tsid is not None


def test_region9_stamp_bit_exact_matches_region_1_reference(monkeypatch):
    """Bit-exact pin: the stamp from a region-9 instance equals the
    reference compute_x_stamp(region=1, ...) for identical pinned inputs
    (tsid/epoch/user_id). If _get_stamp passed region 9, the cipher would
    select the JP IV and produce a different stamp."""
    api = HyundaiCciApiEU(9, 2, "en")
    pinned_tsid = "pinned-tsid-value"
    pinned_time = dt.datetime(2025, 6, 1, 12, 0, 0, tzinfo=dt.UTC)

    class _FakeDateTime:
        @staticmethod
        def now(tz=None):
            return pinned_time

    monkeypatch.setattr(
        "hyundai_kia_connect_api.GspaApiEU.create_tsid", lambda device_id: pinned_tsid
    )
    monkeypatch.setattr(
        "hyundai_kia_connect_api.GspaApiEU.dt",
        SimpleNamespace(datetime=_FakeDateTime, UTC=dt.UTC),
    )
    epoch_seconds = int(pinned_time.timestamp())

    stamp, tsid = api._get_stamp(_make_token(user_id="stamp-uid"))

    assert tsid == pinned_tsid
    assert stamp == compute_x_stamp(
        region=1,
        tsid=pinned_tsid,
        epoch_seconds=epoch_seconds,
        user_id="stamp-uid",
    )
