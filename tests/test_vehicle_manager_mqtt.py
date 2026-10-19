"""Tests for VehicleManager push glue over the ApiImpl push contract."""

import sys
import time
from typing import Any, ClassVar

from hyundai_kia_connect_api import VehicleManager

vm_module = sys.modules["hyundai_kia_connect_api.VehicleManager"]
from hyundai_kia_connect_api.mqtt_service_hub_eu import CciPushMessage
from hyundai_kia_connect_api.Token import Token
from hyundai_kia_connect_api.Vehicle import Vehicle

_ManagerModule = "hyundai_kia_connect_api.VehicleManager"

# ---------------------------------------------------------------------------
# Fake region API implementing the push contract
# ---------------------------------------------------------------------------


class FakePushApi:
    """Minimal ApiImpl double implementing the push contract."""

    supports_mqtt_push = True

    def __init__(self):
        self.broker: dict | None = {
            "mqtt_host": "broker.example",
            "mqtt_port": 8883,
            "use_ssl": True,
        }
        self.reg: dict | None = {
            "client_id": "dev-client",
            "username": None,
            "password": None,
        }
        self.register_result: dict | None = {}
        self.topics = [
            "service/phone/_/vss/testmqtt1/",
            "device/dev-client/remotecontroller/connect",
        ]
        self.calls: list[str] = []

    def get_push_broker_info(self, _token):
        self.calls.append("broker")
        return self.broker

    def register_push_client(self, _token):
        self.calls.append("register_client")
        return self.reg

    def register_push_vehicle(self, _token, vehicle):
        self.calls.append("register_vehicle")
        return self.register_result

    def get_push_vehicle_identity(self, _token, vehicle):
        self.calls.append("identity")
        vehicle.mqtt_vehicle_id = "testmqtt1"
        return {"mqtt_vehicle_id": "testmqtt1", "hu_client_id": "testhu1"}

    def get_push_connection_state(self, _token):
        return None

    def get_push_topics(self, _token, vehicle):
        self.calls.append("topics")
        return list(self.topics)

    def parse_push_message(
        self, topic, payload
    ):  # pragma: no cover — replaced in tests
        return None


def _make_vm() -> VehicleManager:
    """VehicleManager with a push-capable fake API and one vehicle."""
    vm = VehicleManager(
        region=9, brand=2, language="en", username="u", password="p", pin="0000"
    )
    vm.api = FakePushApi()  # type: ignore[assignment]
    vm.token = Token()
    vehicle = Vehicle()
    vehicle.id = "careu1"
    vm.vehicles = {"careu1": vehicle}
    return vm


class FakeTransport:
    """Transport double capturing configure/subscribe, connect state injectable."""

    instances: ClassVar[list] = []

    def __init__(self, on_message=None, on_connect=None, on_disconnect=None):
        self.on_message = on_message
        self.on_connect = on_connect
        self.on_disconnect = on_disconnect
        self.configured: dict[str, Any] = {}
        self.connected = False
        self.subscribed: list[str] = []
        FakeTransport.instances.append(self)

    @property
    def is_connected(self) -> bool:
        return self.connected

    def configure(self, **kwargs):
        self.configured.update(kwargs)

    def connect(self):
        self.connected = True

    def disconnect(self):
        self.connected = False

    def subscribe(self, topics):
        self.subscribed = list(topics)
        return list(topics)


def _connected_transport_factory(monkeypatch):
    """Patch the module-level MqttTransport in VehicleManager with a
    connect-success FakeTransport."""
    FakeTransport.instances = []

    def factory(*args, **kwargs):
        t = FakeTransport(*args, **kwargs)
        t.connected = True
        return t

    monkeypatch.setattr(vm_module, "MqttTransport", factory)
    return FakeTransport


# ---------------------------------------------------------------------------
# start_push
# ---------------------------------------------------------------------------


def test_start_push_true_and_configures_transport(monkeypatch):
    vm = _make_vm()
    _connected_transport_factory(monkeypatch)
    assert vm.start_push("careu1") is True
    assert vm._push_client is not None
    assert vm.api.supports_mqtt_push is True
    assert vm._push_client.configured["broker_host"] == "broker.example"
    assert vm._push_client.configured["client_id"] == "dev-client"


def test_start_push_false_when_region_unsupported():
    vm = _make_vm()
    vm.api.supports_mqtt_push = False  # type: ignore[attr-defined]
    assert vm.start_push("careu1") is False


def test_start_push_false_when_broker_info_missing():
    vm = _make_vm()
    vm.api.broker = None  # type: ignore[attr-defined]
    assert vm.start_push("careu1") is False


def test_start_push_second_vehicle_reuses_client(monkeypatch):
    _connected_transport_factory(monkeypatch)
    vm = _make_vm()
    vm.vehicles["careu2"] = Vehicle()
    vm.vehicles["careu2"].id = "careu2"
    assert vm.start_push("careu1") is True
    assert vm.start_push("careu2") is True
    assert len(FakeTransport.instances) == 1
    assert sorted(vm._push_vehicle_ids) == ["careu1", "careu2"]


