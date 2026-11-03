import datetime as dt
from unittest.mock import MagicMock, patch

from hyundai_kia_connect_api.const import VEHICLE_LOCK_ACTION
from hyundai_kia_connect_api.KiaUvoApiCA import KiaUvoApiCA, _offset_from_tz
from hyundai_kia_connect_api.Token import Token
from hyundai_kia_connect_api.Vehicle import Vehicle


def _at(minutes: int) -> dt.datetime:  # minutes east of UTC
    return dt.datetime(
        2026, 11, 3, 12, 0, tzinfo=dt.timezone(dt.timedelta(minutes=minutes))
    )


def _api(region: int = 2, brand: int = 1) -> KiaUvoApiCA:
    return KiaUvoApiCA(region, brand, "en")


def test_api_headers_profile() -> None:
    h = _api().API_HEADERS
    assert h["from"] == "SPA"
    assert h["language"] == "0"
    assert h["client_id"] and h["client_secret"]
    assert h["User-Agent"].startswith("okhttp/")
    for dropped in (
        "Origin",
        "Referer",
        "Sec-Fetch-Dest",
        "Sec-Fetch-Mode",
        "Sec-Fetch-Site",
        "Priority",
        "Pragma",
        "Cache-Control",
        "Accept",
        "Accept-Language",
        "brand",
    ):
        assert dropped not in h, dropped


def test_offset_wired_from_tz(monkeypatch) -> None:
    monkeypatch.setattr(
        "hyundai_kia_connect_api.KiaUvoApiCA._offset_from_tz", lambda: "-8"
    )
    assert KiaUvoApiCA(2, 1, "en").API_HEADERS["offset"] == "-8"


_EXPECTED_PROFILE_KEYS = {
    "User-Agent",
    "Accept-Encoding",
    "Content-Type",
    "from",
    "offset",
    "language",
    "client_id",
    "client_secret",
}


def test_profile_keys_exact() -> None:
    assert set(_api().API_HEADERS) == _EXPECTED_PROFILE_KEYS


def test_offset_recomputed_per_access(monkeypatch) -> None:
    monkeypatch.setattr(
        "hyundai_kia_connect_api.KiaUvoApiCA._offset_from_tz", lambda: "-5"
    )
    api = _api()
    assert api.API_HEADERS["offset"] == "-5"
    monkeypatch.setattr(
        "hyundai_kia_connect_api.KiaUvoApiCA._offset_from_tz", lambda: "-8"
    )
    assert api.API_HEADERS["offset"] == "-8"


class _HeaderCaptureSession:
    def __init__(self) -> None:
        self.captured: list[dict[str, str]] = []

    def post(self, url: str, headers=None, **kwargs):
        self.captured.append(dict(headers or {}))
        response = MagicMock()
        response.headers = {"transactionId": "TX"}
        response.json.return_value = {
            "responseHeader": {"responseCode": 0},
            "result": {"pAuth": "PAUTH"},
        }
        return response


def test_command_request_headers_isolated() -> None:
    api = _api()
    api._sessions = _HeaderCaptureSession()
    token = Token(username="u", password="p", pin="1234")
    vehicle = Vehicle(id="VEH")
    with patch.object(KiaUvoApiCA, "_get_pin_token", return_value="PT"):
        api.lock_action(token, vehicle, VEHICLE_LOCK_ACTION.LOCK)
    sent = api._sessions.captured[0]
    # Command headers look like the app: profile + per-call auth, nothing else
    assert sent["from"] == "SPA"
    assert sent["vehicleId"] == "VEH"
    assert sent["pAuth"] == "PT"
    # The shared profile must not keep command state after the call
    for leaked in ("accessToken", "vehicleId", "pAuth"):
        assert leaked not in api.API_HEADERS, leaked


def test_offset_east_coast() -> None:
    assert _offset_from_tz(_at(-5 * 60)) == "-5"


def test_offset_pacific_and_half_zone_truncate_toward_zero() -> None:
    assert _offset_from_tz(_at(-8 * 60)) == "-8"
    assert _offset_from_tz(_at(-3 * 60 - 30)) == "-3"  # St. John's, Java trunc
    assert _offset_from_tz(_at(5 * 60 + 30)) == "5"


def test_offset_none_is_zero() -> None:
    assert _offset_from_tz(dt.datetime(2026, 11, 3, tzinfo=None)) == "0"
