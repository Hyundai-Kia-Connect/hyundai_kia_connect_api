"""Tests for the Kia USA TLS 1.2 legacy-cipher workaround.

KiaSSLAdapter pins TLS to exactly 1.2 via ssl_minimum_version /
ssl_maximum_version, without the deprecated ssl.PROTOCOL_TLSv1_2 constant,
and each connection builds its own context (urllib3#5107).
"""

import ssl
from unittest.mock import patch

from urllib3.connection import HTTPSConnection

from hyundai_kia_connect_api.KiaUvoApiUSA import (
    KiaSSLAdapter,
    KiaUvoApiUSA,
    _KiaTls12Connection,
    _KiaTls12PoolManager,
    _KiaTls12ProxyManager,
    create_kia_tls12_context,
)


def test_context_is_pinned_to_tls12_and_keeps_legacy_options():
    context_a = create_kia_tls12_context()
    context_b = create_kia_tls12_context()

    assert context_a.minimum_version == ssl.TLSVersion.TLSv1_2
    assert context_a.maximum_version == ssl.TLSVersion.TLSv1_2
    assert context_a.options & 0x4
    # pyOpenSSL >= 26.2 rejects a used context being mutated, so sharing one
    # across connections must never happen again.
    assert context_a is not context_b


def test_adapter_mounts_pool_without_a_shared_context():
    # Constructing the adapter at all is the Python 3.14 regression test:
    # the previous ssl.PROTOCOL_TLSv1_2 pin raised AttributeError here.
    adapter = KiaSSLAdapter()

    assert isinstance(adapter.poolmanager, _KiaTls12PoolManager)
    pool_class = adapter.poolmanager.pool_classes_by_scheme["https"]
    assert pool_class.ConnectionCls is _KiaTls12Connection
    pool_kwargs = adapter.poolmanager.connection_pool_kw
    assert pool_kwargs["ca_certs"]
    assert "ssl_context" not in pool_kwargs


def test_adapter_applies_to_proxies():
    adapter = KiaSSLAdapter()

    proxy_manager = adapter.proxy_manager_for("http://proxy.example:3128")

    assert isinstance(proxy_manager, _KiaTls12ProxyManager)
    pool_class = proxy_manager.pool_classes_by_scheme["https"]
    assert pool_class.ConnectionCls is _KiaTls12Connection
    assert adapter.proxy_manager_for("http://proxy.example:3128") is proxy_manager


def test_every_connection_gets_a_fresh_context():
    with patch.object(HTTPSConnection, "connect") as super_connect:
        first = _KiaTls12Connection(host="api.owners.kia.com")
        second = _KiaTls12Connection(host="api.owners.kia.com")
        first.connect()
        second.connect()

    assert first.ssl_context.minimum_version == ssl.TLSVersion.TLSv1_2
    assert first.ssl_context is not second.ssl_context
    assert super_connect.call_count == 2


def test_socks_proxies_are_left_to_requests_own_manager(monkeypatch):
    adapter = KiaSSLAdapter()
    base_calls = []

    def fake_base(self, proxy, **kwargs):
        base_calls.append((proxy, kwargs))
        return "base-manager"

    monkeypatch.setattr("requests.adapters.HTTPAdapter.proxy_manager_for", fake_base)
    url = "socks5h://127.0.0.1:1080"

    assert adapter.proxy_manager_for(url) == "base-manager"
    assert base_calls == [(url, {})]


def test_adapter_is_mounted_on_the_api_session():
    api = KiaUvoApiUSA(region=3, brand=1, language="en")

    adapter = api.session.get_adapter("https://api.owners.kia.com/")
    assert isinstance(adapter, KiaSSLAdapter)