def test_failed_vehicle_registration_subscribes_nothing(monkeypatch):
    """A failed protocol registration must not subscribe topics."""
    fake_cls = _connected_transport_factory(monkeypatch)
    vm = _make_vm()
    vm.api.register_result = None  # type: ignore[attr-defined]
    assert vm.start_push("careu1") is False
    assert vm._push_client is None
    assert vm._push_vehicle_ids == []
    assert "topics" not in vm.api.calls
    assert fake_cls.instances == []


def test_failed_identity_subscribes_nothing(monkeypatch):
    """Identity without a vehicle topic id blocks registration."""
    fake_cls = _connected_transport_factory(monkeypatch)
    vm = _make_vm()

    def identity_no_vehicle_id(_token, vehicle):
        vm.api.calls.append("identity")
        # no mqtt_vehicle_id set

    vm.api.get_push_vehicle_identity = identity_no_vehicle_id  # type: ignore[assignment]
    assert vm.start_push("careu1") is False
    assert "topics" not in vm.api.calls
    assert fake_cls.instances == []


# ---------------------------------------------------------------------------
# dispatch
# ---------------------------------------------------------------------------


def test_message_unknown_vehicle_dropped():
    vm = _make_vm()
    seen = []
    vm.set_push_status_callback(lambda vid, tt: seen.append((vid, tt)))
    vm.vehicles["careu1"].mqtt_vehicle_id = "mqttv1"
    msg = CciPushMessage(
        topic_group="CarStatus",
        topic_type="Status",
        vehicle_id="unknown-mqtt-id",
        payload={},
        is_status=True,
    )
    vm._on_push_message(msg)
    assert seen == []


def test_message_status_resolves_vehicle_id():
    vm = _make_vm()
    vm.vehicles["careu1"].mqtt_vehicle_id = "mqttv1"
    seen = []
    vm.set_push_status_callback(lambda vid, tt: seen.append((vid, tt)))
    msg = CciPushMessage(
        topic_group="CarStatus",
        topic_type="Status",
        vehicle_id="mqttv1",
        payload={},
        is_status=True,
    )
    vm._on_push_message(msg)
    assert seen == [("careu1", "Status")]


def test_message_unknown_topic_schema_dropped():
    vm = _make_vm()
    seen = []
    vm.set_push_status_callback(lambda vid, tt: seen.append((vid, tt)))
    vm._on_push_delivery_raw("service/unknown/schema/v999/", b"{}")
    assert seen == []


# ---------------------------------------------------------------------------
# non-status messages are ignored by the dispatcher (no action consumer)
# ---------------------------------------------------------------------------


def test_res_message_is_ignored_without_callback():
    vm = _make_vm()
    seen = []
    vm.set_push_status_callback(lambda vid, tt: seen.append((vid, tt)))
    msg = CciPushMessage(
        topic_group="DeviceRemote",
        topic_type="Res",
        vehicle_id="mqttv1",
        payload={"header": {"tid": "tid-1"}, "body": {"resCode": "0"}},
        is_status=False,
    )
    vm._on_push_message(msg)
    assert seen == []


# ---------------------------------------------------------------------------
# reconnect
# ---------------------------------------------------------------------------


def test_reconnect_calls_check_and_refresh_token(monkeypatch):
    vm = _make_vm()
    vm.vehicles["careu1"].mqtt_vehicle_id = "testmqtt1"
    vm._push_vehicle_ids = ["careu1"]
    refreshes = []
    monkeypatch.setattr(vm, "check_and_refresh_token", lambda: refreshes.append(1))
    from hyundai_kia_connect_api import const as const_mod

    monkeypatch.setattr(const_mod, "MQTT_PUSH_RECONNECT_INITIAL_DELAY", 0.01)
    monkeypatch.setattr(const_mod, "MQTT_PUSH_RECONNECT_MAX_DELAY", 0.01)

    attempts = {"n": 0}

    class _Reconnectable:
        def __init__(self, on_message=None, on_connect=None, on_disconnect=None): ...

        @property
        def is_connected(self):
            return attempts["n"] >= 2

        def configure(self, **kwargs): ...

        def connect(self):
            attempts["n"] += 1

    monkeypatch.setattr(vm_module, "MqttTransport", _Reconnectable)
    vm._push_client = None
    vm._on_push_disconnect("unexpected (rc=7)")
    deadline = time.time() + 2.0
    while attempts["n"] < 2 and time.time() < deadline:
        time.sleep(0.02)
    assert attempts["n"] >= 2
    assert refreshes, "token refresh must be called before each reconnect attempt"


def test_clean_disconnect_does_not_reconnect():
    vm = _make_vm()
    vm._push_vehicle_ids = ["careu1"]
    vm._push_client = FakeTransport()
    vm._on_push_disconnect("clean disconnect")
    assert vm._push_reconnect_cancel is None


# ---------------------------------------------------------------------------
# stop
# ---------------------------------------------------------------------------


def test_stop_push_clears_state(monkeypatch):
    _connected_transport_factory(monkeypatch)
    vm = _make_vm()
    vm.start_push("careu1")
    vm.stop_push()
    assert vm._push_client is None
    assert vm._push_vehicle_ids == []
