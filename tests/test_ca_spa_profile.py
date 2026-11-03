import datetime as dt

from hyundai_kia_connect_api.KiaUvoApiCA import KiaUvoApiCA, _offset_from_tz


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


def test_offset_east_coast() -> None:
    assert _offset_from_tz(_at(-5 * 60)) == "-5"


def test_offset_pacific_and_half_zone_truncate_toward_zero() -> None:
    assert _offset_from_tz(_at(-8 * 60)) == "-8"
    assert _offset_from_tz(_at(-3 * 60 - 30)) == "-3"  # St. John's, Java trunc
    assert _offset_from_tz(_at(5 * 60 + 30)) == "5"


def test_offset_none_is_zero() -> None:
    assert _offset_from_tz(dt.datetime(2026, 11, 3, tzinfo=None)) == "0"
