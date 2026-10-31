"""Tests for the CA Cloudflare TLS fingerprint workaround.

With Python 3.14 / OpenSSL 3.5 (Home Assistant 2026.10), Cloudflare in front
of the CA endpoints answered v2/login with a 403 HTML block page because
urllib3's default context disables TLS session tickets. login() then crashed
on response.json() with an opaque JSONDecodeError. RetrySession now
re-enables session tickets via a fresh per-connection context, and login()
turns a non-JSON body into an APIError that names the HTTP status.

The fresh-per-connection shape also matters on its own: environments that
inject pyOpenSSL >= 26.2 into urllib3 reject a shared context being mutated
after its first connection (kia_uvo#2028, urllib3#5107).
"""

import ssl
from unittest.mock import MagicMock, patch

import certifi
import pytest
import requests
from urllib3.connection import HTTPSConnection

from hyundai_kia_connect_api.exceptions import APIError
from hyundai_kia_connect_api.KiaUvoApiCA import (
    KiaUvoApiCA,
    RetrySession,
    SessionTicketAdapter,
    _SessionTicketConnection,
    _SessionTicketPoolManager,
    _SessionTicketProxyManager,
    create_session_ticket_context,
)


def test_retry_session_mounts_session_ticket_adapter_for_https():
    session = RetrySession()

    assert isinstance(
        session.get_adapter("https://mybluelink.ca/"), SessionTicketAdapter
    )


def test_session_ticket_adapter_enables_tickets():
    context_a = create_session_ticket_context()
    context_b = create_session_ticket_context()

    assert not context_a.options & ssl.OP_NO_TICKET
    # pyOpenSSL >= 26.2 rejects a used context being mutated, so sharing one
    # across connections must never happen again.
    assert context_a is not context_b


def test_session_ticket_adapter_mounts_pool_without_a_shared_context():
    adapter = SessionTicketAdapter()

    assert isinstance(adapter.poolmanager, _SessionTicketPoolManager)
    pool_class = adapter.poolmanager.pool_classes_by_scheme["https"]
    assert pool_class.ConnectionCls is _SessionTicketConnection
    pool_kwargs = adapter.poolmanager.connection_pool_kw
    assert pool_kwargs["ca_certs"] == certifi.where()
    assert "ssl_context" not in pool_kwargs


def test_session_ticket_adapter_applies_to_proxies():
    adapter = SessionTicketAdapter()

    proxy_manager = adapter.proxy_manager_for("http://proxy.example:3128")

    assert isinstance(proxy_manager, _SessionTicketProxyManager)
    pool_class = proxy_manager.pool_classes_by_scheme["https"]
    assert pool_class.ConnectionCls is _SessionTicketConnection
    assert adapter.proxy_manager_for("http://proxy.example:3128") is proxy_manager


def test_every_connection_gets_a_fresh_context_with_tickets():
    with patch.object(HTTPSConnection, "connect") as super_connect:
        first = _SessionTicketConnection(host="mybluelink.ca")
        second = _SessionTicketConnection(host="mybluelink.ca")
        first.connect()
        second.connect()

    assert not first.ssl_context.options & ssl.OP_NO_TICKET
    assert not second.ssl_context.options & ssl.OP_NO_TICKET
    assert first.ssl_context is not second.ssl_context
    assert super_connect.call_count == 2


def test_socks_proxies_are_left_to_requests_own_manager(monkeypatch):
    adapter = SessionTicketAdapter()
    base_calls = []

    def fake_base(self, proxy, **kwargs):
        base_calls.append((proxy, kwargs))
        return "base-manager"

    monkeypatch.setattr("requests.adapters.HTTPAdapter.proxy_manager_for", fake_base)
    url = "socks5h://127.0.0.1:1080"

    assert adapter.proxy_manager_for(url) == "base-manager"
    assert base_calls == [(url, {})]


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
