from hyundai_kia_connect_api.lib.date import date_string_to_datetime
import pytest
from datetime import datetime


@pytest.mark.parametrize(
    "date_string, expected_date",
    [
        ("20250520", datetime(2025, 5, 20)),
        ("202505", datetime(2025, 5, 1)),
        ("20250520072602", datetime(2025, 5, 20, 7, 26, 2)),
    ],
)
def test_parse_date_from_string(date_string, expected_date):
    date = date_string_to_datetime(date_string)
    assert date == expected_date
