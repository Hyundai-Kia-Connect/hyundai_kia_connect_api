import datetime
from zoneinfo import ZoneInfo
from hyundai_kia_connect_api.utils import (
    bool_or_none,
    detect_timezone_for_date,
    float_or_none,
)


def test_detect_timezone_for_date():
    pacific = ZoneInfo("Canada/Pacific")
    eastern = ZoneInfo("Canada/Eastern")
    # apiStartDate 20221214192418 and lastStatusDate 20221214112426 are from
    # https://github.com/Hyundai-Kia-Connect/kia_uvo/issues/488#issuecomment-1352038594
    apiStartDate = datetime.datetime(2022, 12, 14, 19, 24, 18, tzinfo=datetime.UTC)
    lastStatusDate = datetime.datetime(2022, 12, 14, 11, 24, 26)
    assert detect_timezone_for_date(lastStatusDate, apiStartDate, [pacific]) == pacific
    assert detect_timezone_for_date(lastStatusDate, apiStartDate, [eastern]) is None


def test_detect_timezone_for_date_newfoundland():
    # Newfoundland NDT is UTC-0230 (NST is UTC-0330). Yes, half an hour.
    tz = ZoneInfo("Canada/Newfoundland")
    now_utc = datetime.datetime(2025, 9, 21, 23, 4, 30, tzinfo=datetime.UTC)
    early = datetime.datetime(2025, 9, 21, 20, 34, 0)
    after = datetime.datetime(2025, 9, 21, 20, 34, 59)
    assert detect_timezone_for_date(early, now_utc, [tz]) == tz
    assert detect_timezone_for_date(after, now_utc, [tz]) == tz


def test_float_or_none_none():
    assert float_or_none(None) is None


def test_float_or_none_off_string():
    assert float_or_none("OFF") is None


def test_float_or_none_numeric_string():
    assert float_or_none("72") == 72.0
    assert isinstance(float_or_none("72"), float)


def test_float_or_none_int():
    assert float_or_none(72) == 72.0
    assert isinstance(float_or_none(72), float)


def test_float_or_none_decimal_string():
    assert float_or_none("72.5") == 72.5


def test_float_or_none_non_numeric():
    assert float_or_none("abc") is None


def test_bool_or_none_none():
    assert bool_or_none(None) is None


def test_bool_or_none_truthy():
    assert bool_or_none(1) is True
    assert bool_or_none("1") is True


def test_bool_or_none_falsy():
    assert bool_or_none(0) is False
    assert bool_or_none("") is False


def test_float_or_none_empty_string():
    assert float_or_none("") is None
