"""Tests for EU country timezone detection."""

from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from hyundai_kia_connect_api.KiaUvoApiEU import KiaUvoApiEU
from hyundai_kia_connect_api.const import (
    BRAND_KIA,
    BRANDS,
    EU_COUNTRY_TIMEZONES,
    REGION_EUROPE,
    REGIONS,
)
from hyundai_kia_connect_api.Token import Token


def _make_eu_api() -> KiaUvoApiEU:
    return KiaUvoApiEU(
        region=[k for k, v in REGIONS.items() if v == REGION_EUROPE][0],
        brand=[k for k, v in BRANDS.items() if v == BRAND_KIA][0],
        language="en",
    )


def _make_token() -> Token:
    return Token(
        username="test@example.com",
        password="pass",
        access_token="at",
        refresh_token="rt",
        device_id="did",
        valid_until=None,
        stamp="stamp",
    )


class TestEUCountryTimezones:
    def test_all_values_are_valid_iana(self):
        """Every value in EU_COUNTRY_TIMEZONES must be a valid IANA timezone."""
        for country, tz_name in EU_COUNTRY_TIMEZONES.items():
            assert ZoneInfo(tz_name), f"{tz_name} is not a valid timezone for {country}"

    def test_known_countries_present(self):
        expected = ["pl", "de", "fr", "pt", "fi", "gb", "es", "it", "nl", "se"]
        for c in expected:
            assert c in EU_COUNTRY_TIMEZONES, f"{c} missing from EU_COUNTRY_TIMEZONES"

    def test_poland_maps_to_warsaw(self):
        assert EU_COUNTRY_TIMEZONES["pl"] == "Europe/Warsaw"

    def test_portugal_maps_to_lisbon(self):
        assert EU_COUNTRY_TIMEZONES["pt"] == "Europe/Lisbon"


class TestDetectUserTimezone:
    def test_default_timezone_is_berlin(self):
        api = _make_eu_api()
        assert api.data_timezone == ZoneInfo("Europe/Berlin")

    def test_detect_sets_timezone_from_country(self):
        api = _make_eu_api()
        mock_response = MagicMock()
        mock_response.json.return_value = {"country": "PL", "lang": "en"}
        mock_response.status_code = 200

        with patch(
            "hyundai_kia_connect_api.KiaUvoApiEU.requests.get",
            return_value=mock_response,
        ):
            with patch(
                "hyundai_kia_connect_api.KiaUvoApiEU._check_response_for_errors"
            ):
                api._detect_user_timezone(_make_token())

        assert api.data_timezone == ZoneInfo("Europe/Warsaw")

    def test_detect_portugal_timezone(self):
        api = _make_eu_api()
        mock_response = MagicMock()
        mock_response.json.return_value = {"country": "PT", "lang": "pt"}
        mock_response.status_code = 200

        with patch(
            "hyundai_kia_connect_api.KiaUvoApiEU.requests.get",
            return_value=mock_response,
        ):
            with patch(
                "hyundai_kia_connect_api.KiaUvoApiEU._check_response_for_errors"
            ):
                api._detect_user_timezone(_make_token())

        assert api.data_timezone == ZoneInfo("Europe/Lisbon")

    def test_detect_unknown_country_falls_back_to_berlin(self):
        api = _make_eu_api()
        mock_response = MagicMock()
        mock_response.json.return_value = {"country": "XX", "lang": "en"}
        mock_response.status_code = 200

        with patch(
            "hyundai_kia_connect_api.KiaUvoApiEU.requests.get",
            return_value=mock_response,
        ):
            with patch(
                "hyundai_kia_connect_api.KiaUvoApiEU._check_response_for_errors"
            ):
                api._detect_user_timezone(_make_token())

        assert api.data_timezone == ZoneInfo("Europe/Berlin")

    def test_detect_failure_keeps_default(self):
        api = _make_eu_api()

        with patch(
            "hyundai_kia_connect_api.KiaUvoApiEU.requests.get",
            side_effect=Exception("network error"),
        ):
            api._detect_user_timezone(_make_token())

        assert api.data_timezone == ZoneInfo("Europe/Berlin")

    def test_detect_lowercase_country(self):
        api = _make_eu_api()
        mock_response = MagicMock()
        mock_response.json.return_value = {"country": "fi", "lang": "fi"}
        mock_response.status_code = 200

        with patch(
            "hyundai_kia_connect_api.KiaUvoApiEU.requests.get",
            return_value=mock_response,
        ):
            with patch(
                "hyundai_kia_connect_api.KiaUvoApiEU._check_response_for_errors"
            ):
                api._detect_user_timezone(_make_token())

        assert api.data_timezone == ZoneInfo("Europe/Helsinki")
