"""Unit tests for ApiImplType1._get_time_from_string.

Covers the unset-timer case (issue #1206): API returns "0000" when no
departure/off-peak timer is configured; the parser must return None
instead of raising ValueError. Also covers the preexisting timesection=None
TypeError and defense-in-depth against unexpected formats.

EU, AU, IN, and CN all inherit the base implementation (the CN and IN
overrides were removed as bit-for-bit duplicates).
"""

import datetime as dt

import pytest

from hyundai_kia_connect_api.ApiImplType1 import ApiImplType1


@pytest.fixture
def type1_api() -> ApiImplType1:
    # Instantiate without __init__ to avoid network/credential setup.
    api = ApiImplType1.__new__(ApiImplType1)
    return api


class TestGetTypeFromStringBase:
    """Covers EU, AU, IN, CN (all inherit the base)."""

    @pytest.mark.parametrize(
        "value,timesection,expected",
        [
            # unset-timer values -> None (issue #1206)
            ("0000", 0, None),
            ("0", 0, None),
            ("", 0, None),
            (0, 0, None),
            (None, 0, None),
            ("0000", None, None),
            # real timers
            ("0800", 0, dt.time(8, 0)),
            ("0800", 1, dt.time(20, 0)),
            ("2200", 0, dt.time(22, 0)),  # >1260 -> %H%M path
            ("1300", 1, dt.time(13, 0)),  # >1260 -> %H%M path
            # preexisting-bug fix: timesection=None no longer crashes
            ("0800", None, dt.time(8, 0)),
            # defense-in-depth: unexpected format -> None (no crash)
            ("ABCD", 0, None),
        ],
    )
    def test_parse(self, type1_api, value, timesection, expected):
        assert type1_api._get_time_from_string(value, timesection) == expected
