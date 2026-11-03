"""Japan MQTT contract tests: HTTP and MQTT are fully mocked."""

import json
import ssl
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from requests.structures import CaseInsensitiveDict

from hyundai_kia_connect_api.const import ORDER_STATUS
from hyundai_kia_connect_api.exceptions import (
    APIError,
    AuthenticationError,
    InvalidAPIResponseError,
    RequestTimeoutError,
)
from hyundai_kia_connect_api.japan_mqtt import JapanMqttSession
from hyundai_kia_connect_api.Token import Token


class FakeClient:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.username_pw_set = Mock()
        self.tls_set_context = Mock()
        self.connect_async = Mock()
        self.disconnect = Mock()
        self.loop_stop = Mock()
        self.subscribe = Mock(return_value=(0, 7))
        self.grant = [1, 1, 1]
        self.connect_code = 0

    def loop_start(self):
        self.on_connect(self, None, {}, self.connect_code, None)
        if self.connect_code == 0:
            self.on_subscribe(self, None, 7, self.grant, None)
        return 0


def response(payload, status=200, headers=None):
    result = Mock(status_code=status, headers=CaseInsensitiveDict(headers or {}))
    result.json.return_value = payload
    return result


@pytest.fixture
def session(monkeypatch):
    instance = JapanMqttSession(
        Token(access_token="Bearer synthetic-access"),
        "synthetic-vehicle",
        "synthetic-uuid",
        lambda: {
            "Authorization": "Bearer synthetic-access",
            "Stamp": "synthetic-stamp",
        },
    )
    instance._http_session = Mock()
    instance._http_session.request.side_effect = [
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
        response({"clientId": "synthetic-client", "deviceId": "synthetic-device"}),
        response({"vehicles": [{"unit": "ccu", "vehicleId": "synthetic-vehicle"}]}),
        response({}),
    ]
    client = FakeClient()
    monkeypatch.setattr(
        "hyundai_kia_connect_api.japan_mqtt.mqtt.Client", lambda *args, **kwargs: client
    )
    yield instance, client
    instance.close()


def message(topic, payload, retained=False):
    return SimpleNamespace(
        topic=topic, payload=json.dumps(payload).encode(), retain=retained
    )


def test_prepare_registers_and_subscribes_with_tls(session):
    instance, client = session
    instance.prepare()
    calls = instance._http_session.request.call_args_list
    assert calls[1].kwargs["json"] == {"unit": "mobile", "uuid": "synthetic-uuid_BLU"}
    assert calls[2].kwargs["params"] == {"carId": "synthetic-vehicle", "brand": "H"}
    assert calls[3].kwargs["json"]["protocols"] == [
        "service.phone.vss",
        "service.phone.connection",
        "service.phone.res",
    ]
    assert all(call.kwargs["allow_redirects"] is False for call in calls)
    client.username_pw_set.assert_called_once_with(
        "synthetic-client", "synthetic-access"
    )
    context = client.tls_set_context.call_args.args[0]
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    client.connect_async.assert_called_once_with(
        "broker.ap-northeast.hmgmobility.com", 8883, keepalive=15
    )
    client.subscribe.assert_called_once_with(
        [
            ("service/phone/_/vss/synthetic-vehicle", 1),
            ("service/phone/_/res/synthetic-client", 1),
            ("service/phone/_/connection/synthetic-vehicle", 1),
        ]
    )
    instance.prepare()
    assert len(instance._http_session.request.call_args_list) == 4


@pytest.mark.parametrize(
    "host",
    [
        "attacker.example",
        "ap-northeast.hmgmobility.com.attacker.example",
        "https://broker.ap-northeast.hmgmobility.com",
        "broker.ap-northeast.hmgmobility.com/",
        "broker..ap-northeast.hmgmobility.com",
        "user@broker.ap-northeast.hmgmobility.com",
    ],
)
def test_untrusted_broker_is_rejected_before_credentials(host):
    with pytest.raises(InvalidAPIResponseError):
        JapanMqttSession._broker({"mqtt": {"host": host, "port": 8883, "ssl": True}})


