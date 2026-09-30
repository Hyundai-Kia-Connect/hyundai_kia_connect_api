"""Tests for VehicleManager push glue over the ApiImpl push contract."""

import threading
import time
from typing import Any, ClassVar

from hyundai_kia_connect_api import VehicleManager
from hyundai_kia_connect_api.mqtt_service_hub import CciPushMessage
from hyundai_kia_connect_api.Token import Token
from hyundai_kia_connect_api.Vehicle import Vehicle

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
        self.reg = {"client_id": "dev-client", "username": None, "password": None}
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
        return {}

    def get_push_vehicle_identity(self, _token, vehicle):
        self.calls.append("identity")
        vehicle.mqtt_vehicle_id = "testmqtt1"
        return {"mqtt_vehicle_id": "testmqtt1", "hu_client_id": "testhu1"}

    def get_push_connection_state(self, _token):
        return "ONLINE"

    def get_push_topics(self, _token, vehicle):
        self.calls.append("topics")
        return list(self.topics)

    def parse_push_message(self, topic, payload):  # pragma: no cover — replaced in tests
        return None


def _make_vm() -> VehicleManager:
    """VehicleManager with a push-capable fake API and one vehicle."""
    vm = VehicleManager(region=9, brand=2, language="en", username="u", password="p", pin="0000")
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
        self.connection_change = None
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


def _fake_transport(monkeypatch, vm, connect_ok: bool = True):
    t = FakeTransport
    t.instances = []
    if connect_ok:
        monkeypatch.setattr(vm, "_push_transport_factory", _make_connected_transport)
    return t


def _make_connected_transport(*args, **kwargs):
    t = FakeTransport(*args, **kwargs)
    t.connected = True
    return t


# ---------------------------------------------------------------------------
# start_push
# ---------------------------------------------------------------------------


def test_start_push_true_and_configures_transport(monkeypatch):
    vm = _make_vm()
    monkeypatch.setattr(vm, "_push_transport_factory", _make_connected_transport)
    assert vm.start_push("careu1") is True
    assert vm._push_client is not None
    assert vm.is_push_supported is True
    assert vm._push_client.configured["broker_host"] == "broker.example"
    assert vm._push_client.configured["client_id"] == "dev-client"


def test_start_push_false_when_region_unsupported():
    vm = _make_vm()
    vm.api.supports_mqtt_push = False  # type: ignore[attr-defined]
    assert vm.start_push("careu1") is False


def test_start_push_true_when_region_contract_noop():
    vm = _make_vm()
    vm.api.broker = None  # type: ignore[attr-defined]
    assert vm.start_push("careu1") is False


def test_start_push_second_vehicle_reuses_client(monkeypatch):
    FakeTransport.instances = []
    vm = _make_vm()
    monkeypatch.setattr(vm, "_push_transport_factory", _make_connected_transport)
    vm.vehicles["careu2"] = Vehicle()
    vm.vehicles["careu2"].id = "careu2"
    assert vm.start_push("careu1") is True
    assert vm.start_push("careu2") is True
    assert len(FakeTransport.instances) == 1
    assert sorted(vm._push_vehicle_ids) == ["careu1", "careu2"]


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
# action tracking
# ---------------------------------------------------------------------------


def test_wait_for_push_result_signaled_by_action_message():
    vm = _make_vm()
    result_holder: dict = {}

    def waiter():
        result_holder["r"] = vm.wait_for_push_result("tid-1", timeout=1.0)

    thread = threading.Thread(target=waiter, daemon=True)
    thread.start()
    time.sleep(0.05)

    msg = CciPushMessage(
        topic_group="DeviceRemote",
        topic_type="Res",
        vehicle_id="mqttv1",
        payload={"header": {"tid": "tid-1"}, "body": {"resCode": "0"}},
        is_status=False,
        action_id="tid-1",
        action_result="0",
        action_connected=False,
    )
    vm._on_push_message(msg)
    thread.join(1.0)
    assert result_holder["r"] == "0"


def test_wait_for_push_result_timeout():
    vm = _make_vm()
    assert vm.wait_for_push_result("no-such-tid", timeout=0.05) is None


# ---------------------------------------------------------------------------
# reconnect
# ---------------------------------------------------------------------------


def test_reconnect_calls_check_and_refresh_token(monkeypatch):
    vm = _make_vm()
    vm.vehicles["careu1"].mqtt_vehicle_id = "testmqtt1"
    vm._push_vehicle_ids = ["careu1"]
    refreshes = []
    monkeypatch.setattr(vm, "check_and_refresh_token", lambda: refreshes.append(1))
    monkeypatch.setattr(vm, "_push_reconnect_initial_delay", 0.01)
    monkeypatch.setattr(vm, "_push_reconnect_max_delay", 0.01)

    attempts = {"n": 0}

    class _Reconnectable:
        def __init__(self, on_message=None, on_connect=None, on_disconnect=None): ...

        @property
        def is_connected(self):
            return attempts["n"] >= 2

        def configure(self, **kwargs): ...

        def connect(self):
            attempts["n"] += 1

    monkeypatch.setattr(vm, "_push_transport_factory", _Reconnectable)
    vm._push_client = None
    vm._on_push_disconnect("unexpected (rc=7)")
    deadline = time.time() + 2.0
    while attempts["n"] < 2 and time.time() < deadline:
        time.sleep(0.02)
    assert attempts["n"] >= 2
    assert refreshes, "token refresh must be called before each reconnect attempt"


def test_clean_disconnect_does_not_reconnect(monkeypatch):
    vm = _make_vm()
    monkeypatch.setattr(vm, "_push_reconnect_initial_delay", 60)
    monkeypatch.setattr(vm, "_push_reconnect_max_delay", 60)
    vm._push_vehicle_ids = ["careu1"]
    vm._push_client = FakeTransport()
    vm._on_push_disconnect("clean disconnect")
    assert vm._push_reconnect_cancel is None


# ---------------------------------------------------------------------------
# stop
# ---------------------------------------------------------------------------


def test_stop_push_clears_state(monkeypatch):
    vm = _make_vm()
    monkeypatch.setattr(vm, "_push_transport_factory", _make_connected_transport)
    vm.start_push("careu1")
    vm._push_action_events["x"] = threading.Event()
    vm.stop_push()
    assert vm._push_client is None
    assert vm._push_action_events == {}
    assert vm._push_vehicle_ids == []
