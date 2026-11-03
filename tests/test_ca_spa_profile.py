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


def _api(region: int = 2, brand: int = 2) -> KiaUvoApiCA:
    return KiaUvoApiCA(region, brand, "en")


def test_api_headers_profile() -> None:
    """Hyundai gets the SPA profile (the decompiled myHyundai CA app's shape)."""
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


def test_kia_profile_is_legacy_portal() -> None:
    """Kia keeps the proven web-portal profile (kiaconnect SPA login = 7404)."""
    h = KiaUvoApiCA(2, 1, "en").API_HEADERS
    assert h["from"] == "CWP"
    assert h["User-Agent"].startswith("Mozilla/5.0 (Linux; Android")
    assert h["Origin"] == "https://kiaconnect.ca"
    assert h["Referer"] == "https://kiaconnect.ca/login"
    assert h["offset"] == "-5"
    assert h["client_id"] and h["client_secret"]
    assert "brand" not in h


def test_genesis_profile_is_legacy_portal() -> None:
    h = KiaUvoApiCA(2, 3, "en").API_HEADERS
    assert h["from"] == "CWP"
    assert h["Origin"] == "https://genesisconnect.ca"
    assert h["Referer"] == "https://genesisconnect.ca/login"
    assert h["User-Agent"].startswith("Mozilla/5.0 (Linux; Android")
    assert h["offset"] == "-5"


def test_offset_wired_from_tz(monkeypatch) -> None:
    monkeypatch.setattr(
        "hyundai_kia_connect_api.KiaUvoApiCA._offset_from_tz", lambda: "-8"
    )
    assert KiaUvoApiCA(2, 2, "en").API_HEADERS["offset"] == "-8"


def test_refresh_relogs_in_for_kia_and_genesis(monkeypatch) -> None:
    """Pre-#1496 behavior: Kia/Genesis mint refresh through a full login."""
    token = Token(
        username="user@test.com",
        password="pw",
        pin="1234",
        device_id="PERSISTED",
    )
    from hyundai_kia_connect_api import KiaUvoApiCA as _mod

    fresh = Token(username="user@test.com", password="pw", pin="1234")
    calls: list[tuple] = []
    monkeypatch.setattr(
        _mod.KiaUvoApiCA,
        "login",
        lambda self, username=None, password=None, pin=None: (
            calls.append((username, password, pin, self._device_id)) or fresh
        ),
    )
    for brand in (1, 3):
        calls.clear()
        api = KiaUvoApiCA(2, brand, "en")
        api._device_id = None
        assert api.refresh_access_token(token) is fresh
        assert calls == [("user@test.com", "pw", "1234", "PERSISTED")]


def test_refresh_hyundai_keeps_gtothbyrftkn() -> None:
    api = KiaUvoApiCA(2, 2, "en")
    api._sessions = _HeaderCaptureSession()
    token = Token(username="u", password="p", pin="1234", refresh_token="RT")
    with patch.object(
        KiaUvoApiCA,
        "login",
        side_effect=AssertionError("Hyundai must not re-login on refresh"),
    ):
        api.refresh_access_token(token)
    sent = api._sessions.captured[-1]
    assert sent["Deviceid"]
    assert "accessToken" not in sent


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
            "result": {
                "pAuth": "PAUTH",
                "expireIn": 3600,
                "accessToken": "NEW-AT",
                "refreshToken": "NEW-RT",
            },
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
