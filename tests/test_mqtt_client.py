"""Tests for the generic MQTT transport (mqtt_client module)."""

import pytest

from hyundai_kia_connect_api.mqtt_client import MqttTransport

pytest.importorskip("paho.mqtt.client")


def test_connect_constructs_paho_client_with_callback_api_v2(monkeypatch):
    """paho v2-only: the paho Client is constructed with CallbackAPIVersion.VERSION2."""
    import hyundai_kia_connect_api.mqtt_client as mqtt_client_mod

    captured = {}

    class _FakePahoClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def username_pw_set(self, *args, **kwargs): ...
        def tls_set(self, tls_version=None): ...
        def tls_insecure_set(self, *args, **kwargs): ...
        def reconnect_delay_set(self, *args, **kwargs): ...
        def connect_async(self, *args, **kwargs): ...
        def loop_start(self): ...

    monkeypatch.setattr(mqtt_client_mod.mqtt, "Client", _FakePahoClient)
    t = MqttTransport()
    t.configure("broker.example", 8883)
    t.connect()
    assert (
        captured.get("callback_api_version")
        == mqtt_client_mod.mqtt.CallbackAPIVersion.VERSION2
    )


def test_module_has_no_v1_fallback_flags():
    """The v1 compat shells must be gone: no _PAHO_AVAILABLE / _PAHO_V2 flags."""
    import hyundai_kia_connect_api.mqtt_client as mqtt_client_mod

    assert not hasattr(mqtt_client_mod, "_PAHO_AVAILABLE")
    assert not hasattr(mqtt_client_mod, "_PAHO_V2")


def test_subscribe_batch_qos0():
    """subscribe(list[str]) — single batch SUBSCRIBE, QoS 0, no schema knowledge."""
    calls: list[list[tuple[str, int]]] = []

    class _FakePaho:
        def subscribe(self, topics):
            calls.append(list(topics))
            return (0, 7)

        def connect_async(self, *a, **k): ...
        def loop_start(self): ...
        def username_pw_set(self, *a, **k): ...
        def tls_set(self, tls_version=None): ...
        def tls_insecure_set(self, *a, **k): ...
        def reconnect_delay_set(self, *a, **k): ...

    t = MqttTransport()
    t._client = _FakePaho()  # type: ignore[assignment]
    t._connected = True
    t.subscribe(["service/phone/_/vss/v1/", "device/c1/remotecontroller/connect"])
    assert calls == [
        [
            ("service/phone/_/vss/v1/", 0),
            ("device/c1/remotecontroller/connect", 0),
        ]
    ]


def test_on_message_receives_raw_topic_payload():
    got: list[tuple[str, bytes]] = []
    t = MqttTransport(on_message=lambda topic, payload: got.append((topic, payload)))

    class _Msg:
        topic = "service/phone/_/vss/v1/"
        payload = b"j"

    t._on_mqtt_message(None, None, _Msg())
    assert got == [("service/phone/_/vss/v1/", b"j")]


def test_configure_accepts_broker_credential_names():
    """The manager→transport call shape (post-review rename) works on the
    REAL transport, not just test doubles."""
    import hyundai_kia_connect_api.mqtt_client as mod

    t = mod.MqttTransport()
    t.configure(
        broker_host="broker.example",
        broker_port=8883,
        client_id="client-1",
        broker_username="u",
        broker_password="p",
    )
    assert t._broker_username == "u"


def test_connack_refusal_notifies_instead_of_raising():
    """Refusal on the paho thread must not raise: paho re-raises callback
    exceptions on its network loop, killing the thread before any glue
    sees it (live-probed on paho 2.1.0). Surface via on_disconnect +
    connection-change instead."""

    class _Reason:
        value = 135

    refused: list[str] = []
    changes: list[bool] = []
    t = MqttTransport(
        on_disconnect=lambda reason: refused.append(reason),
        on_connect=lambda: refused.append("connected"),  # must NOT fire
    )
    t.set_on_connection_change(lambda up: changes.append(up))
    t._on_mqtt_connect(None, None, None, _Reason())
    assert refused == ["broker refused (rc=135)"]
    assert changes == [False]
    assert t.is_connected is False


def test_connect_fail_routes_to_disconnect_callback():
    """A failed socket connect (DNS/TLS) goes through the same glue path."""
    refused: list[str] = []
    changes: list[bool] = []
    t = MqttTransport(on_disconnect=lambda reason: refused.append(reason))
    t.set_on_connection_change(lambda up: changes.append(up))
    t._on_mqtt_connect_fail(None, None)
    assert refused == ["connect failed (unreachable)"]
    assert changes == [False]


def test_tls_uses_client_protocol_constant(monkeypatch):
    """tls_set must pass tls_version explicitly: paho 2.1.0's default builds
    ssl.PROTOCOL_TLSv1_2, which Python 3.14 removed — a bare tls_set()
    crashes on 3.14+ at the first real connect (test doubles hide it)."""
    import ssl as ssl_mod

    import hyundai_kia_connect_api.mqtt_client as mod

    captured = {}

    class _FakePaho:
        def __init__(self, **kwargs): ...
        def tls_set(self, tls_version=None):
            captured["tls_version"] = tls_version

        def tls_insecure_set(self, *a, **k): ...
        def username_pw_set(self, *a, **k): ...
        def reconnect_delay_set(self, *a, **k): ...
        def connect_async(self, *a, **k): ...
        def loop_start(self): ...

    monkeypatch.setattr(mod.mqtt, "Client", _FakePaho)
    t = mod.MqttTransport()
    t.configure("broker.example", 8883)
    t.connect()
    assert (
        captured["tls_version"]
        == ssl_mod.PROTOCOL_TLS_CLIENT
        == ssl_mod.PROTOCOL_TLS_CLIENT
    )
