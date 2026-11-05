"""Scoped providers reuse the shared push lifecycle without another transport."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

from hyundai_kia_connect_api.mqtt_push_session import MqttPushSession
from hyundai_kia_connect_api.Token import Token
from hyundai_kia_connect_api.Vehicle import Vehicle


class Provider:
    def __init__(self):
        self.is_connected = False
        self.stop_count = 0
        self.status = None
        self.connection = None

    def start_push(self, status, connection):
        self.status, self.connection = status, connection
        self.is_connected = True
        connection(True)
        return True

    def stop_push(self):
        self.is_connected = False
        self.stop_count += 1


def session(provider):
    vehicle = Vehicle(id="synthetic-car")
    return MqttPushSession(
        api=SimpleNamespace(get_push_provider=lambda token, car: provider),
        token_fn=lambda: Token(username="synthetic", password="synthetic"),
        refresh_fn=Mock(),
        get_vehicle=lambda vehicle_id: vehicle,
        vehicles=lambda: {vehicle.id: vehicle},
    )


def test_provider_is_owned_before_synchronous_start_callback_and_uses_latest_observers():
    provider = Provider()
    owner = session(provider)
    connected = []
    owner.set_connection_change_callback(
        lambda state: connected.append((state, owner.is_connected))
    )
    assert owner.ensure_started("synthetic-car")
    assert connected == [(True, True)]
    first, replacement = Mock(), Mock()
    owner.set_status_callback(first)
    provider.status("synthetic-car", "Status")
    first.assert_called_once_with("synthetic-car", "Status")
    owner.set_status_callback(replacement)
    provider.status("synthetic-car", "Status")
    replacement.assert_called_once_with("synthetic-car", "Status")
    owner.stop()
    provider.status("synthetic-car", "Status")
    replacement.assert_called_once()
    assert not owner.is_connected


def test_stop_during_provider_start_cannot_resurrect_session():
    provider = Provider()
    owner = session(provider)
    entered, release = Event(), Event()
    original_start = provider.start_push

    def start(status, connection):
        entered.set()
        assert release.wait(2), "start release not signaled"
        return original_start(status, connection)

    provider.start_push = start
    changed = Mock()
    owner.set_connection_change_callback(changed)
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(owner.ensure_started, "synthetic-car")
        assert entered.wait(2), "provider never started"
        owner.stop()
        release.set()
        assert pending.result(timeout=2) is False
    assert not owner.is_connected
    changed.assert_not_called()


def test_old_generation_callback_cannot_observe_restarted_same_provider():
    provider = Provider()
    owner = session(provider)
    changed = Mock()
    owner.set_status_callback(changed)
    assert owner.ensure_started("synthetic-car")
    previous_status = provider.status
    owner.stop()
    assert owner.ensure_started("synthetic-car")
    previous_status("synthetic-car", "Status")
    changed.assert_not_called()
    provider.status("synthetic-car", "Status")
    changed.assert_called_once_with("synthetic-car", "Status")
    owner.stop()


def test_legacy_eager_connect_callback_subscribes_exactly_once(monkeypatch):
    client = SimpleNamespace(is_connected=False, subscribe=Mock(), disconnect=Mock())

    def factory(**callbacks):
        def connect():
            client.is_connected = True
            callbacks["on_connect"]()

        client.connect = connect
        client.configure = Mock()
        return client

    monkeypatch.setattr(
        "hyundai_kia_connect_api.mqtt_push_session.MqttTransport", factory
    )
    vehicle = Vehicle(id="synthetic-car", mqtt_vehicle_id="synthetic-topic-car")
    api = SimpleNamespace(
        get_push_broker_info=lambda token: {"mqtt_host": "synthetic.example"},
        register_push_client=lambda token: {"client_id": "synthetic-client"},
        get_push_credentials=lambda token: ("synthetic-client", "synthetic-password"),
        get_push_vehicle_identity=lambda token, car: {
            "mqtt_vehicle_id": car.mqtt_vehicle_id
        },
        register_push_vehicle=lambda token, car: {},
        get_push_topics=lambda token, car: ["synthetic/topic"],
    )
    owner = MqttPushSession(
        api=api,
        token_fn=lambda: Token(username="synthetic", password="synthetic"),
        refresh_fn=Mock(),
        get_vehicle=lambda vehicle_id: vehicle,
        vehicles=lambda: {vehicle.id: vehicle},
    )
    assert owner.ensure_started(vehicle.id)
    client.subscribe.assert_called_once_with(["synthetic/topic"])
    owner.stop()
