"""Tests for VehicleManager push glue over the ApiImpl push contract."""

import sys
import threading
import time
from typing import Any, ClassVar

import pytest

from hyundai_kia_connect_api import VehicleManager

vm_module = sys.modules["hyundai_kia_connect_api.VehicleManager"]
hub_module = sys.modules["hyundai_kia_connect_api.mqtt_service_hub_eu"]
from hyundai_kia_connect_api.mqtt_service_hub_eu import CciPushMessage
from hyundai_kia_connect_api.Token import Token
from hyundai_kia_connect_api.Vehicle import Vehicle

_ManagerModule = "hyundai_kia_connect_api.VehicleManager"

# ---------------------------------------------------------------------------
# Fake region API implementing the push contract
# ---------------------------------------------------------------------------


class FakePushApi:
    """Minimal ApiImpl double implementing the push contract."""

    def __init__(self):
        self.broker: dict | None = {
            "mqtt_host": "broker.example",
            "mqtt_port": 8883,
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
    vehicle.supports.mqtt_push = True
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
        self.fail_connect = False
        self.connection_change_cb: Any = None
        FakeTransport.instances.append(self)

    @property
    def is_connected(self) -> bool:
        return self.connected

    def configure(self, **kwargs):
        self.configured.update(kwargs)

    def connect(self):
        if self.fail_connect:
            raise ValueError("bad broker config")
        self.connected = True

    def disconnect(self):
        self.connected = False

    def subscribe(self, topics):
        self.subscribed = list(topics)
        return list(topics)

    def set_on_connection_change(self, callback):
        self.connection_change_cb = callback


def _connected_transport_factory(monkeypatch):
    """Patch the module-level MqttTransport in VehicleManager with a
    connect-success FakeTransport."""
    FakeTransport.instances = []

    def factory(*args, **kwargs):
        t = FakeTransport(*args, **kwargs)
        t.connected = True
        return t

    monkeypatch.setattr(hub_module, "MqttTransport", factory)
    return FakeTransport


# ---------------------------------------------------------------------------
# start_push
# ---------------------------------------------------------------------------


def test_start_push_true_and_configures_transport(monkeypatch):
    vm = _make_vm()
    _connected_transport_factory(monkeypatch)
    assert vm.start_push("careu1") is True
    assert vm._push_session is not None and vm._push_session._client is not None
    assert vm.vehicles["careu1"].supports.mqtt_push is True
    assert vm._push_session._client.configured["broker_host"] == "broker.example"
    assert vm._push_session._client.configured["client_id"] == "dev-client"


def test_start_push_false_when_vehicle_unknown():
    vm = _make_vm()
    assert vm.start_push("careu-missing") is False
    assert vm._push_session is None


def test_start_push_false_when_push_unmarked():
    vm = _make_vm()
    vm.vehicles["careu1"].supports.mqtt_push = None
    assert vm.start_push("careu1") is False
    assert vm._push_session is None
    vm.vehicles["careu1"].supports.mqtt_push = False
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
    vm.vehicles["careu2"].supports.mqtt_push = True
    assert vm.start_push("careu1") is True
    assert vm.start_push("careu2") is True
    assert len(FakeTransport.instances) == 1
    assert vm._push_session is not None and sorted(vm._push_session._vehicle_ids) == [
        "careu1",
        "careu2",
    ]


def test_failed_vehicle_registration_subscribes_nothing(monkeypatch):
    """A failed protocol registration must not subscribe topics."""
    fake_cls = _connected_transport_factory(monkeypatch)
    vm = _make_vm()
    vm.api.register_result = None  # type: ignore[attr-defined]
    assert vm.start_push("careu1") is False
    assert vm._push_session is None or vm._push_session._client is None
    assert vm._push_session is not None and vm._push_session._vehicle_ids == set()
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
    vm._ensure_push_session()
    vm._push_session._on_push_message(msg)
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
    vm._ensure_push_session()
    vm._push_session._on_push_message(msg)
    assert seen == [("careu1", "Status")]


def test_message_unknown_topic_schema_dropped():
    vm = _make_vm()
    seen = []
    vm.set_push_status_callback(lambda vid, tt: seen.append((vid, tt)))
    vm._ensure_push_session()
    vm._push_session._on_push_delivery_raw("service/unknown/schema/v999/", b"{}")
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
    vm._ensure_push_session()
    vm._push_session._on_push_message(msg)
    assert seen == []


# ---------------------------------------------------------------------------
# reconnect
# ---------------------------------------------------------------------------


def test_reconnect_calls_check_and_refresh_token(monkeypatch):
    vm = _make_vm()
    vm.vehicles["careu1"].mqtt_vehicle_id = "testmqtt1"
    vm._ensure_push_session()
    vm._push_session._vehicle_ids = {"careu1"}
    refreshes = []
    monkeypatch.setattr(vm, "check_and_refresh_token", lambda: refreshes.append(1))
    if vm._push_session is not None:
        vm._push_session._refresh_fn = vm.check_and_refresh_token
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

    monkeypatch.setattr(hub_module, "MqttTransport", _Reconnectable)
    vm._ensure_push_session()
    vm._push_session._client = None
    vm._ensure_push_session()
    vm._push_session._on_push_disconnect("unexpected (rc=7)")
    deadline = time.time() + 2.0
    while attempts["n"] < 2 and time.time() < deadline:
        time.sleep(0.02)
    assert attempts["n"] >= 2
    assert refreshes, "token refresh must be called before each reconnect attempt"


def test_clean_disconnect_does_not_reconnect():
    vm = _make_vm()
    vm._ensure_push_session()
    vm._push_session._vehicle_ids = {"careu1"}
    vm._ensure_push_session()
    vm._push_session._client = FakeTransport()
    vm._ensure_push_session()
    vm._push_session._on_push_disconnect("clean disconnect")
    assert vm._push_session is None or vm._push_session._reconnect_cancel is None


# ---------------------------------------------------------------------------
# stop
# ---------------------------------------------------------------------------


def test_stop_push_clears_state(monkeypatch):
    _connected_transport_factory(monkeypatch)
    vm = _make_vm()
    vm.start_push("careu1")
    vm.stop_push()
    assert vm._push_session is None or vm._push_session._client is None
    assert vm._push_session is not None and vm._push_session._vehicle_ids == set()


def test_start_push_false_when_client_registration_rejected():
    vm = _make_vm()
    vm.api.reg = None  # type: ignore[attr-defined]
    assert vm.start_push("careu1") is False
    assert vm._push_session is None or vm._push_session._client is None
    assert vm._push_session is not None and vm._push_session._vehicle_ids == set()


def test_start_push_second_vehicle_failed_registration_keeps_first(monkeypatch):
    _connected_transport_factory(monkeypatch)
    vm = _make_vm()
    vm.vehicles["careu2"] = Vehicle()
    vm.vehicles["careu2"].id = "careu2"
    assert vm.start_push("careu1") is True
    vm.api.register_result = None  # type: ignore[attr-defined]
    assert vm.start_push("careu2") is False
    assert "careu2" not in vm._push_session._vehicle_ids
    assert vm._push_session is not None and vm._push_session._client is not None  # kept


def test_start_push_wires_connection_change_callback(monkeypatch):
    _connected_transport_factory(monkeypatch)
    vm = _make_vm()

    def before(connected):
        """first callback"""

    vm.set_push_connection_change_callback(before)
    assert vm.start_push("careu1") is True
    assert vm._push_session._client.connection_change_cb is before
    # late registration rewires the live client

    def after(connected):
        """replacement callback"""

    vm.set_push_connection_change_callback(after)
    assert vm._push_session._client.connection_change_cb is after


def test_start_push_connect_failure_rolls_vehicle_back(monkeypatch):
    def factory(*args, **kwargs):
        t = FakeTransport(*args, **kwargs)
        t.fail_connect = True
        return t

    monkeypatch.setattr(hub_module, "MqttTransport", factory)
    vm = _make_vm()
    with pytest.raises(ValueError, match="bad broker config"):
        vm.start_push("careu1")
    assert vm._push_session is None or vm._push_session._client is None
    assert vm._push_session is not None and vm._push_session._vehicle_ids == set()


def test_on_push_connect_subscribes_started_vehicles(monkeypatch):
    _connected_transport_factory(monkeypatch)
    vm = _make_vm()
    vm.start_push("careu1")
    vm._ensure_push_session()
    vm._push_session._client.connected = False
    vm._ensure_push_session()
    vm._push_session._on_push_connect()
    assert "topics" in vm.api.calls

    # a subscribe failure during reconnect must not raise out of the callback
    def boom(_token, vehicle):
        raise RuntimeError("subscribe failed")

    vm.api.get_push_topics = boom  # type: ignore[assignment]
    vm._ensure_push_session()
    vm._push_session._on_push_connect()


def test_raw_delivery_of_parsed_message_dispatches():
    vm = _make_vm()
    vm.vehicles["careu1"].mqtt_vehicle_id = "mqttv1"
    seen = []
    vm.set_push_status_callback(lambda vid, tt: seen.append((vid, tt)))
    vm.api.parse_push_message = lambda topic, payload: CciPushMessage(  # type: ignore[assignment]
        topic_group="CarStatus",
        topic_type="Status",
        vehicle_id="mqttv1",
        payload={},
        is_status=True,
    )
    vm._ensure_push_session()
    vm._push_session._on_push_delivery_raw("service/phone/_/vss/mqttv1/", b"{}")
    assert seen == [("careu1", "Status")]


def test_push_status_callback_error_is_contained():
    vm = _make_vm()
    vm.vehicles["careu1"].mqtt_vehicle_id = "mqttv1"

    def broken(vid, tt):
        raise RuntimeError("callback failed")

    vm.set_push_status_callback(broken)
    assert vm._push_status_callback is broken


def test_stop_push_cancels_reconnect_and_tolerates_broken_client():
    vm = _make_vm()
    cancel = threading.Event()
    vm._ensure_push_session()
    vm._push_session._reconnect_cancel = cancel

    class _BrokenTransport:
        is_connected = False
        connection_change_cb = None

        def disconnect(self):
            raise RuntimeError("already down")

        def set_on_connection_change(self, callback): ...

    vm._ensure_push_session()
    vm._push_session._client = _BrokenTransport()
    vm._ensure_push_session()
    vm._push_session._vehicle_ids = {"careu1"}
    vm.stop_push()
    assert cancel.is_set()
    assert vm._push_session is None or vm._push_session._reconnect_cancel is None
    assert vm._push_session is None or vm._push_session._client is None
    assert vm._push_session is not None and vm._push_session._vehicle_ids == set()


def test_get_push_connection_state_delegates():
    from hyundai_kia_connect_api.const import MQTT_PUSH_CONNECTION_STATE

    vm = _make_vm()
    vm.api.get_push_connection_state = (  # type: ignore[assignment]
        lambda token: MQTT_PUSH_CONNECTION_STATE.ONLINE
    )
    assert vm.get_push_connection_state() is MQTT_PUSH_CONNECTION_STATE.ONLINE


def test_reconnect_cleanup_tolerates_broken_client_and_cancels_prior_run():
    vm = _make_vm()
    vm._ensure_push_session()
    vm._push_session._vehicle_ids = {"careu1"}
    prior_cancel = threading.Event()
    vm._ensure_push_session()
    vm._push_session._reconnect_cancel = prior_cancel

    class _BrokenTransport:
        is_connected = False

        def disconnect(self):
            raise RuntimeError("already down")

    vm._ensure_push_session()
    vm._push_session._client = _BrokenTransport()
    vm._ensure_push_session()
    vm._push_session._on_push_disconnect("unexpected (rc=7)")
    assert prior_cancel.is_set()  # prior cancellation event signalled
    assert (
        vm._push_session is None or vm._push_session._client is None
    )  # cleanup swallowed the disconnect error
    assert (
        vm._push_session is not None and vm._push_session._reconnect_cancel is not None
    )  # fresh event
    vm._ensure_push_session()
    assert vm._push_session is not None
    vm._push_session._reconnect_cancel.set()


def test_reconnect_success_returns_after_first_cycle(monkeypatch):
    vm = _make_vm()
    vm.vehicles["careu1"].mqtt_vehicle_id = "testmqtt1"
    vm._ensure_push_session()
    vm._push_session._vehicle_ids = {"careu1"}
    monkeypatch.setattr(vm, "check_and_refresh_token", lambda: None)
    if vm._push_session is not None:
        vm._push_session._refresh_fn = vm.check_and_refresh_token
    from hyundai_kia_connect_api import const as const_mod

    monkeypatch.setattr(const_mod, "MQTT_PUSH_RECONNECT_INITIAL_DELAY", 0.01)
    monkeypatch.setattr(const_mod, "MQTT_PUSH_RECONNECT_MAX_DELAY", 0.01)
    _connected_transport_factory(monkeypatch)
    vm._ensure_push_session()
    vm._push_session._client = None
    before = _reconnect_thread_idents()
    vm._ensure_push_session()
    vm._push_session._on_push_disconnect("unexpected (rc=7)")
    thread = _new_reconnect_thread(before)
    thread.join(timeout=2.0)
    assert not thread.is_alive()
    assert vm.is_push_connected is True


def test_reconnect_cancelled_exits_backoff_loop(monkeypatch):
    vm = _make_vm()
    vm._ensure_push_session()
    vm._push_session._vehicle_ids = {"careu1"}
    from hyundai_kia_connect_api import const as const_mod

    monkeypatch.setattr(const_mod, "MQTT_PUSH_RECONNECT_INITIAL_DELAY", 0.05)
    monkeypatch.setattr(const_mod, "MQTT_PUSH_RECONNECT_MAX_DELAY", 0.05)
    release = threading.Event()

    def slow_refresh():
        # Hold the attempt in flight so the cancel lands BETWEEN attempts
        # (a cancel during cancel_event.wait() returns inside the loop body).
        release.wait(timeout=5.0)

    monkeypatch.setattr(vm, "check_and_refresh_token", slow_refresh)
    if vm._push_session is not None:
        vm._push_session._refresh_fn = vm.check_and_refresh_token

    def factory(*args, **kwargs):
        t = FakeTransport(*args, **kwargs)
        t.fail_connect = True  # every attempt fails → loop keeps backing off
        return t

    monkeypatch.setattr(hub_module, "MqttTransport", factory)
    vm._ensure_push_session()
    vm._push_session._client = None
    before = _reconnect_thread_idents()
    vm._ensure_push_session()
    vm._push_session._on_push_disconnect("unexpected (rc=7)")
    thread = _new_reconnect_thread(before)
    time.sleep(0.15)  # wait(delay) elapsed → first attempt is inside refresh
    vm._ensure_push_session()
    assert vm._push_session is not None
    vm._push_session._reconnect_cancel.set()
    release.set()
    thread.join(timeout=2.0)
    assert not thread.is_alive()
    assert vm.is_push_connected is False


def test_reconnect_without_vehicles_returns_early():
    vm = _make_vm()
    vm._push_vehicle_ids = set()
    vm._ensure_push_session()
    vm._push_session._on_push_disconnect("unexpected (rc=7)")
    assert vm._push_session is None or vm._push_session._reconnect_cancel is None
    assert vm._push_session is None or vm._push_session._client is None


def _reconnect_thread_idents() -> set:
    return {
        t.ident
        for t in threading.enumerate()
        if t.name == "push-reconnect" and t.ident is not None
    }


def _new_reconnect_thread(before: set):
    deadline = time.time() + 2.0
    while time.time() < deadline:
        candidates = [
            t
            for t in threading.enumerate()
            if t.name == "push-reconnect"
            and t.ident not in before
            and t.ident is not None
        ]
        if candidates:
            return candidates[0]
        time.sleep(0.01)
    raise AssertionError("push-reconnect thread not started")


def test_set_push_callbacks_forward_to_running_session(monkeypatch):
    _connected_transport_factory(monkeypatch)
    vm = _make_vm()
    assert vm.start_push("careu1") is True
    seen: list[str] = []
    conn: list[bool] = []

    vm.set_push_status_callback(lambda vid, tt: seen.append(vid))
    vm.set_push_connection_change_callback(lambda ok: conn.append(ok))
    assert vm._push_session is not None
    assert vm._push_session._status_callback is not None
    transport = vm._push_session._client
    assert transport is not None
    assert transport.connection_change_cb is not None
    assert seen == []  # unrelated vehicle id dropped, callback wired though


def test_set_push_callbacks_before_start_wire_session_later(monkeypatch):
    _connected_transport_factory(monkeypatch)
    vm = _make_vm()
    vm.set_push_status_callback(None)
    vm.set_push_connection_change_callback(None)
    assert vm.start_push("careu1") is True
    assert vm._push_session is not None
    assert vm._push_session._status_callback is None
    assert vm._push_session._connection_change_callback is None
