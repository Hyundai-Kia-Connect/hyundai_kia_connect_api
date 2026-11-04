"""Replay the shared Service Hub lifecycle and Japan controls without sockets."""

import importlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock, RLock
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from hyundai_kia_connect_api import VehicleManager
from hyundai_kia_connect_api.const import ORDER_STATUS
from hyundai_kia_connect_api.japan_mqtt import JapanMqttSession, JapanMqttTransportPool
from hyundai_kia_connect_api.mqtt_service_hub import ServiceHubProviderMixin
from hyundai_kia_connect_api.mqtt_service_hub_eu import MqttServiceHubMixin
from hyundai_kia_connect_api.Token import Token
from hyundai_kia_connect_api.Vehicle import Vehicle


class BrokerClient:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.username_pw_set = Mock()
        self.tls_set_context = Mock()
        self.reconnect_delay_set = Mock()
        self.max_inflight_messages_set = Mock()
        self.connect_async = Mock()
        self.disconnect = Mock()
        self.loop_stop = Mock()
        self.publish = Mock()
        self.requests = []
        self.subscribed = Event()

    def subscribe(self, topics):
        mid = len(self.requests) + 1
        self.requests.append((mid, topics))
        self.subscribed.set()
        return 0, mid

    def acknowledge(self, mid):
        topics = self.requests[mid - 1][1]
        self.on_subscribe(self, None, mid, [qos for _topic, qos in topics], None)

    def loop_start(self):
        self.on_connect(self, None, {}, 0, None)
        for mid, _topics in list(self.requests):
            self.acknowledge(mid)
        return 0

    def deliver(self, topic, payload, retained=False):
        self.on_message(
            self,
            None,
            SimpleNamespace(
                topic=topic, payload=json.dumps(payload).encode(), retain=retained
            ),
        )


def response(data):
    result = Mock(status_code=200, headers={})
    result.json.return_value = data
    return result


def bootstrap(helper, vehicle_id):
    helper._http_session = Mock()
    helper._http_session.request.side_effect = [
        response(
            {
                "mqtt": {
                    "host": "broker.ap-northeast.hmgmobility.com",
                    "port": 8883,
                    "ssl": True,
                },
                "tid": "synthetic-tid",
            }
        ),
        response({"clientId": "synthetic-broker-client"}),
        response({"vehicles": [{"unit": "ccu", "vehicleId": vehicle_id}]}),
        response({}),
    ]


