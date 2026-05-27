"""Tests for ApiImplSession — timeout and connection pooling."""

from unittest.mock import MagicMock, patch

import pytest
import requests

from hyundai_kia_connect_api.ApiImpl import ApiImplSession
from hyundai_kia_connect_api.exceptions import RequestTimeoutError


class TestApiImplSessionTimeout:
    """Session applies default timeout to all requests."""

    def test_default_timeout_applied(self):
        session = ApiImplSession()
        with patch.object(
            requests.Session, "request", return_value=MagicMock()
        ) as mock:
            session.get("https://example.com")
            mock.assert_called_once()
            call_kwargs = mock.call_args[1]
            assert call_kwargs["timeout"] == (10, 30)

    def test_custom_timeout_not_overridden(self):
        session = ApiImplSession()
        with patch.object(
            requests.Session, "request", return_value=MagicMock()
        ) as mock:
            session.get("https://example.com", timeout=(5, 15))
            mock.assert_called_once()
            call_kwargs = mock.call_args[1]
            assert call_kwargs["timeout"] == (5, 15)

    def test_region_override_timeout(self):
        session = ApiImplSession()
        session.HTTP_READ_TIMEOUT = 45
        with patch.object(
            requests.Session, "request", return_value=MagicMock()
        ) as mock:
            session.get("https://example.com")
            mock.assert_called_once()
            call_kwargs = mock.call_args[1]
            assert call_kwargs["timeout"] == (10, 45)

    def test_post_timeout(self):
        session = ApiImplSession()
        with patch.object(
            requests.Session, "request", return_value=MagicMock()
        ) as mock:
            session.post("https://example.com", json={"key": "value"})
            mock.assert_called_once()
            call_kwargs = mock.call_args[1]
            assert call_kwargs["timeout"] == (10, 30)


class TestApiImplSessionTimeoutException:
    """Timeout raises RequestTimeoutError."""

    def test_timeout_raises_request_timeout_error(self):
        session = ApiImplSession()
        with patch.object(
            requests.Session,
            "request",
            side_effect=requests.exceptions.Timeout("connection timed out"),
        ):
            with pytest.raises(RequestTimeoutError, match="connection timed out"):
                session.get("https://example.com")

    def test_connect_timeout_raises_request_timeout_error(self):
        session = ApiImplSession()
        with patch.object(
            requests.Session,
            "request",
            side_effect=requests.exceptions.ConnectTimeout("connect timed out"),
        ):
            with pytest.raises(RequestTimeoutError, match="connect timed out"):
                session.get("https://example.com")

    def test_read_timeout_raises_request_timeout_error(self):
        session = ApiImplSession()
        with patch.object(
            requests.Session,
            "request",
            side_effect=requests.exceptions.ReadTimeout("read timed out"),
        ):
            with pytest.raises(RequestTimeoutError, match="read timed out"):
                session.get("https://example.com")

    def test_other_exceptions_passthrough(self):
        session = ApiImplSession()
        with patch.object(
            requests.Session,
            "request",
            side_effect=requests.exceptions.ConnectionError("refused"),
        ):
            with pytest.raises(requests.exceptions.ConnectionError, match="refused"):
                session.get("https://example.com")
