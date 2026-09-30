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
        def tls_set(self): ...
        def tls_insecure_set(self, *args, **kwargs): ...
        def reconnect_delay_set(self, *args, **kwargs): ...
        def connect_async(self, *args, **kwargs): ...
        def loop_start(self): ...

    monkeypatch.setattr(mqtt_client_mod.mqtt, "Client", _FakePahoClient)
    t = MqttTransport()
    t.configure("broker.example", 8883)
    t.connect()
    assert captured.get("callback_api_version") == mqtt_client_mod.mqtt.CallbackAPIVersion.VERSION2


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
        def tls_set(self): ...
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
