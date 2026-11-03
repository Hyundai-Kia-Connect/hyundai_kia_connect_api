"""Tests for the Hyundai USA legacy-cipher TLS workaround.

Hyundai USA's endpoints require the legacy ciphers in CIPHERS, so cipherAdapter
mounts a custom connection class instead of a pool-shared context: environments
that inject pyOpenSSL >= 26.2 into urllib3 reject a shared context being
mutated after its first connection (kia_uvo#2028 shape, urllib3#5107).
"""

from unittest.mock import patch

from urllib3.connection import HTTPSConnection

from hyundai_kia_connect_api.HyundaiBlueLinkApiUSA import (
    _CipherConnection,
    _CipherPoolManager,
    _CipherProxyManager,
    cipherAdapter,
    create_cipher_context,
)


def test_cipher_context_keeps_legacy_options():
    context_a = create_cipher_context()
    context_b = create_cipher_context()

    assert context_a.options & 0x4
    # pyOpenSSL >= 26.2 rejects a used context being mutated, so sharing one
    # across connections must never happen again.
    assert context_a is not context_b


def test_cipher_adapter_mounts_pool_without_a_shared_context():
    adapter = cipherAdapter()

    assert isinstance(adapter.poolmanager, _CipherPoolManager)
    pool_class = adapter.poolmanager.pool_classes_by_scheme["https"]
    assert pool_class.ConnectionCls is _CipherConnection
    pool_kwargs = adapter.poolmanager.connection_pool_kw
    assert pool_kwargs["ca_certs"]
    assert "ssl_context" not in pool_kwargs


def test_cipher_adapter_applies_to_proxies():
    adapter = cipherAdapter()

    proxy_manager = adapter.proxy_manager_for("http://proxy.example:3128")

    assert isinstance(proxy_manager, _CipherProxyManager)
    pool_class = proxy_manager.pool_classes_by_scheme["https"]
    assert pool_class.ConnectionCls is _CipherConnection
    assert adapter.proxy_manager_for("http://proxy.example:3128") is proxy_manager


def test_every_connection_gets_a_fresh_context():
    with patch.object(HTTPSConnection, "connect") as super_connect:
        first = _CipherConnection(host="www.hyundaiusa.com")
        second = _CipherConnection(host="www.hyundaiusa.com")
        first.connect()
        second.connect()

    assert first.ssl_context.options & 0x4
    assert first.ssl_context is not second.ssl_context
    assert super_connect.call_count == 2


def test_socks_proxies_are_left_to_requests_own_manager(monkeypatch):
    adapter = cipherAdapter()
    base_calls = []

    def fake_base(self, proxy, **kwargs):
        base_calls.append((proxy, kwargs))
        return "base-manager"

    monkeypatch.setattr("requests.adapters.HTTPAdapter.proxy_manager_for", fake_base)
    url = "socks5h://127.0.0.1:1080"

    assert adapter.proxy_manager_for(url) == "base-manager"
    assert base_calls == [(url, {})]


def test_cipher_adapter_is_mounted_on_the_api_session():
    # KiaUvoApiUSA-style regions mount adapters inside __init__; a broken
    # mount means the region class can't be constructed at all.
    from hyundai_kia_connect_api.HyundaiBlueLinkApiUSA import HyundaiBlueLinkApiUSA

    api = HyundaiBlueLinkApiUSA(region=3, brand=2, language="en")

    adapter = api.session.get_adapter("https://api.telematics.hyundaiusa.com/")
    assert isinstance(adapter, cipherAdapter)
