"""The shared receive-only transport has no provider or vehicle dependencies."""

import ssl
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from paho.mqtt.enums import CallbackAPIVersion

from hyundai_kia_connect_api.mqtt_client import MqttTransport


@pytest.fixture
def client(monkeypatch):
    instance = Mock()
    instance.loop_start.return_value = 0
    instance.subscribe.return_value = (0, 7)
    factory = Mock(return_value=instance)
    monkeypatch.setattr("hyundai_kia_connect_api.mqtt_client.mqtt.Client", factory)
    yield instance, factory
    instance.publish.assert_not_called()


def connect(client, **callbacks):
    instance, _factory = client
    transport = MqttTransport(**callbacks)
    transport.configure("synthetic.example", client_id="synthetic-client")
    transport.connect()
    instance.on_connect(instance, None, {}, 0, None)
    return transport


def suback(client, grants, mid=7):
    instance, _factory = client
    instance.on_subscribe(
        instance, None, mid, [SimpleNamespace(value=value) for value in grants], None
    )


def test_default_transport_keeps_tls_reconnect_and_immediate_qos_zero_behavior(client):
    instance, factory = client
    transport = connect(client)
    factory.assert_called_once_with(
        callback_api_version=CallbackAPIVersion.VERSION2,
        client_id="synthetic-client",
        protocol=4,
        clean_session=True,
        reconnect_on_failure=True,
    )
    instance.tls_set.assert_called_once_with(tls_version=ssl.PROTOCOL_TLS_CLIENT)
    instance.tls_insecure_set.assert_called_once_with(False)
    instance.tls_set_context.assert_not_called()
    instance.reconnect_delay_set.assert_called_once_with(min_delay=1, max_delay=30)
    instance.max_inflight_messages_set.assert_called_once_with(100)
    assert instance.connect_timeout == 10
    instance.connect_async.assert_called_once_with(
        "synthetic.example", 8883, keepalive=15
    )

    assert transport.subscribe(["synthetic/a", "synthetic/b"]) == [
        "synthetic/a",
        "synthetic/b",
    ]
    instance.subscribe.assert_called_once_with([("synthetic/a", 0), ("synthetic/b", 0)])
    snapshot = transport.subscribed_topics
    snapshot.clear()
    assert transport.subscribed_topics == ["synthetic/a", "synthetic/b"]
    assert transport.is_connected


def test_connect_requires_configuration_without_starting_a_client(client):
    _instance, factory = client
    with pytest.raises(ValueError, match="not configured"):
        MqttTransport().connect()
    factory.assert_not_called()


def test_disconnected_and_empty_subscriptions_are_not_sent(client):
    instance, _factory = client
    transport = MqttTransport()
    assert transport.subscribe(["synthetic/a"]) == []
    transport = connect(client)
    assert transport.subscribe([]) == []
    instance.subscribe.assert_not_called()


@pytest.mark.parametrize("qos", [0, 1, 2])
def test_opt_in_confirmation_requires_matching_mid_count_and_requested_qos(client, qos):
    success, failure = Mock(), Mock()
    transport = connect(
        client, on_subscribe_success=success, on_subscribe_failure=failure
    )
    transport.subscribe(["synthetic/a", "synthetic/b"], qos=qos)
    success.assert_not_called()
    failure.assert_not_called()
    suback(client, [qos, qos], mid=99)
    success.assert_not_called()
    suback(client, [qos, qos])
    success.assert_called_once_with()
    failure.assert_not_called()
    suback(client, [qos, qos])
    success.assert_called_once()


@pytest.mark.parametrize("grants", [[], [1], [1, 128], [1, 1, 1], [1, 2]])
def test_opt_in_confirmation_reports_missing_invalid_or_rejected_grants(client, grants):
    success, failure = Mock(), Mock()
    transport = connect(
        client, on_subscribe_success=success, on_subscribe_failure=failure
    )
    transport.subscribe(["synthetic/a", "synthetic/b"], qos=1)
    suback(client, grants)
    success.assert_not_called()
    failure.assert_called_once_with("subscriptions not granted requested QoS")


def test_opt_in_confirmation_accepts_legal_qos_downgrade(client):
    success, failure = Mock(), Mock()
    transport = connect(
        client, on_subscribe_success=success, on_subscribe_failure=failure
    )
    transport.subscribe(["synthetic/a", "synthetic/b"], qos=1)
    suback(client, [1, 0])
    success.assert_called_once_with()
    failure.assert_not_called()


def test_subscription_callbacks_are_correlated_to_each_request_mid(client):
    first, second, failure = Mock(), Mock(), Mock()
    transport = connect(client)
    client[0].subscribe.side_effect = [(0, 7), (0, 8)]
    transport.subscribe(["synthetic/a"], qos=1, on_success=first, on_failure=failure)
    transport.subscribe(["synthetic/b"], qos=1, on_success=second, on_failure=failure)
    suback(client, [1], mid=8)
    second.assert_called_once_with()
    first.assert_not_called()
    suback(client, [0], mid=7)
    first.assert_called_once_with()
    failure.assert_not_called()