@pytest.mark.parametrize(
    "port,secure",
    [
        (0, True),
        (65536, True),
        (True, True),
        ("8883", True),
        (8883, False),
        (8883, "true"),
    ],
)
def test_invalid_port_and_tls_are_rejected(port, secure):
    with pytest.raises(InvalidAPIResponseError):
        JapanMqttSession._broker(
            {
                "mqtt": {
                    "host": "broker.ap-northeast.hmgmobility.com",
                    "port": port,
                    "ssl": secure,
                }
            }
        )


def test_matching_completion_before_http_ack_is_preserved(session):
    instance, client = session
    instance.prepare()
    instance._on_message(
        client,
        None,
        message(
            instance._topics[1],
            {"sid": "synthetic-action", "res-msg": "S", "res-code": "0000"},
        ),
    )
    instance.track("synthetic-action")
    assert instance.status("synthetic-action") == ORDER_STATUS.SUCCESS


def test_wrong_sid_retained_and_other_topics_do_not_complete_command(session):
    instance, client = session
    instance.prepare()
    instance.track("synthetic-action")
    for topic, sid, retained in [
        (instance._topics[1], "other-action", False),
        (instance._topics[1], "synthetic-action", True),
        ("unrelated/topic", "synthetic-action", False),
    ]:
        instance._on_message(
            client, None, message(topic, {"sid": sid, "res-msg": "S"}, retained)
        )
    assert instance.status("synthetic-action") == ORDER_STATUS.PENDING
    assert instance.status("synthetic-action", timeout=0.001) == ORDER_STATUS.TIMEOUT


def test_failure_message_is_not_assumed_success(session):
    instance, client = session
    instance.prepare()
    instance.track("synthetic-action")
    instance._on_message(
        client,
        None,
        message(
            instance._topics[1],
            {"sid": "synthetic-action", "res-msg": "F", "res-code": "5000"},
        ),
    )
    assert instance.status("synthetic-action") == ORDER_STATUS.FAILED


def test_latest_vss_state_is_copied(session):
    instance, client = session
    instance.prepare()
    instance._on_message(
        client,
        None,
        message(instance._topics[0], {"state": {"Vehicle": {"synthetic-value": 1}}}),
    )
    state = instance.latest_state
    state["synthetic-value"] = 2
    assert instance.latest_state == {"synthetic-value": 1}


@pytest.mark.parametrize("grant", [[0, 0, 0], [128, 1, 1], [1]])
def test_subscription_failure_disconnects_without_command(session, grant):
    instance, client = session
    client.grant = grant
    with pytest.raises(APIError, match="QoS 1"):
        instance.prepare()
    client.disconnect.assert_called_once()
    client.loop_stop.assert_called_once()


def test_connection_failure_disconnects(session):
    instance, client = session
    client.connect_code = 5
    with pytest.raises(APIError, match="connection was rejected"):
        instance.prepare()
    client.disconnect.assert_called_once()


def test_disconnect_interrupts_wait(session):
    instance, client = session
    instance.prepare()
    instance.track("synthetic-action")
    instance._on_disconnect(client, None, {}, 1, None)
    with pytest.raises(APIError, match="connection was lost"):
        instance.status("synthetic-action")


def test_missing_suback_times_out_and_closes(session, monkeypatch):
    instance, client = session
    monkeypatch.setattr(instance, "CONNECT_TIMEOUT", 0.001)

    def start():
        instance._on_connect(client, None, {}, 0, None)
        return 0

    client.loop_start = start
    with pytest.raises(RequestTimeoutError):
        instance.prepare()
    client.disconnect.assert_called_once()


def test_close_is_idempotent_and_clears_state(session):
    instance, client = session
    instance.prepare()
    instance.close()
    instance.close()
    client.disconnect.assert_called_once()
    client.loop_stop.assert_called_once()
    assert instance.latest_state is None
    with pytest.raises(APIError, match="closed"):
        instance.prepare()


