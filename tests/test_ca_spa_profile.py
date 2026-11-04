import datetime as dt
from unittest.mock import MagicMock, patch

from hyundai_kia_connect_api.const import VEHICLE_LOCK_ACTION
from hyundai_kia_connect_api.KiaUvoApiCA import KiaUvoApiCA
from hyundai_kia_connect_api.Token import Token
from hyundai_kia_connect_api.utils import offset_from_tz
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


def test_genesis_profile_is_app_spa() -> None:
    """Genesis CA app = stationdm.hca: SPA, brand G, own client keys (README apk_ca)."""
    h = KiaUvoApiCA(2, 3, "en").API_HEADERS
    assert h["from"] == "SPA"
    assert h["brand"] == "G"
    assert h["client_id"] == "HATAGSPACA0232141ED9722C67715A0B"
    assert h["client_secret"] == "CLISCR01AGSPA"
    assert h["User-Agent"].startswith("okhttp/")
    for dropped in ("Origin", "Referer", "Sec-Fetch-Site", "Accept"):
        assert dropped not in h, dropped


def test_offset_wired_from_tz(monkeypatch) -> None:
    monkeypatch.setattr(
        "hyundai_kia_connect_api.KiaUvoApiCA.offset_from_tz", lambda: "-8"
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
    api = KiaUvoApiCA(2, 1, "en")
    api._device_id = None
    assert api.refresh_access_token(token) is fresh
    assert calls == [("user@test.com", "pw", "1234", "PERSISTED")]


def test_refresh_genesis_uses_gtothbyrftkn_like_hyundai(monkeypatch) -> None:
    """Genesis app refreshes via gtothbyrftkn too (HCARemoteAPI:559)."""
    from hyundai_kia_connect_api import KiaUvoApiCA as _mod

    token = Token(
        username="u", password="p", pin="1234", device_id="DEV", refresh_token="RT"
    )
    api = KiaUvoApiCA(2, 3, "en")
    api._sessions = _HeaderCaptureSession()
    calls = []
    monkeypatch.setattr(
        _mod.KiaUvoApiCA,
        "login",
        lambda self, **kw: (
            calls.append(kw)
            or (_ for _ in ()).throw(AssertionError("Genesis must not re-login"))
        ),
    )
    api.refresh_access_token(token)
    assert calls == []


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
        "hyundai_kia_connect_api.KiaUvoApiCA.offset_from_tz", lambda: "-5"
    )
    api = _api()
    assert api.API_HEADERS["offset"] == "-5"
    monkeypatch.setattr(
        "hyundai_kia_connect_api.KiaUvoApiCA.offset_from_tz", lambda: "-8"
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
    assert offset_from_tz(_at(-5 * 60)) == "-5"


def test_offset_pacific_and_half_zone_truncate_toward_zero() -> None:
    assert offset_from_tz(_at(-8 * 60)) == "-8"
    assert offset_from_tz(_at(-3 * 60 - 30)) == "-3"  # St. John's, Java trunc
    assert offset_from_tz(_at(5 * 60 + 30)) == "5"


def test_offset_none_is_zero() -> None:
    assert offset_from_tz(dt.datetime(2026, 11, 3, tzinfo=None)) == "0"


def _capture_api_calls(monkeypatch, brand: int) -> tuple[KiaUvoApiCA, MagicMock]:
    """Return an api whose session captures (headers, url, json) per post."""
    from unittest.mock import MagicMock as MM

    api = KiaUvoApiCA(2, brand, "en")

    class Cap:
        def __init__(self) -> None:
            self.calls: list[tuple[dict, str]] = []

        def post(self, url, headers=None, json=None, data=None, timeout=None):
            self.calls.append((dict(headers or {}), url))
            resp = MM()
            resp.json.return_value = {
                "responseHeader": {"responseCode": 0},
                "result": {"pAuth": "PT", "expireIn": 3600},
            }
            resp.headers = {"transactionId": "TX"}
            resp.ok = True
            resp.status_code = 200
            return resp

    api._sessions = Cap()
    monkeypatch.setattr(KiaUvoApiCA, "_get_pin_token", lambda self, tok, veh: "PT")
    return api, api._sessions


def test_kia_location_and_setsoc_keep_proven_shapes(monkeypatch) -> None:
    """#1069: fndmcr needs from:SPA + Referer /remote/; #957: setsoc needs SPA + portal extras."""
    api, cap = _capture_api_calls(monkeypatch, brand=1)
    token = Token(username="u", password="p", pin="1234", device_id="DEV")
    vehicle = Vehicle(id="VEH")
    api._get_location(token, vehicle)
    api.set_charge_limits(token, vehicle, 50, 50)
    loc_hdr, loc_url = cap.calls[0]
    soc_hdr, soc_url = cap.calls[-1]
    assert loc_url.endswith("fndmcr")
    assert (
        loc_hdr["from"] == "SPA"
        and loc_hdr["Referer"] == "https://kiaconnect.ca/remote/"
    )
    assert soc_url.endswith("evc/setsoc")
    assert (
        soc_hdr["from"] == "SPA"
        and soc_hdr["Referer"] == "https://kiaconnect.ca/remote/"
    )
    assert soc_hdr["offset"] == "-8" and soc_hdr["priority"] == "u=1, i"


def test_hyundai_location_and_setsoc_carry_no_portal_extras(monkeypatch) -> None:
    api, cap = _capture_api_calls(monkeypatch, brand=2)
    token = Token(username="u", password="p", pin="1234", device_id="DEV")
    vehicle = Vehicle(id="VEH")
    api._get_location(token, vehicle)
    api.set_charge_limits(token, vehicle, 50, 50)
    loc_hdr, _ = cap.calls[0]
    soc_hdr, _ = cap.calls[-1]
    assert loc_hdr["from"] == "SPA"
    assert "Referer" not in loc_hdr
    assert soc_hdr["from"] == "SPA"
    assert "priority" not in soc_hdr and "Referer" not in soc_hdr
