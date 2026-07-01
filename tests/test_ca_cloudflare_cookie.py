"""Tests for KiaUvoApiCA Cloudflare __cf_bm cookie handling."""

import datetime as dt

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


class TestCloudflareCookieInLogin:
    def test_login_includes_cloudflare_cookie(self, ca_api):
        """Login request should include Cookie header with __cf_bm."""
        mock_cf_response = MagicMock()
        mock_cf_response.cookies = {"__cf_bm": "test_cookie_value"}

        mock_login_response = MagicMock()
        mock_login_response.json.return_value = {
            "responseHeader": {"responseCode": 0},
            "result": {
                "token": {
                    "accessToken": "test_at",
                    "refreshToken": "test_rt",
                    "expireIn": 3600,
                }
            },
        }

        with (
            patch("requests.get", return_value=mock_cf_response),
            patch.object(
                ca_api.sessions, "post", return_value=mock_login_response
            ) as mock_post,
        ):
            ca_api.login("user@test.com", "password123")
            call_kwargs = mock_post.call_args
            headers = call_kwargs.kwargs.get(
                "headers", call_kwargs[1].get("headers", {})
            )
            assert "Cookie" in headers
            assert "__cf_bm=test_cookie_value" in headers["Cookie"]


class TestEnsureCloudflareCookie:
    def test_proactive_refresh_after_25_min(self, ca_api):
        """Should proactively refresh cookie after 25 minutes."""
        ca_api._cloudflare_cookie = "__cf_bm=old_cookie"
        ca_api._cloudflare_cookie_fetched_at = dt.datetime.now(
            dt.timezone.utc
        ) - dt.timedelta(minutes=26)

        mock_cf_response = MagicMock()
        mock_cf_response.cookies = {"__cf_bm": "new_cookie"}

        with patch("requests.get", return_value=mock_cf_response):
            result = ca_api._ensure_cloudflare_cookie()
        assert result == "__cf_bm=new_cookie"

    def test_skip_refresh_when_fresh(self, ca_api):
        """Should not refresh cookie when less than 25 minutes old."""
        ca_api._cloudflare_cookie = "__cf_bm=fresh_cookie"
        ca_api._cloudflare_cookie_fetched_at = dt.datetime.now(dt.timezone.utc)

        with patch("requests.get") as mock_get:
            result = ca_api._ensure_cloudflare_cookie()
        mock_get.assert_not_called()
        assert result == "__cf_bm=fresh_cookie"

    def test_fetch_when_no_cookie(self, ca_api):
        """Should fetch cookie when none exists yet."""
        ca_api._cloudflare_cookie = ""
        ca_api._cloudflare_cookie_fetched_at = None

        mock_cf_response = MagicMock()
        mock_cf_response.cookies = {"__cf_bm": "first_cookie"}

        with patch("requests.get", return_value=mock_cf_response):
            result = ca_api._ensure_cloudflare_cookie()
        assert result == "__cf_bm=first_cookie"


class TestAddCloudflareCookie:
    def test_adds_cookie_to_headers(self, ca_api):
        """Should add Cloudflare cookie to headers dict."""
        ca_api._cloudflare_cookie = "__cf_bm=test_value"
        ca_api._cloudflare_cookie_fetched_at = dt.datetime.now(dt.timezone.utc)

        headers = {"accessToken": "test"}
        with patch("requests.get"):  # _ensure won't call since fresh
            result = ca_api._add_cloudflare_cookie(headers)
        assert "Cookie" in result
        assert result["Cookie"] == "__cf_bm=test_value"

    def test_no_cookie_key_when_empty(self, ca_api):
        """Should not add Cookie key when no cookie available."""
        ca_api._cloudflare_cookie = ""
        ca_api._cloudflare_cookie_fetched_at = None

        headers = {"accessToken": "test"}
        with patch("requests.get", return_value=MagicMock()):
            result = ca_api._add_cloudflare_cookie(headers)
        assert "Cookie" not in result


class TestIsCloudflareBlock:
    def test_403_html_is_cloudflare(self, ca_api):
        """403 with HTML content type is a Cloudflare block."""
        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.headers = {"Content-Type": "text/html"}
        mock_response.text = "<html>Attention Required</html>"
        assert ca_api._is_cloudflare_block(mock_response) is True

    def test_403_with_cloudflare_in_body(self, ca_api):
        """403 with 'cloudflare' in body is a Cloudflare block."""
        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.headers = {"Content-Type": "text/plain"}
        mock_response.text = "Cloudflare attention required"
        assert ca_api._is_cloudflare_block(mock_response) is True

    def test_403_json_not_cloudflare(self, ca_api):
        """403 with JSON content type is NOT a Cloudflare block."""
        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.headers = {"Content-Type": "application/json"}
        mock_response.text = '{"error": "auth failed"}'
        assert ca_api._is_cloudflare_block(mock_response) is False

    def test_200_not_cloudflare(self, ca_api):
        """200 response is never a Cloudflare block."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        assert ca_api._is_cloudflare_block(mock_response) is False


class TestPostWithCloudflareRetry:
    def test_retries_on_cloudflare_403(self, ca_api):
        """Should refresh cookie and retry on Cloudflare 403."""
        ca_api._cloudflare_cookie = "__cf_bm=old_cookie"
        ca_api._cloudflare_cookie_fetched_at = dt.datetime.now(dt.timezone.utc)

        mock_cf_response = MagicMock()
        mock_cf_response.cookies = {"__cf_bm": "new_cookie"}

        call_count = 0

        def mock_post(url, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                resp = MagicMock()
                resp.status_code = 403
                resp.headers = {"Content-Type": "text/html"}
                resp.text = "<html>Cloudflare Attention Required</html>"
                return resp
            else:
                resp = MagicMock()
                resp.status_code = 200
                resp.json.return_value = {
                    "responseHeader": {"responseCode": 0},
                    "result": {},
                }
                return resp

        headers = {"accessToken": "test"}
        with (
            patch("requests.get", return_value=mock_cf_response),
            patch.object(ca_api.sessions, "post", side_effect=mock_post),
        ):
            response = ca_api._post_with_cloudflare_retry(
                "https://test.ca/api", headers=headers, json={"pin": "1234"}
            )

        assert call_count == 2
        assert response.status_code == 200
        assert headers["Cookie"] == "__cf_bm=new_cookie"

    def test_no_retry_on_success(self, ca_api):
        """Should not retry when response is 200."""
        call_count = 0

        def mock_post(url, **kwargs):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {"responseHeader": {"responseCode": 0}}
            return resp

        headers = {"accessToken": "test"}
        with patch.object(ca_api.sessions, "post", side_effect=mock_post):
            ca_api._post_with_cloudflare_retry("https://test.ca/api", headers=headers)

        assert call_count == 1

    def test_no_retry_on_non_cloudflare_403(self, ca_api):
        """Should not retry on 403 that is not Cloudflare."""
        call_count = 0

        def mock_post(url, **kwargs):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            resp.status_code = 403
            resp.headers = {"Content-Type": "application/json"}
            resp.text = '{"error": "unauthorized"}'
            return resp

        headers = {"accessToken": "test"}
        with patch.object(ca_api.sessions, "post", side_effect=mock_post):
            result = ca_api._post_with_cloudflare_retry(
                "https://test.ca/api", headers=headers
            )

        assert call_count == 1
        assert result.status_code == 403
