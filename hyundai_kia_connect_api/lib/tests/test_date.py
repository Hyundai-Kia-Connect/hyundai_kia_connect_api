from hyundai_kia_connect_api.lib.date import parse_date_from_string
import pytest
from datetime import date


@pytest.mark.parametrize(
    "date_string, expected_date",
    [
        ("20250520", date(2025, 5, 20)),
        ("202505", date(2025, 5, 1)),
    ],
)
def test_parse_date_from_string(date_string, expected_date):
    date = parse_date_from_string(date_string)
    assert date == expected_date