@pytest.fixture
def broker(monkeypatch):
    clients = []

    def factory(**kwargs):
        client = BrokerClient(**kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr("hyundai_kia_connect_api.mqtt_client.mqtt.Client", factory)
    yield clients
    for client in clients:
        client.publish.assert_not_called()


def manager():
    result = VehicleManager(
        region=10, brand=2, username="synthetic", password="synthetic", pin="synthetic"
    )
    result.token = Token(
        username="synthetic",
        password="synthetic",
        pin="synthetic-pin",
        access_token="Bearer synthetic-access",
        device_id="synthetic-device",
        mqtt_registration_uuid="synthetic-phone-uuid",
        control_token="Bearer synthetic-control",
        control_token_expiry=time.time() + 3600,
    )
    result.vehicles = {
        "synthetic-car": Vehicle(id="synthetic-car", ccu_ccs2_protocol_support=1)
    }
    result.api._request = Mock(return_value={"msgId": "synthetic-action"})
    helper = result.api.get_push_provider(
        result.token, result.vehicles["synthetic-car"]
    )
    bootstrap(helper, "synthetic-topic-car")
    return result, helper


@pytest.mark.parametrize("push_first", [True, False])
def test_manager_push_and_commands_share_registration_and_one_connection(
    broker, push_first
):
    fleet, helper = manager()
    status = Mock()
    fleet.set_push_status_callback(status)
    try:
        if push_first:
            assert fleet.start_push("synthetic-car")
        action = fleet.lock("synthetic-car")
        if not push_first:
            assert fleet.start_push("synthetic-car")
        assert len(broker) == 1
        assert helper._http_session.request.call_count == 4
        assert (
            fleet.api.get_push_provider(fleet.token, fleet.vehicles["synthetic-car"])
            is helper
        )
        assert fleet.token.mqtt_registration_uuid == "synthetic-phone-uuid"
        assert fleet.token.mqtt_client_id == "synthetic-broker-client"
        assert fleet.vehicles["synthetic-car"].mqtt_vehicle_id == "synthetic-topic-car"
        assert (
            fleet.check_action_status("synthetic-car", action) == ORDER_STATUS.ACCEPTED
        )
        assert fleet.is_push_connected

        broker[0].deliver(
            helper._topics[0],
            {
                "state": {
                    "Vehicle": {"Date": "20261104010000.000", "DrivingReady": False}
                }
            },
        )
        status.assert_called_once_with("synthetic-car", "Status")
        assert helper.drain_state_updates() == [
            {"Date": "20261104010000.000", "DrivingReady": False}
        ]
        fleet.stop_push()
        assert not fleet.is_push_connected
        assert helper.is_connected  # API ownership continues serving commands.
        broker[0].disconnect.assert_not_called()
    finally:
        fleet.stop_push()
        fleet.api.close()
    broker[0].disconnect.assert_called_once()


def test_push_only_stop_releases_its_connection_before_api_shutdown(broker):
    fleet, helper = manager()
    assert fleet.start_push("synthetic-car")
    assert helper.is_connected
    fleet.stop_push()
    assert not fleet.is_push_connected
    assert helper.closed
    assert not fleet.api._mqtt_transport_pool._connections
    broker[0].disconnect.assert_called_once()
    fleet.api.close()
    broker[0].disconnect.assert_called_once()


def test_push_only_stop_during_bootstrap_does_not_create_a_late_connection(broker):
    fleet, helper = manager()
    entered, release = Event(), Event()
    replies = iter(helper._http_session.request.side_effect)

    def request(*args, **kwargs):
        entered.set()
        assert release.wait(2), "bootstrap release not signaled"
        return next(replies)

    helper._http_session.request.side_effect = request
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(fleet.start_push, "synthetic-car")
            assert entered.wait(2), "bootstrap never started"
            fleet.stop_push()
            release.set()
            assert pending.result(timeout=2) is False
        assert helper.closed
        assert broker == []
        assert not fleet.is_push_connected
        assert not fleet.api._mqtt_transport_pool._connections
    finally:
        release.set()
        fleet.stop_push()
        fleet.api.close()


def test_stopping_push_preserves_a_command_consumer_already_bootstrapping(broker):
    fleet, helper = manager()
    entered, release, push_attached = Event(), Event(), Event()
    replies = iter(helper._http_session.request.side_effect)

    def request(*args, **kwargs):
        entered.set()
        assert release.wait(2), "command bootstrap release not signaled"
        return next(replies)

    original_consumer = helper._prepare_consumer

    def consumer(kind):
        if kind == "push":
            push_attached.set()
        return original_consumer(kind)

    helper._prepare_consumer = consumer
    helper._http_session.request.side_effect = request
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            command = executor.submit(helper.prepare)
            assert entered.wait(2), "command consumer never started"
            push = executor.submit(fleet.start_push, "synthetic-car")
            assert push_attached.wait(2), "push observer never attached"
            fleet.stop_push()
            assert not helper.closed
            release.set()
            command.result(timeout=2)
            assert push.result(timeout=2) is False
        assert helper.is_connected
        assert not fleet.is_push_connected
        broker[0].disconnect.assert_not_called()
    finally:
        release.set()
        fleet.stop_push()
        fleet.api.close()
    broker[0].disconnect.assert_called_once()


def test_same_broker_client_is_shared_across_vehicles_with_correlated_subacks(broker):
    token = Token(
        username="synthetic",
        password="synthetic",
        access_token="Bearer synthetic-access",
    )
    pool = JapanMqttTransportPool()
    first = JapanMqttSession(
        token, "synthetic-a", "synthetic-phone", dict, transport_pool=pool
    )
    second = JapanMqttSession(
        token, "synthetic-b", "synthetic-phone", dict, transport_pool=pool
    )
    bootstrap(first, "topic-a")
    bootstrap(second, "topic-b")
    try:
        first.prepare()
        client = broker[0]
        client.subscribed.clear()
        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(second.prepare)
            assert client.subscribed.wait(2), "second subscription was never sent"
            assert not second.is_connected
            assert first.is_connected
            client.acknowledge(1)  # An old ACK cannot confirm the second vehicle.
            assert not second.is_connected
            client.acknowledge(2)
            pending.result(timeout=2)
        assert len(broker) == 1
        assert first._transport is second._transport
        first.track("action-a")
        second.track("action-b")
        client.deliver(first._topics[0], {"state": {"Vehicle": {"value": "a"}}})
        client.deliver(second._topics[0], {"state": {"Vehicle": {"value": "b"}}})
        assert first.drain_state_updates() == [{"value": "a"}]
        assert second.drain_state_updates() == [{"value": "b"}]
        client.deliver(first._topics[1], {"sid": "action-a", "res-msg": "S"})
        assert first.status("action-a") == ORDER_STATUS.SUCCESS
        assert second.status("action-b") == ORDER_STATUS.PENDING
        first.close()
        client.disconnect.assert_not_called()
        assert second.is_connected
        client.deliver(second._topics[0], {"state": {"Vehicle": {"value": "still-b"}}})
        assert second.drain_state_updates() == [{"value": "still-b"}]
    finally:
        first.close()
        second.close()
    broker[0].disconnect.assert_called_once()


def test_regions_inherit_the_same_registration_implementation():
    for name in (
        "get_push_broker_info",
        "register_push_client",
        "register_push_vehicle",
        "get_push_vehicle_identity",
        "get_push_credentials",
    ):
        assert getattr(MqttServiceHubMixin, name) is getattr(
            ServiceHubProviderMixin, name
        )
        assert getattr(JapanMqttSession, name) is getattr(ServiceHubProviderMixin, name)


def test_missing_metadata_cannot_reuse_a_previous_registration_identity(broker):
    token = Token(
        username="synthetic",
        password="synthetic",
        access_token="Bearer synthetic-access",
    )
    vehicle = Vehicle(id="synthetic-car", mqtt_vehicle_id="previous-topic-id")
    helper = JapanMqttSession(
        token, vehicle.id, "synthetic-phone", dict, vehicle=vehicle
    )
    bootstrap(helper, "fresh-topic-id")
    replies = list(helper._http_session.request.side_effect)
    missing = response({})
    missing.status_code = 404
    helper._http_session.request.side_effect = [
        replies[0],
        replies[1],
        missing,
        response({}),
        replies[2],
        replies[3],
    ]
    try:
        helper.prepare()
        assert helper._http_session.request.call_count == 6
        assert vehicle.mqtt_vehicle_id == "fresh-topic-id"
        assert helper.request_context["vehicleId"] == "fresh-topic-id"
    finally:
        helper.close()


def test_concurrent_push_and_command_factory_lookup_cannot_orphan_a_helper(
    broker, monkeypatch
):
    api_module = importlib.import_module("hyundai_kia_connect_api.KiaUvoApiJP")
    fleet = VehicleManager(
        region=10, brand=2, username="synthetic", password="synthetic", pin="synthetic"
    )
    fleet.token = Token(
        username="synthetic",
        password="synthetic",
        access_token="Bearer synthetic-access",
        mqtt_registration_uuid="synthetic-phone",
    )
    vehicle = Vehicle(id="synthetic-car", ccu_ccs2_protocol_support=1)
    fleet.vehicles = {vehicle.id: vehicle}
    entered, release, second_lookup = Event(), Event(), Event()
    underlying, counter_lock = RLock(), Lock()

    class ObservedLock:
        entries = 0

        def __enter__(self):
            with counter_lock:
                self.entries += 1
                if self.entries == 2:
                    second_lookup.set()
            underlying.acquire()
            return self

        def __exit__(self, *args):
            underlying.release()

    fleet.api._mqtt_sessions_lock = ObservedLock()
    original = api_module.JapanMqttSession
    created = []

    def constructor(*args, **kwargs):
        entered.set()
        assert release.wait(2), "factory release not signaled"
        helper = original(*args, **kwargs)
        created.append(helper)
        return helper

    monkeypatch.setattr(api_module, "JapanMqttSession", constructor)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            push = executor.submit(fleet.api.get_push_provider, fleet.token, vehicle)
            assert entered.wait(2), "first helper construction never started"
            command = executor.submit(fleet.api._mqtt_for_vehicle, fleet.token, vehicle)
            assert second_lookup.wait(2), "second lookup did not contend for ownership"
            release.set()
            helper = push.result(timeout=2)
            assert command.result(timeout=2) is helper
        assert len(created) == 1
        assert len(fleet.api._mqtt_sessions) == 1
        bootstrap(helper, "synthetic-topic-car")
        helper.prepare()
        assert len(broker) == 1
        assert len(fleet.api._mqtt_transport_pool._connections) == 1
    finally:
        release.set()
        fleet.api.close()
    assert helper.closed
    assert not fleet.api._mqtt_transport_pool._connections
    broker[0].disconnect.assert_called_once()
