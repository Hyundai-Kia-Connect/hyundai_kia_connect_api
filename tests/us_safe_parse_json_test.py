"""Tests for _safe_parse_json helper in HyundaiBlueLinkApiUSA.

Covers the fix for JSONDecodeError when Hyundai's USA API returns an
empty response body (HTTP 200 OK, no body) for control commands.
"""

import pytest
from hyundai_kia_connect_api.HyundaiBlueLinkApiUSA import _safe_parse_json
from hyundai_kia_connect_api.exceptions import APIError


class _FakeResponse:
    """Minimal fake for requests.Response."""

    def __init__(self, text="", status_code=200):
        self.text = text
        self.status_code = status_code

    def json(self):
        import json

        return json.loads(self.text)


class TestSafeParseJson:
    def test_empty_body_200_returns_none(self):
        """HTTP 200 with empty body should return None (command succeeded)."""
        response = _FakeResponse(text="", status_code=200)
        result = _safe_parse_json(response, "test_action")
        assert result is None

    def test_whitespace_body_200_returns_none(self):
        """HTTP 200 with whitespace-only body should return None."""
        response = _FakeResponse(text="   ", status_code=200)
        result = _safe_parse_json(response, "test_action")
        assert result is None

    def test_valid_json_200_returns_dict(self):
        """HTTP 200 with valid JSON body should return parsed dict."""
        response = _FakeResponse(text='{"errorCode": "200"}', status_code=200)
        result = _safe_parse_json(response, "test_action")
        assert result == {"errorCode": "200"}

    def test_non_200_raises_api_error(self):
        """Non-200 status code should raise APIError."""
        response = _FakeResponse(text="Server Error", status_code=502)
        with pytest.raises(APIError):
            _safe_parse_json(response, "test_action")

    def test_non_200_error_message_contains_status(self):
        """APIError message should include the HTTP status code."""
        response = _FakeResponse(text="Not Found", status_code=404)
        with pytest.raises(APIError, match="404"):
            _safe_parse_json(response, "test_action")

    def test_non_200_error_message_contains_action_name(self):
        """APIError message should include the action name."""
        response = _FakeResponse(text="Error", status_code=500)
        with pytest.raises(APIError, match="lock_action"):
            _safe_parse_json(response, "lock_action")
