import datetime
from zoneinfo import ZoneInfo
from hyundai_kia_connect_api.utils import detect_timezone_for_date


def test_detect_timezone_for_date():
    pacific = ZoneInfo("Canada/Pacific")
    eastern = ZoneInfo("Canada/Eastern")
    # apiStartDate 20221214192418 and lastStatusDate 20221214112426 are from
    # https://github.com/Hyundai-Kia-Connect/kia_uvo/issues/488#issuecomment-1352038594
    apiStartDate = datetime.datetime(
        2022, 12, 14, 19, 24, 18, tzinfo=datetime.timezone.utc
    )
    lastStatusDate = datetime.datetime(2022, 12, 14, 11, 24, 26)
    assert detect_timezone_for_date(lastStatusDate, apiStartDate, [pacific]) == pacific
    assert detect_timezone_for_date(lastStatusDate, apiStartDate, [eastern]) is None


def test_detect_timezone_for_date_newfoundland():
    # Newfoundland NDT is UTC-0230 (NST is UTC-0330). Yes, half an hour.
    tz = ZoneInfo("Canada/Newfoundland")
    now_utc = datetime.datetime(2025, 9, 21, 23, 4, 30, tzinfo=datetime.timezone.utc)
    early = datetime.datetime(2025, 9, 21, 20, 34, 0)
    after = datetime.datetime(2025, 9, 21, 20, 34, 59)
    assert detect_timezone_for_date(early, now_utc, [tz]) == tz
    assert detect_timezone_for_date(after, now_utc, [tz]) == tz