def test_malformed_payloads_are_ignored(session):
    instance, client = session
    instance.prepare()
    instance.track("synthetic-action")
    for payload in (b"not-json", b"[]", b"\xff"):
        instance._on_message(
            client,
            None,
            SimpleNamespace(topic=instance._topics[1], payload=payload, retain=False),
        )
    assert instance.status("synthetic-action") == ORDER_STATUS.PENDING


def test_metadata_broker_identity_differs_from_api_car_id(session):
    instance, _client = session
    replies = list(instance._http_session.request.side_effect)
    replies[2] = response(
        {
            "vehicles": [
                {"unit": "tmu", "vehicleId": "synthetic-tmu"},
                {"unit": "ccu", "vehicleId": "synthetic-ccu"},
            ]
        }
    )
    instance._http_session.request.side_effect = replies
    instance.prepare()
    assert (
        instance._http_session.request.call_args_list[2].kwargs["params"]["carId"]
        == "synthetic-vehicle"
    )
    assert instance._topics[0].endswith("/synthetic-ccu")
    assert instance._topics[2].endswith("/synthetic-ccu")


@pytest.mark.parametrize("missing", [response({"vehicles": []}), response({}, 404)])
def test_missing_metadata_is_created_then_fetched(session, missing):
    instance, _client = session
    replies = list(instance._http_session.request.side_effect)
    instance._http_session.request.side_effect = [
        replies[0],
        replies[1],
        missing,
        response({"vehicleId": "synthetic-created"}),
        response({"vehicles": [{"unit": "tmu", "vehicleId": "synthetic-created"}]}),
        replies[3],
    ]
    instance.prepare()
    calls = instance._http_session.request.call_args_list
    assert calls[3].args == (
        "POST",
        instance.SERVICEHUB_URL + "/api/v3/servicehub/vehicleId",
    )
    assert calls[3].kwargs["json"] == {"carId": "synthetic-vehicle", "brand": "H"}
    assert calls[4].args[0] == "GET"
    assert instance._topics[0].endswith("/synthetic-created")


def test_missing_unit_does_not_assume_car_id_is_topic_identity():
    assert (
        JapanMqttSession._metadata_vehicle_id(
            {"vehicles": [{"vehicleId": "synthetic-vehicle"}]}
        )
        is None
    )
    with pytest.raises(InvalidAPIResponseError):
        JapanMqttSession._metadata_vehicle_id(
            {"vehicles": [{"unit": "ccu", "vehicleId": "synthetic/#"}]}
        )


def test_requests_json_decode_error_has_typed_safe_response(session):
    import requests

    instance, _client = session
    invalid = response(None)
    invalid.json.side_effect = requests.exceptions.JSONDecodeError(
        "synthetic-secret", "invalid", 0
    )
    instance._http_session.request.side_effect = [invalid]
    with pytest.raises(InvalidAPIResponseError) as caught:
        instance.prepare()
    assert "synthetic-secret" not in str(caught.value)
    assert instance._client is None


@pytest.mark.parametrize("header_name", ["tid", "Tid", "TID"])
def test_host_transaction_identity_comes_from_http_header(session, header_name):
    instance, _client = session
    replies = list(instance._http_session.request.side_effect)
    payload = replies[0].json.return_value.copy()
    payload.pop("tid")
    replies[0] = response(payload, headers={header_name: "synthetic-header-tid"})
    instance._http_session.request.side_effect = replies
    instance.prepare()
    calls = instance._http_session.request.call_args_list
    assert all(
        call.kwargs["headers"]["tid"] == "synthetic-header-tid" for call in calls[1:]
    )
    assert "tid" not in payload


def test_header_transaction_identity_takes_precedence_over_body(session):
    instance, _client = session
    replies = list(instance._http_session.request.side_effect)
    replies[0].headers["tid"] = "synthetic-header-tid"
    instance._http_session.request.side_effect = replies
    instance.prepare()
    assert (
        instance._http_session.request.call_args_list[1].kwargs["headers"]["tid"]
        == "synthetic-header-tid"
    )


