"""Tests for the CA Cloudflare TLS fingerprint workaround.

With Python 3.14 / OpenSSL 3.5 (Home Assistant 2026.10), Cloudflare in front
of the CA endpoints answered v2/login with a 403 HTML block page because
urllib3's default context disables TLS session tickets. login() then crashed
on response.json() with an opaque JSONDecodeError. RetrySession now
re-enables session tickets, and login() turns a non-JSON body into an
APIError that names the HTTP status.
"""

import ssl
from unittest.mock import MagicMock

import pytest
import requests

from hyundai_kia_connect_api.exceptions import APIError
from hyundai_kia_connect_api.KiaUvoApiCA import (
    KiaUvoApiCA,
    RetrySession,
    SessionTicketAdapter,
)


def test_retry_session_mounts_session_ticket_adapter_for_https():
    session = RetrySession()

    assert isinstance(
        session.get_adapter("https://mybluelink.ca/"), SessionTicketAdapter
    )


def test_session_ticket_adapter_enables_tickets():
    adapter = SessionTicketAdapter()

    context = adapter.poolmanager.connection_pool_kw["ssl_context"]

    assert not context.options & ssl.OP_NO_TICKET


def test_session_ticket_adapter_applies_to_proxies():
    adapter = SessionTicketAdapter()

    proxy_manager = adapter.proxy_manager_for("http://proxy.example:3128")

    context = proxy_manager.connection_pool_kw["ssl_context"]
    assert not context.options & ssl.OP_NO_TICKET


def _html_block_response():
    response = requests.Response()
    response.status_code = 403
    response.headers["server"] = "cloudflare"
    response.headers["cf-ray"] = "a4f22d1bc80ce407-YUL"
    response._content = b"<!DOCTYPE html>\n<html>blocked</html>"
    return response


def test_login_raises_api_error_on_non_json_response():
    api = KiaUvoApiCA(region=2, brand=2, language="en")
    api._sessions = MagicMock()
    api._sessions.post.return_value = _html_block_response()

    with pytest.raises(APIError, match="HTTP 403.*server=cloudflare"):
        api.login("user@example.com", "secret")
