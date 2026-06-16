"""Tests for KiaUvoApiCA Cloudflare __cf_bm cookie handling."""

import pytest
from unittest.mock import patch, MagicMock

from hyundai_kia_connect_api.KiaUvoApiCA import KiaUvoApiCA


@pytest.fixture
def ca_api():
    api = KiaUvoApiCA(region=2, brand=1, language="en")
    return api


class TestGetCloudflareCookie:
    def test_extracts_cf_bm_cookie(self, ca_api):
        """Should extract __cf_bm cookie from response."""
        mock_response = MagicMock()
        mock_response.cookies = {"__cf_bm": "abc123def456"}
        with patch("requests.get", return_value=mock_response):
            result = ca_api._get_cloudflare_cookie()
        assert result == "__cf_bm=abc123def456"

    def test_returns_empty_when_no_cf_bm(self, ca_api):
        """Should return empty string when __cf_bm not in cookies."""
        mock_response = MagicMock()
        mock_response.cookies = {"other_cookie": "value"}
        with patch("requests.get", return_value=mock_response):
            result = ca_api._get_cloudflare_cookie()
        assert result == ""

    def test_returns_empty_on_request_failure(self, ca_api):
        """Should return empty string when cookie fetch request fails."""
        with patch("requests.get", side_effect=Exception("connection error")):
            result = ca_api._get_cloudflare_cookie()
        assert result == ""