@pytest.mark.parametrize(
    "tid", ["", "   ", "synthetic\r\ninvalid", "synthetic\x00invalid"]
)
def test_invalid_header_transaction_identity_fails_before_registration(session, tid):
    instance, _client = session
    replies = list(instance._http_session.request.side_effect)
    replies[0].headers["tid"] = tid
    instance._http_session.request.side_effect = replies
    with pytest.raises(
        InvalidAPIResponseError, match="transaction identity is missing"
    ):
        instance.prepare()
    assert instance._http_session.request.call_count == 1


def test_missing_host_transaction_identity_fails_before_registration(session):
    instance, _client = session
    replies = list(instance._http_session.request.side_effect)
    replies[0].json.return_value.pop("tid")
    instance._http_session.request.side_effect = replies
    with pytest.raises(
        InvalidAPIResponseError, match="transaction identity is missing"
    ):
        instance.prepare()
    assert instance._http_session.request.call_count == 1


def test_request_context_is_empty_before_prepare(session):
    instance, _client = session
    assert instance.request_context == {}


def test_request_context_uses_validated_broker_identity_and_is_copied(session):
    instance, _client = session
    replies = list(instance._http_session.request.side_effect)
    replies[2] = response(
        {"vehicles": [{"unit": "ccu", "vehicleId": "synthetic-broker-vehicle"}]}
    )
    instance._http_session.request.side_effect = replies
    instance.prepare()
    expected = {"clientId": "synthetic-client", "vehicleId": "synthetic-broker-vehicle"}
    assert instance.request_context == expected
    context = instance.request_context
    context["vehicleId"] = "changed-copy"
    assert instance.request_context == expected
    instance.close()
    assert instance.request_context == expected
    assert instance.closed


def test_failed_subscription_does_not_expose_request_context(session):
    instance, client = session
    client.grant = [128, 1, 1]
    with pytest.raises(APIError):
        instance.prepare()
    assert instance.request_context == {}


@pytest.mark.parametrize("status", [200, 201, 204])
def test_protocol_registration_does_not_require_json_body(session, status):
    instance, client = session
    replies = list(instance._http_session.request.side_effect)
    acknowledgement = response(None, status)
    acknowledgement.json.side_effect = ValueError("synthetic-private-response")
    replies[3] = acknowledgement
    instance._http_session.request.side_effect = replies

    instance.prepare()

    acknowledgement.json.assert_not_called()
    client.connect_async.assert_called_once()
    assert instance.request_context == {
        "clientId": "synthetic-client",
        "vehicleId": "synthetic-vehicle",
    }


@pytest.mark.parametrize("status", [302, 400, 401, 403, 429, 500])
def test_protocol_acknowledgement_must_still_have_success_http_status(session, status):
    instance, client = session
    replies = list(instance._http_session.request.side_effect)
    acknowledgement = response(None, status)
    replies[3] = acknowledgement
    instance._http_session.request.side_effect = replies

    with pytest.raises(AuthenticationError if status in (401, 403) else APIError):
        instance.prepare()

    acknowledgement.json.assert_not_called()
    client.connect_async.assert_not_called()
    assert instance.request_context == {}


@pytest.mark.parametrize(
    "method,path,stage",
    [
        ("GET", "/api/v3/servicehub/device/host", "host discovery"),
        ("POST", "/api/v3/servicehub/device/register", "client registration"),
        ("GET", "/api/v3/servicehub/vehicles/metadatalist", "vehicle metadata"),
        ("POST", "/api/v3/servicehub/vehicleId", "vehicle registration"),
    ],
)
def test_required_json_body_is_strict_with_safe_stage_diagnostics(
    session, method, path, stage
):
    instance, client = session
    invalid = response(None, 200)
    invalid.json.side_effect = ValueError("synthetic-private-response")
    instance._http_session.request.side_effect = [invalid]

    with pytest.raises(InvalidAPIResponseError) as caught:
        instance._http(method, path)

    assert str(caught.value) == (
        f"Japan MQTT bootstrap returned invalid JSON ({stage}; HTTP 200)"
    )
    assert "synthetic-private-response" not in str(caught.value)
    client.connect_async.assert_not_called()


