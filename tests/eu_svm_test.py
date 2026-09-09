"""EU GSPA SVM — parser, media-library flow, and capability tests.

Response shapes follow the EU GSPA SVM endpoints
(svm/vehicles/{carId}/na-images[/tvId]); the scsDetail object mirrors the
USA SVM response shape with 0/1 ints for door states and a
yyyyMMddHHmmss (or epoch-ms) timestamp.
"""

import base64
import datetime as dt

import pytest

from hyundai_kia_connect_api.exceptions import APIError
from hyundai_kia_connect_api.GspaApiEU import (
    GspaApiEU,
    _parse_gspa_svm_timestamp,
    parse_svm_detail,
)
from hyundai_kia_connect_api.HyundaiCciApiEU import HyundaiCciApiEU
from hyundai_kia_connect_api.KiaCciApiEU import KiaCciApiEU
from hyundai_kia_connect_api.svm import SVMDetails, redact_svm_metadata
from hyundai_kia_connect_api.Token import Token
from hyundai_kia_connect_api.Vehicle import Vehicle


def _make_token():
    return Token(
        access_token="Bearer test-token",
        refresh_token="test-refresh",
        device_id="test-device",
        valid_until=dt.datetime.now(dt.UTC) + dt.timedelta(hours=1),
    )


def _make_vehicle():
    vehicle = Vehicle(id="CAR123")
    vehicle.ccu_ccs2_protocol_support = 1
    return vehicle


def _make_api():
    return HyundaiCciApiEU.__new__(HyundaiCciApiEU)


def _sample_scs_detail() -> dict:
    return {
        "svmImage": base64.b64encode(b"hello").decode("ascii"),
        "doorOpen": {
            "frontLeft": 0,
            "frontRight": 1,
            "backLeft": 0,
            "backRight": 1,
        },
        "trunkOpen": False,
        "sidemirrorOpen": True,
        "gpsDetail": {
            "coord": {"lat": 51.307836, "lon": 22.081975, "type": 0, "alt": 0.0},
            "head": 68,
            "speed": {"value": 0.0, "unit": 0},
            "time": "20260716200818",
        },
        "imageSize": [4472, 720],
        "installAngle": [1.0, 2.0],
        "boundaryArea": [10, 20, 30, 40],
        "validAngleofView": [1.5, 2.5],
    }


# ---------------------------------------------------------------------------
# parse_svm_detail
# ---------------------------------------------------------------------------


def test_parse_svm_detail_full():
    detail = parse_svm_detail(_sample_scs_detail())
    assert isinstance(detail, SVMDetails)
    assert detail.image_bytes == b"hello"
    assert detail.captured_at == dt.datetime(2026, 7, 16, 20, 8, 18, tzinfo=dt.UTC)
    assert detail.captured_at_raw == "20260716200818"
    assert detail.latitude == 51.307836
    assert detail.longitude == 22.081975
    assert detail.heading == 68
    assert detail.speed == (0.0, 0)
    # EU door values are 0/1 ints; normalized to booleans on shared keys.
    assert detail.door_open == {
        "frontLeft": False,
        "frontRight": True,
        "backLeft": False,
        "backRight": True,
    }
    assert detail.trunk_open is False
    assert detail.image_size == (4472, 720)


def test_parse_svm_detail_raw_metadata_keeps_coords_redacts_image():
    data = _sample_scs_detail()
    detail = parse_svm_detail(data)
    raw = detail.raw_metadata
    assert raw["svmImage"] == "<redacted>"
    # Coordinates deliberately preserved (gps=False): typed fields above
    # already carry them, advanced consumers get the rest of the response.
    assert raw["gpsDetail"]["coord"] == data["gpsDetail"]["coord"]
    # Fields without a typed slot on SVMDetails stay reachable here.
    assert raw["installAngle"] == [1.0, 2.0]
    assert raw["boundaryArea"] == [10, 20, 30, 40]
    assert raw["validAngleofView"] == [1.5, 2.5]
    assert raw["sidemirrorOpen"] is True


def test_parse_svm_detail_missing_fields_graceful():
    detail = parse_svm_detail({})
    assert detail.image_bytes == b""
    assert detail.captured_at is None
    assert detail.captured_at_raw is None
    assert detail.latitude is None
    assert detail.longitude is None
    assert detail.heading is None
    assert detail.speed == (None, None)
    assert detail.door_open is None
    assert detail.trunk_open is None
    assert detail.image_size is None


def test_parse_svm_detail_malformed_gps_no_crash():
    detail = parse_svm_detail(
        {
            "svmImage": "not!!base64!!",
            "gpsDetail": "not-a-dict",
            "doorOpen": "not-a-dict",
            "coord": "nope",
        }
    )
    assert detail.image_bytes == b""
    assert detail.door_open is None
    assert detail.latitude is None