@pytest.mark.parametrize("qos", [True, -1, 3, "1"])
def test_invalid_qos_never_subscribes(client, qos):
    transport = connect(client)
    with pytest.raises(ValueError, match="QoS"):
        transport.subscribe(["synthetic/a"], qos=qos)
    client[0].subscribe.assert_not_called()


def test_immediate_subscription_rejection_notifies_opt_in_provider(client):
    success, failure = Mock(), Mock()
    transport = connect(
        client, on_subscribe_success=success, on_subscribe_failure=failure
    )
    client[0].subscribe.return_value = (4, None)
    transport.subscribe(["synthetic/a"], qos=1)
    failure.assert_called_once_with("subscription rejected")
    success.assert_not_called()


def test_subscription_exception_is_reported_without_exception_text_when_opted_in(
    client,
):
    failure = Mock()
    transport = connect(client, on_subscribe_failure=failure)
    client[0].subscribe.side_effect = ValueError("synthetic-private-details")
    assert transport.subscribe(["synthetic/a"], qos=1) == []
    failure.assert_called_once_with("subscription failed")


def test_default_subscription_exception_behavior_is_unchanged(client):
    transport = connect(client)
    client[0].subscribe.side_effect = ValueError("synthetic-error")
    with pytest.raises(ValueError, match="synthetic-error"):
        transport.subscribe(["synthetic/a"])


def test_default_message_callback_and_optional_metadata_preserve_delivery(client):
    plain, details = Mock(), Mock()
    transport = connect(client, on_message=plain, on_message_details=details)
    instance = client[0]
    for retained in (False, True):
        delivery = SimpleNamespace(
            topic="synthetic/a", payload=b"synthetic", retain=retained
        )
        instance.on_message(instance, None, delivery)
        plain.assert_called_with("synthetic/a", b"synthetic")
        details.assert_called_with(delivery)
    assert transport.is_connected


def test_opt_in_tls_context_and_reconnect_policy_do_not_change_defaults(client):
    instance, factory = client
    context = ssl.create_default_context()
    transport = MqttTransport()
    transport.configure(
        "synthetic.example",
        client_id="synthetic-client",
        broker_username="synthetic-user",
        broker_password="synthetic-password",
        reconnect_on_failure=False,
        tls_context=context,
    )
    transport.connect()
    assert factory.call_args.kwargs["reconnect_on_failure"] is False
    instance.tls_set_context.assert_called_once_with(context)
    instance.tls_set.assert_not_called()
    instance.tls_insecure_set.assert_not_called()
    instance.username_pw_set.assert_called_once_with(
        "synthetic-user", "synthetic-password"
    )


def test_loop_start_failure_is_reported_only_to_opt_in_callback(client):
    instance, _factory = client
    instance.loop_start.return_value = 4
    failed = Mock()
    connect(client, on_loop_start_failure=failed)
    failed.assert_called_once_with()


@pytest.mark.parametrize("stop_loop_first", [True, False])
def test_disconnect_order_and_pending_confirmation_cleanup(client, stop_loop_first):
    success = Mock()
    transport = connect(client, on_subscribe_success=success)
    transport.subscribe(["synthetic/a"], qos=1)
    instance = client[0]
    order = []
    instance.disconnect.side_effect = lambda: order.append("disconnect")
    instance.loop_stop.side_effect = lambda: order.append("loop_stop")
    transport.disconnect(stop_loop_first=stop_loop_first)
    assert order == (
        ["loop_stop", "disconnect"] if stop_loop_first else ["disconnect", "loop_stop"]
    )
    assert not transport.is_connected
    assert transport.subscribed_topics == []
    suback(client, [1])
    success.assert_not_called()
    transport.disconnect(stop_loop_first=stop_loop_first)
    assert len(order) == 2


def test_opt_in_disconnect_still_stops_loop_and_clears_state_on_disconnect_error(
    client,
):
    transport = connect(client)
    instance = client[0]
    instance.disconnect.side_effect = ValueError("synthetic-error")
    with pytest.raises(ValueError, match="synthetic-error"):
        transport.disconnect(stop_loop_first=False)
    instance.loop_stop.assert_called_once()
    assert not transport.is_connected
    assert transport.subscribed_topics == []
    transport.disconnect(stop_loop_first=False)
    instance.disconnect.assert_called_once()


def test_connection_loss_discards_previous_subscription_confirmation(client):
    success = Mock()
    transport = connect(client, on_subscribe_success=success)
    transport.subscribe(["synthetic/a"], qos=1)
    instance = client[0]
    instance.on_disconnect(instance, None, {}, 1, None)
    suback(client, [1])
    success.assert_not_called()
    assert not transport.is_connected