def test_diagnostic_counters_are_safe_copied_and_survive_close(session):
    instance, client = session
    instance.prepare()
    instance.track("private-action")
    for topic, payload in (
        (instance._topics[0], {"state": {"Vehicle": {"private": "data"}}}),
        (instance._topics[2], {"state": "ONLINE"}),
        ("private-other-topic", {}),
        (instance._topics[1], {}),
        (instance._topics[1], {"sid": "private-other", "res-msg": "private-unknown"}),
        (instance._topics[1], {"sid": "private-action", "res-msg": "S"}),
    ):
        instance._on_message(client, None, message(topic, payload))
    summary = instance.diagnostics
    assert summary["messages_received"] == 6
    assert summary["vss_messages"] == 1
    assert summary["connection_messages"] == 1
    assert summary["other_topic_messages"] == 1
    assert summary["result_messages"] == 3
    assert summary["missing_sid_messages"] == 1
    assert summary["unknown_result_messages"] == 1
    assert summary["unmatched_result_messages"] == 1
    assert summary["matched_results"] == 1
    assert summary["commands_tracked"] == 1
    assert summary["last_status"] == "success"
    assert "private" not in json.dumps(summary)
    summary["matched_results"] = 999
    instance.close()
    assert instance.diagnostics["matched_results"] == 1
    assert instance.diagnostics["last_status"] == "success"
    assert instance.diagnostics["closed"] is True
    assert instance.diagnostics["ready"] is False


def test_diagnostics_count_invalid_messages_without_recording_payload(session):
    instance, client = session
    instance.prepare()
    for payload in (b"private-invalid-json", b"[]"):
        instance._on_message(
            client,
            None,
            SimpleNamespace(
                topic=instance._topics[1],
                payload=payload,
                retain=False,
            ),
        )
    instance._on_message(client, None, message(instance._topics[1], {}, retained=True))
    instance._on_message(
        client,
        None,
        SimpleNamespace(
            topic=instance._topics[1],
            payload=b"x" * (instance.MAX_MESSAGE_BYTES + 1),
            retain=False,
        ),
    )
    assert instance.diagnostics["messages_received"] == 4
    assert instance.diagnostics["malformed_messages"] == 2
    assert instance.diagnostics["retained_messages"] == 1
    assert instance.diagnostics["oversized_messages"] == 1
    assert "private" not in json.dumps(instance.diagnostics)


def test_diagnostics_track_early_results_once_and_timeout(session):
    instance, client = session
    instance.prepare()
    instance._on_message(
        client,
        None,
        message(
            instance._topics[1],
            {
                "sid": "private-early",
                "res-msg": "S",
            },
        ),
    )
    instance.track("private-early")
    instance.track("private-early")
    assert instance.diagnostics["matched_results"] == 1
    assert instance.diagnostics["commands_tracked"] == 1
    instance.track("private-pending")
    assert instance.status("private-pending", timeout=0.001) == ORDER_STATUS.TIMEOUT
    instance.close()
    assert instance.diagnostics["last_status"] == "timeout"


def test_late_callbacks_cannot_resurrect_closed_session(session):
    instance, client = session
    instance.prepare()
    instance.close()
    instance._on_connect(client, None, {}, 0, None)
    instance._on_subscribe(client, None, 7, [1, 1, 1], None)
    instance._on_connect_fail(client, None)
    assert instance.closed
    assert instance.diagnostics["ready"] is False
    assert instance.diagnostics["last_status"] == "not_started"
    client.subscribe.assert_called_once()


