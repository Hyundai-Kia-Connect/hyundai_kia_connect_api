import datetime as dt

from hyundai_kia_connect_api.KiaUvoApiCA import _offset_from_tz


def _at(minutes: int) -> dt.datetime:  # minutes east of UTC
    return dt.datetime(
        2026, 11, 3, 12, 0, tzinfo=dt.timezone(dt.timedelta(minutes=minutes))
    )


def test_offset_east_coast() -> None:
    assert _offset_from_tz(_at(-5 * 60)) == "-5"


def test_offset_pacific_and_half_zone_truncate_toward_zero() -> None:
    assert _offset_from_tz(_at(-8 * 60)) == "-8"
    assert _offset_from_tz(_at(-3 * 60 - 30)) == "-3"  # St. John's, Java trunc
    assert _offset_from_tz(_at(5 * 60 + 30)) == "5"


def test_offset_none_is_zero() -> None:
    assert _offset_from_tz(dt.datetime(2026, 11, 3, tzinfo=None)) == "0"
