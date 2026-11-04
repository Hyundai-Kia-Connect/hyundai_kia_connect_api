"""Terminal shutdown cannot be undone by blocked push registration or reconnect."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event, current_thread
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests

from hyundai_kia_connect_api import const
from hyundai_kia_connect_api.mqtt_service_hub_eu import MqttPushSession
from hyundai_kia_connect_api.Token import Token
from hyundai_kia_connect_api.Vehicle import Vehicle
from tests.test_mqtt_provider_lifecycle import Provider, session


@pytest.fixture
def generic(monkeypatch):
    vehicle = Vehicle(id="synthetic", mqtt_vehicle_id="synthetic-topic")
    api = SimpleNamespace(
        get_push_broker_info=Mock(return_value={"mqtt_host": "synthetic.example"}),
        register_push_client=Mock(return_value={"client_id": "synthetic-client"}),
        get_push_credentials=Mock(return_value=None),
        get_push_vehicle_identity=Mock(return_value={}),
        register_push_vehicle=Mock(return_value={}),
        get_push_topics=Mock(return_value=["synthetic/topic"]),
        close=Mock(),
    )
    transports = []

    def factory(**callbacks):
        client = SimpleNamespace(
            is_connected=False,
            configure=Mock(),
            set_on_connection_change=Mock(),
            subscribe=Mock(),
        )

        def connect():
            client.is_connected = True
            callbacks["on_connect"]()

        def disconnect():
            client.is_connected = False

        client.connect = Mock(side_effect=connect)
        client.disconnect = Mock(side_effect=disconnect)
        transports.append(client)
        return client

    monkeypatch.setattr(
        "hyundai_kia_connect_api.mqtt_service_hub_eu.MqttTransport", factory
    )
    owner = MqttPushSession(
        api=api,
        token_fn=lambda: Token(username="synthetic", password="synthetic"),
        refresh_fn=Mock(),
        get_vehicle=lambda _: vehicle,
        vehicles=lambda: {vehicle.id: vehicle},
        after_close_fn=api.close,
    )
    return owner, api, transports


def http_calls(api):
    return sum(
        getattr(api, name).call_count
        for name in (
            "get_push_broker_info",
            "register_push_client",
            "get_push_credentials",
            "get_push_vehicle_identity",
            "register_push_vehicle",
            "get_push_topics",
        )
    )


@pytest.mark.parametrize(
    "stage",
    [
        "get_push_broker_info",
        "register_push_client",
        "get_push_vehicle_identity",
        "register_push_vehicle",
    ],
)
def test_close_during_registration_cannot_start_later_stages(generic, stage):
    owner, api, transports = generic
    entered, release = Event(), Event()
    operation = getattr(api, stage)
    response = operation.return_value

    def block(*_args):
        entered.set()
        assert release.wait(2)
        return response

    operation.side_effect = block
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(owner.ensure_started, "synthetic")
        assert entered.wait(2)
        calls = http_calls(api)
        owner.close()
        release.set()
        assert pending.result(timeout=2) is False
    assert http_calls(api) == calls
    assert transports == []
    assert not owner.is_connected


def test_close_during_reconnect_token_refresh_never_registers_again(
    generic, monkeypatch
):
    owner, api, transports = generic
    assert owner.ensure_started("synthetic")
    monkeypatch.setattr(const, "MQTT_PUSH_RECONNECT_INITIAL_DELAY", 0)
    entered, release = Event(), Event()
    workers = []

    def refresh():
        workers.append(current_thread())
        entered.set()
        assert release.wait(2)

    owner._refresh_fn = refresh
    owner._on_push_disconnect("synthetic disconnect")
    assert entered.wait(2)
    calls = http_calls(api)
    from tests.test_manager_cleanup import manager

    fleet = manager()
    fleet.api = api
    fleet._push_session = owner
    fleet.close()
    release.set()
    workers[0].join(2)

    assert not workers[0].is_alive()
    assert api.close.call_count == 2
    assert http_calls(api) == calls
    assert len(transports) == 1
    assert not transports[0].is_connected
    assert not owner.is_connected


def test_close_during_transport_connect_disconnects_late_connection(
    generic, monkeypatch
):
    from hyundai_kia_connect_api import mqtt_service_hub_eu as hub

    owner, _, transports = generic
    entered, release = Event(), Event()
    factory = hub.MqttTransport

    def blocking_factory(**callbacks):
        client = factory(**callbacks)

        def connect():
            entered.set()
            assert release.wait(2)
            client.is_connected = True

        client.connect.side_effect = connect
        return client

    monkeypatch.setattr(hub, "MqttTransport", blocking_factory)
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(owner.ensure_started, "synthetic")
        assert entered.wait(2)
        owner.close()
        release.set()
        assert pending.result(timeout=2) is False

    assert not owner.is_connected
    assert not transports[0].is_connected
    assert transports[0].disconnect.call_count == 2


def test_stop_allows_restart_but_close_is_terminal(generic):
    owner, api, transports = generic
    assert owner.ensure_started("synthetic")
    owner.stop()
    assert owner.ensure_started("synthetic")
    assert len(transports) == 2
    owner.close()
    calls = http_calls(api)
    assert owner.ensure_started("synthetic") is False
    assert http_calls(api) == calls
    assert not owner.is_connected


def test_terminal_close_preserves_owned_provider_stop_during_start():
    provider = Provider()
    owner = session(provider)
    entered, release = Event(), Event()
    original = provider.start_push

    def block(*args):
        entered.set()
        assert release.wait(2)
        return original(*args)

    provider.start_push = block
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(owner.ensure_started, "synthetic-car")
        assert entered.wait(2)
        owner.close()
        release.set()
        assert pending.result(timeout=2) is False
    assert owner.ensure_started("synthetic-car") is False
    assert not provider.is_connected


def test_close_attempts_all_providers_even_if_one_fails(generic):
    owner, _, transports = generic
    assert owner.ensure_started("synthetic")
    first = SimpleNamespace(stop_push=Mock())
    second = SimpleNamespace(stop_push=Mock(side_effect=RuntimeError("stop")))
    owner._providers = {"first": first, "second": second}

    with pytest.raises(RuntimeError, match="stop"):
        owner.close()

    first.stop_push.assert_called_once()
    second.stop_push.assert_called_once()
    assert not transports[0].is_connected
    assert owner.ensure_started("synthetic") is False


def test_reconnect_finishing_after_close_releases_reopened_http_pool(
    generic, monkeypatch
):
    from tests.test_manager_cleanup import manager

    owner, api, _ = generic
    assert owner.ensure_started("synthetic")
    monkeypatch.setattr(const, "MQTT_PUSH_RECONNECT_INITIAL_DELAY", 0)
    entered, release = Event(), Event()
    workers = []
    reopened = Mock(spec=requests.Session)
    api.session = Mock(spec=requests.Session)
    api.close.side_effect = lambda: api.session.close()

    def refresh():
        workers.append(current_thread())
        entered.set()
        assert release.wait(2)
        api.session = reopened

    owner._refresh_fn = refresh
    owner._on_push_disconnect("synthetic disconnect")
    assert entered.wait(2)
    fleet = manager()
    fleet.api, fleet._push_session = api, owner
    fleet.close()
    reopened.close.assert_not_called()
    release.set()
    workers[0].join(2)

    assert not workers[0].is_alive()
    reopened.close.assert_called_once()
    assert not owner.is_connected