def test_close_during_bootstrap_prevents_client_start(session):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    instance, client = session
    entered, release = Event(), Event()
    first = next(instance._http_session.request.side_effect)

    def request(*args, **kwargs):
        entered.set()
        assert release.wait(2), "bootstrap release was not signaled"
        return first

    instance._http_session.request.side_effect = request
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(instance.prepare)
        try:
            assert entered.wait(2), "bootstrap never started"
            instance.close()
        finally:
            release.set()
        with pytest.raises(APIError, match="closed"):
            future.result(timeout=2)
    assert instance._http_session.request.call_count == 1
    client.connect_async.assert_not_called()
    assert instance._client is None
    assert instance.diagnostics["ready"] is False


def test_close_interrupts_subscription_wait_without_waiting_for_timeout(session):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    instance, client = session
    started = Event()

    def start():
        instance._on_connect(client, None, {}, 0, None)
        started.set()
        return 0

    client.loop_start = start
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(instance.prepare)
        assert started.wait(2), "network loop did not start"
        instance.close()
        with pytest.raises(APIError, match="closed"):
            future.result(timeout=2)
    client.disconnect.assert_called_once()
    client.loop_stop.assert_called_once()
    assert instance.diagnostics["ready"] is False


def test_concurrent_prepare_bootstraps_only_once(session, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event, Lock

    instance, client = session
    entered, release, second_entered = Event(), Event(), Event()
    real_lock = Lock()

    class ObservedLock:
        entries = 0
        entries_lock = Lock()

        def __enter__(self):
            with self.entries_lock:
                self.entries += 1
                if self.entries == 2:
                    second_entered.set()
            real_lock.acquire()

        def __exit__(self, *args):
            real_lock.release()

    instance._prepare_lock = ObservedLock()
    replies = list(instance._http_session.request.side_effect)
    paths = {
        "/api/v3/servicehub/device/host": replies[0],
        "/api/v3/servicehub/device/register": replies[1],
        "/api/v3/servicehub/vehicles/metadatalist": replies[2],
        "/api/v3/servicehub/device/protocol": replies[3],
    }

    def request(method, url, **kwargs):
        if url.endswith("/device/host"):
            entered.set()
            assert release.wait(2), "bootstrap release was not signaled"
        return paths[url.removeprefix(instance.SERVICEHUB_URL)]

    instance._http_session.request.side_effect = request
    factory = Mock(return_value=client)
    monkeypatch.setattr("hyundai_kia_connect_api.japan_mqtt.mqtt.Client", factory)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(instance.prepare)
        try:
            assert entered.wait(2), "bootstrap never started"
            second = executor.submit(instance.prepare)
            assert second_entered.wait(2), "second prepare never reached bootstrap lock"
        finally:
            release.set()
        first.result(timeout=2)
        second.result(timeout=2)
    factory.assert_called_once()
    assert instance._http_session.request.call_count == 4


def test_disconnected_session_cannot_start_a_replacement_client(session):
    instance, client = session
    instance.prepare()
    instance._on_disconnect(client, None, {}, 1, None)
    with pytest.raises(APIError, match="connection was lost"):
        instance.prepare()
    assert instance.closed
    assert instance._http_session.request.call_count == 4
    client.disconnect.assert_called_once()


@pytest.mark.parametrize("timeout", [True, False, float("nan"), float("inf"), -1, 121])
def test_invalid_wait_timeout_is_rejected_before_wait(session, timeout):
    instance, _client = session
    instance.prepare()
    instance.track("synthetic-action")
    with pytest.raises(APIError, match="wait timeout is invalid"):
        instance.status("synthetic-action", timeout=timeout)


def test_excessively_nested_json_cannot_stop_completion_processing(session):
    instance, client = session
    instance.prepare()
    instance.track("synthetic-action")
    instance._on_message(
        client,
        None,
        SimpleNamespace(
            topic=instance._topics[1],
            payload=b"[" * 2000 + b"0" + b"]" * 2000,
            retain=False,
        ),
    )
    instance._on_message(
        client,
        None,
        message(instance._topics[1], {"sid": "synthetic-action", "res-msg": "S"}),
    )
    assert instance.status("synthetic-action") == ORDER_STATUS.SUCCESS
    assert instance.diagnostics["malformed_messages"] == 1
