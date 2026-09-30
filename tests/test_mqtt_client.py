"""Tests for the generic MQTT transport (mqtt_client module)."""


from hyundai_kia_connect_api.mqtt_client import HyundaiMqttClient


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
    t = HyundaiMqttClient()
    t.configure("broker.example", 8883)
    t.connect()
    assert captured.get("callback_api_version") == mqtt_client_mod.mqtt.CallbackAPIVersion.VERSION2


def test_module_has_no_v1_fallback_flags():
    """The v1 compat shells must be gone: no _PAHO_AVAILABLE / _PAHO_V2 flags."""
    import hyundai_kia_connect_api.mqtt_client as mqtt_client_mod

    assert not hasattr(mqtt_client_mod, "_PAHO_AVAILABLE")
    assert not hasattr(mqtt_client_mod, "_PAHO_V2")