# ---------------------------------------------------------------------------
# _parse_gspa_svm_timestamp
# ---------------------------------------------------------------------------


def test_svm_timestamp_14_digit_string():
    assert _parse_gspa_svm_timestamp("20260716200818") == dt.datetime(
        2026, 7, 16, 20, 8, 18, tzinfo=dt.UTC
    )


def test_svm_timestamp_epoch_ms():
    assert _parse_gspa_svm_timestamp(1715000000000) == dt.datetime(
        2024, 5, 6, 12, 53, 20, tzinfo=dt.UTC
    )


def test_svm_timestamp_epoch_seconds_by_magnitude():
    assert _parse_gspa_svm_timestamp(1715000000) == dt.datetime(
        2024, 5, 6, 12, 53, 20, tzinfo=dt.UTC
    )


def test_svm_timestamp_iso_and_garbage():
    assert _parse_gspa_svm_timestamp("2026-07-16T20:08:18+00:00") == dt.datetime(
        2026, 7, 16, 20, 8, 18, tzinfo=dt.UTC
    )
    assert _parse_gspa_svm_timestamp("garbage") is None
    assert _parse_gspa_svm_timestamp(None) is None
    assert _parse_gspa_svm_timestamp(True) is None


# ---------------------------------------------------------------------------
# get_svm_details media-library flow
# ---------------------------------------------------------------------------


def _patch_gspa_get(monkeypatch, handler):
    """Route all _gspa_get calls through handler(self, token, vehicle, endpoint, params)."""
    monkeypatch.setattr(GspaApiEU, "_gspa_get", handler)


def test_get_svm_details_selects_latest_media_set(monkeypatch):
    api = _make_api()
    token = _make_token()
    vehicle = _make_vehicle()
    requested = []

    def fake_gspa_get(self, token, vehicle, endpoint, params=None):
        requested.append(endpoint)
        if endpoint == "svm/vehicles/{carId}/na-images":
            return {
                "scsList": [
                    {"tvid": "tv-old", "date": 1715000000000},
                    {"tvid": "tv-new", "date": 1715000100000},
                    {"tvid": "tv-malformed", "date": "bad"},
                ]
            }
        return {"scsDetail": _sample_scs_detail()}

    _patch_gspa_get(monkeypatch, fake_gspa_get)
    details = api.get_svm_details(token, vehicle)
    assert requested[1] == "svm/vehicles/{carId}/na-images/tv-new"
    assert details.image_bytes == b"hello"


def test_get_svm_details_empty_media_library(monkeypatch):
    api = _make_api()
    token = _make_token()
    vehicle = _make_vehicle()
    monkeypatch.setattr(
        GspaApiEU,
        "_gspa_get",
        lambda self, token, vehicle, endpoint, params=None: {"scsList": []},
    )
    with pytest.raises(APIError, match="No SVM media"):
        api.get_svm_details(token, vehicle)


def test_get_svm_details_propagates_server_error(monkeypatch):
    api = _make_api()
    token = _make_token()
    vehicle = _make_vehicle()

    def fake_gspa_get(self, token, vehicle, endpoint, params=None):
        raise APIError("GSPA error: HTTP 500 500-000")

    monkeypatch.setattr(GspaApiEU, "_gspa_get", fake_gspa_get)
    with pytest.raises(APIError, match="500-000"):
        api.get_svm_details(token, vehicle)


def test_get_svm_details_missing_scs_detail(monkeypatch):
    api = _make_api()
    token = _make_token()
    vehicle = _make_vehicle()

    def fake_gspa_get(self, token, vehicle, endpoint, params=None):
        if endpoint == "svm/vehicles/{carId}/na-images":
            return {"scsList": [{"tvid": "tv1", "date": 1715000000000}]}
        return {"data": {}}

    monkeypatch.setattr(GspaApiEU, "_gspa_get", fake_gspa_get)
    with pytest.raises(Exception, match="scsDetail"):
        api.get_svm_details(token, vehicle)


# ---------------------------------------------------------------------------
# Capability flag + shared helpers
# ---------------------------------------------------------------------------


def test_supports_svm_flag_per_brand():
    assert HyundaiCciApiEU.supports_svm is True
    # Kia EU SVM awaits live verification — stays False there.
    assert KiaCciApiEU.supports_svm is False


def test_log_redaction_redacts_image_and_gps():
    """The log path (gps=True) redacts image and GPS-bearing keys."""
    data = _sample_scs_detail()
    redacted = redact_svm_metadata(data)
    assert redacted["svmImage"] == "<redacted>"
    assert redacted["gpsDetail"]["coord"] == "<redacted>"
    assert redacted["gpsDetail"]["head"] == "<redacted>"
    assert redacted["gpsDetail"]["time"] == "20260716200818"
