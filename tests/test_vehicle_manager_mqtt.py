"""Tests for VehicleManager MQTT orchestration."""

import threading
from unittest.mock import MagicMock, patch

from hyundai_kia_connect_api import VehicleManager
from hyundai_kia_connect_api.mqtt_client import (
    MqttMessage,
)
from hyundai_kia_connect_api.Token import Token
from hyundai_kia_connect_api.Vehicle import Vehicle

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_vm() -> VehicleManager:
    """Create a VehicleManager with CCI/EU region (key=9) and Hyundai (key=2)."""
    vm = VehicleManager(
        region=9,  # REGION_EUROPE_CCI
        brand=2,  # BRAND_HYUNDAI (int key)
        username="test@example.com",
        password="testpass",
        pin="1234",
    )
    vm.token = Token(access_token="at", refresh_token="rt", device_id="did")
    vehicle = Vehicle(id="vid123", name="Test Car")
    vehicle.hu_client_id = "hu-123"
    vm.vehicles["vid123"] = vehicle
    return vm


# ---------------------------------------------------------------------------
# start_mqtt tests
# ---------------------------------------------------------------------------


class TestStartMqtt:
    def test_returns_none_when_paho_unavailable(self):
        """If paho-mqtt is not installed, start_mqtt returns None gracefully."""
        vm = _make_vm()
        with (
            patch.dict("sys.modules", {"paho.mqtt.client": None}),
            patch.object(vm, "get_mqtt_host", return_value=None),
        ):
            # paho-mqtt import fails inside start_mqtt
            result = vm.start_mqtt("vid123")
            assert result is None

    def test_returns_none_when_host_unavailable(self):
        """If Service Hub returns no broker, start_mqtt returns None."""
        vm = _make_vm()
        with (
            patch.object(vm, "get_mqtt_host", return_value=None),
            patch.object(vm, "register_mqtt_client"),
        ):
            result = vm.start_mqtt("vid123")
            assert result is None

    def test_returns_none_when_registration_fails(self):
        """If device registration fails, start_mqtt returns None."""
        vm = _make_vm()
        with (
            patch.object(
                vm,
                "get_mqtt_host",
                return_value={
                    "mqtt_host": "broker.example.com",
                    "mqtt_port": 8883,
                    "use_ssl": True,
                },
            ),
            patch.object(vm, "register_mqtt_client", return_value=None),
        ):
            result = vm.start_mqtt("vid123")
            assert result is None

    @patch("hyundai_kia_connect_api.mqtt_client.HyundaiMqttClient")
    def test_full_flow_success(self, mock_client_cls):
        """Service Hub steps 1-5 wire the client: configure() with the
        registration credentials (client_id as username fallback, access
        token as password fallback), connect(), and the identity fields
        prepared for subscribe-on-connect."""
        vm = _make_vm()
        mock_client = mock_client_cls.return_value
        mock_client.is_connected = False

        with (
            patch.object(
                vm,
                "get_mqtt_host",
                return_value={
                    "mqtt_host": "egw.example.com:31010",
                    "mqtt_port": 8883,
                    "use_ssl": True,
                },
            ),
            patch.object(
                vm,
                "register_mqtt_client",
                return_value={"client_id": "client-abc"},
            ),
            patch.object(vm, "register_mqtt_protocol"),
            patch.object(vm, "get_mqtt_metadata", return_value=None),
            patch.object(vm, "get_mqtt_vehicle_id", return_value="mqtt-vid-1"),
        ):
            result = vm.start_mqtt("vid123")

        assert result is mock_client
        mock_client_cls.assert_called_once()
        mock_client.configure.assert_called_once_with(
            broker_host="egw.example.com",  # :31010 stripped for MQTT
            broker_port=8883,
            use_ssl=True,
            client_id="client-abc",
            username="client-abc",  # fallback: clientId as username
            password="at",  # fallback: access token, "Bearer " stripped
        )
        mock_client.connect.assert_called_once()
        assert vm._mqtt_vehicle_id == "mqtt-vid-1"
        # Not connected yet — subscribe is deferred to the on_connect
        # callback, not called inline.
        mock_client.subscribe_topics.assert_not_called()
        # The deferred subscription carries the identity fields.
        deferred = vm._mqtt_client._on_connect
        assert callable(deferred)

    @patch("hyundai_kia_connect_api.mqtt_client.HyundaiMqttClient")
    def test_subscribe_caps_carry_identity_fields(self, mock_client_cls):
        """The capabilities handed to subscribe_topics carry the real
        identity sources (registration client_id, vehicle metadata
        hu_client_id, mqtt_vid infix); the capability flags stay False
        until the MQTTCacheResponse wiring lands."""
        vm = _make_vm()
        mock_client = mock_client_cls.return_value
        mock_client.is_connected = True

        with (
            patch.object(
                vm,
                "get_mqtt_host",
                return_value={
                    "mqtt_host": "egw.example.com",
                    "mqtt_port": 8883,
                    "use_ssl": True,
                },
            ),
            patch.object(
                vm,
                "register_mqtt_client",
                return_value={"client_id": "client-abc"},
            ),
            patch.object(vm, "register_mqtt_protocol"),
            patch.object(vm, "get_mqtt_metadata", return_value=None),
            patch.object(vm, "get_mqtt_vehicle_id", return_value="mqtt-vid-1"),
        ):
            vm.start_mqtt("vid123")

        kwargs = mock_client.subscribe_topics.call_args.kwargs
        caps = kwargs["capabilities"]
        assert kwargs["vehicle_id"] == "mqtt-vid-1"
        assert kwargs["client_id"] == "client-abc"
        assert kwargs["hu_client_id"] == "hu-123"
        assert caps.ccu_client_id == "client-abc"
        assert caps.client_id == "client-abc"
        assert caps.hu_client_id == "hu-123"
        assert caps.vehicle_id == "mqtt-vid-1"
        assert caps.has_hvac_close_remote is False
        assert caps.is_support_ota_progress is False


# ---------------------------------------------------------------------------
# stop_mqtt tests
# ---------------------------------------------------------------------------


class TestStopMqtt:
    def test_disconnects_client(self):
        """stop_mqtt calls disconnect and cleans up."""
        vm = _make_vm()
        mock_client = MagicMock()
        vm._mqtt_client = mock_client

        vm.stop_mqtt()

        mock_client.disconnect.assert_called_once()
        assert vm._mqtt_client is None

    def test_clears_action_tracking(self):
        """stop_mqtt clears pending action events and results."""
        vm = _make_vm()
        vm._mqtt_action_events = {"tid1": threading.Event()}
        vm._mqtt_action_results = {"tid1": "success"}
        mock_client = MagicMock()
        vm._mqtt_client = mock_client

        vm.stop_mqtt()

        assert len(vm._mqtt_action_events) == 0
        assert len(vm._mqtt_action_results) == 0

    def test_noop_when_no_client(self):
        """stop_mqtt is safe when no MQTT client is active."""
        vm = _make_vm()
        vm.stop_mqtt()  # Should not raise


# ---------------------------------------------------------------------------
# wait_for_mqtt_result tests
# ---------------------------------------------------------------------------


class TestWaitForMqttResult:
    def test_returns_result_when_event_set(self):
        """Returns result code when the event is signaled."""
        vm = _make_vm()

        # Simulate: set result and signal event before wait
        def set_result():
            vm._mqtt_action_results["action1"] = "0"  # success resCode
            vm._mqtt_action_events["action1"].set()

        # Schedule the set on a separate thread
        timer = threading.Timer(0.05, set_result)
        timer.start()

        result = vm.wait_for_mqtt_result("action1", timeout=5.0)
        assert result == "0"

        timer.join()

    def test_returns_none_on_timeout(self):
        """Returns None when no result arrives within timeout."""
        vm = _make_vm()
        result = vm.wait_for_mqtt_result("action_timeout", timeout=0.1)
        assert result is None

    def test_cleans_up_event_after_result(self):
        """Action events are cleaned up after receiving result."""
        vm = _make_vm()

        def set_result():
            vm._mqtt_action_results["action2"] = "success"
            vm._mqtt_action_events["action2"].set()

        timer = threading.Timer(0.05, set_result)
        timer.start()

        vm.wait_for_mqtt_result("action2", timeout=5.0)
        assert "action2" not in vm._mqtt_action_events

        timer.join()

    def test_cleans_up_event_after_timeout(self):
        """Action events are cleaned up even on timeout."""
        vm = _make_vm()
        vm.wait_for_mqtt_result("action3", timeout=0.1)
        assert "action3" not in vm._mqtt_action_events


# ---------------------------------------------------------------------------
# _on_mqtt_message tests
# ---------------------------------------------------------------------------


class TestOnMqttMessage:
    def test_res_message_sets_event(self):
        """DeviceRemote Res message with tid signals waiting action."""
        vm = _make_vm()
        event = threading.Event()
        # Pre-register the event as if we're waiting for this tid
        vm._mqtt_action_events["tid-abc"] = event

        msg = MqttMessage(
            topic_group="DeviceRemote",
            topic_type="Res",
            vehicle_id="vid123",
            payload={
                "header": {"tid": "tid-abc"},
                "body": {"resCode": 0},
            },
        )

        vm._on_mqtt_message(msg)

        assert event.is_set()
        assert vm._mqtt_action_results["tid-abc"] == "0"

    def test_connect_message_sets_event(self):
        """CarRemote Connect message with tid signals waiting action."""
        vm = _make_vm()
        event = threading.Event()
        vm._mqtt_action_events["tid-xyz"] = event

        msg = MqttMessage(
            topic_group="CarRemote",
            topic_type="Connect",
            vehicle_id="vid123",
            payload={
                "header": {"tId": "tid-xyz"},
                "body": {},
            },
        )

        vm._on_mqtt_message(msg)
        assert event.is_set()
        assert vm._mqtt_action_results["tid-xyz"] == "connected"

    def test_status_message_does_not_set_event(self):
        """CarStatus messages don't have tids for action tracking."""
        vm = _make_vm()

        msg = MqttMessage(
            topic_group="CarStatus",
            topic_type="Status",
            vehicle_id="vid123",
            payload={"status": "OK"},
        )

        # Should not raise, just log
        vm._on_mqtt_message(msg)
        assert len(vm._mqtt_action_events) == 0

    def test_res_without_matching_tid_is_ignored(self):
        """Res message with unknown tid is ignored (no waiting action)."""
        vm = _make_vm()

        msg = MqttMessage(
            topic_group="DeviceRemote",
            topic_type="Res",
            vehicle_id="vid123",
            payload={
                "header": {"tid": "unknown-tid"},
                "body": {"resCode": 0},
            },
        )

        # Should not raise
        vm._on_mqtt_message(msg)
        assert "unknown-tid" not in vm._mqtt_action_results


# ---------------------------------------------------------------------------
# Properties tests
# ---------------------------------------------------------------------------


class TestMqttProperties:
    def test_mqtt_client_property(self):
        vm = _make_vm()
        assert vm.mqtt_client is None

    def test_is_mqtt_connected_when_no_client(self):
        vm = _make_vm()
        assert vm.is_mqtt_connected is False

    def test_is_mqtt_connected_when_client_connected(self):
        vm = _make_vm()
        mock_client = MagicMock()
        mock_client.is_connected = True
        vm._mqtt_client = mock_client
        assert vm.is_mqtt_connected is True

    def test_is_mqtt_connected_when_client_disconnected(self):
        vm = _make_vm()
        mock_client = MagicMock()
        mock_client.is_connected = False
        vm._mqtt_client = mock_client
        assert vm.is_mqtt_connected is False

    def test_get_mqtt_connection_state_passes_through(self):
        """get_mqtt_connection_state delegates to the API impl method."""
        vm = _make_vm()
        vm.api.get_mqtt_connection_state = MagicMock(return_value="ONLINE")
        assert vm.get_mqtt_connection_state() == "ONLINE"
        vm.api.get_mqtt_connection_state.assert_called_once_with(vm.token)

    def test_api_impl_provides_get_mqtt_connection_state(self):
        """Regression: the CCI/EU API impl must implement connstate lookup.

        VehicleManager.get_mqtt_connection_state calls
        self.api.get_mqtt_connection_state — an API impl without the
        method raises AttributeError at runtime.
        """
        from hyundai_kia_connect_api.HyundaiCciApiEU import HyundaiCciApiEU

        api = HyundaiCciApiEU(region=9, brand=2, language="en")
        assert callable(getattr(api, "get_mqtt_connection_state", None))


# ---------------------------------------------------------------------------
# CarStatus callback tests
# ---------------------------------------------------------------------------


class TestMqttStatusCallback:
    def test_car_status_calls_callback(self):
        """CarStatus messages invoke the registered status callback."""
        vm = _make_vm()
        vm._mqtt_vehicle_id = "vid123"
        received = []

        def on_status(vehicle_id, topic_type):
            received.append((vehicle_id, topic_type))

        vm.set_mqtt_status_callback(on_status)

        msg = MqttMessage(
            topic_group="CarStatus",
            topic_type="Status",
            vehicle_id="vid123",
            payload={"status": "ok"},
        )
        vm._on_mqtt_message(msg)

        assert received == [("vid123", "Status")]

    def test_car_status_location_calls_callback(self):
        """CarStatus Location messages invoke callback with Location type."""
        vm = _make_vm()
        vm._mqtt_vehicle_id = "vid123"
        received = []

        def on_status(vehicle_id, topic_type):
            received.append((vehicle_id, topic_type))

        vm.set_mqtt_status_callback(on_status)

        msg = MqttMessage(
            topic_group="CarStatus",
            topic_type="Location",
            vehicle_id="vid123",
            payload={"lat": 50.0, "lon": 20.0},
        )
        vm._on_mqtt_message(msg)

        assert received == [("vid123", "Location")]

    def test_device_remote_does_not_call_status_callback(self):
        """DeviceRemote messages don't trigger status callback."""
        vm = _make_vm()
        received = []

        def on_status(vehicle_id, topic_type):
            received.append((vehicle_id, topic_type))

        vm.set_mqtt_status_callback(on_status)

        msg = MqttMessage(
            topic_group="DeviceRemote",
            topic_type="Res",
            vehicle_id="vid123",
            payload={
                "header": {"tid": "tid-abc"},
                "body": {"resCode": 0},
            },
        )
        vm._on_mqtt_message(msg)

        assert received == []

    def test_no_callback_set_does_not_raise(self):
        """CarStatus messages are safe when no callback is registered."""
        vm = _make_vm()
        # No callback registered
        msg = MqttMessage(
            topic_group="CarStatus",
            topic_type="Status",
            vehicle_id="vid123",
            payload={"status": "ok"},
        )
        # Should not raise
        vm._on_mqtt_message(msg)

    def test_callback_exception_does_not_crash(self):
        """Exceptions in the status callback are caught and logged."""
        vm = _make_vm()

        def bad_callback(vehicle_id, topic_type):
            raise RuntimeError("boom")

        vm.set_mqtt_status_callback(bad_callback)

        msg = MqttMessage(
            topic_group="CarStatus",
            topic_type="Status",
            vehicle_id="vid123",
            payload={"status": "ok"},
        )
        # Should not raise, exception is caught
        vm._on_mqtt_message(msg)

    def test_unregister_callback(self):
        """Setting callback to None unregisters it."""
        vm = _make_vm()
        received = []

        def on_status(vehicle_id, topic_type):
            received.append((vehicle_id, topic_type))

        vm.set_mqtt_status_callback(on_status)
        vm.set_mqtt_status_callback(None)

        msg = MqttMessage(
            topic_group="CarStatus",
            topic_type="Status",
            vehicle_id="vid123",
            payload={"status": "ok"},
        )
        vm._on_mqtt_message(msg)

        assert received == []

    def test_callback_with_action_result(self):
        """CarStatus + Res message: both callback and action result fire."""
        vm = _make_vm()
        vm._mqtt_vehicle_id = "vid123"
        status_received = []
        event = threading.Event()
        vm._mqtt_action_events["tid-both"] = event

        def on_status(vehicle_id, topic_type):
            status_received.append((vehicle_id, topic_type))

        vm.set_mqtt_status_callback(on_status)

        # CarStatus.Status triggers callback
        status_msg = MqttMessage(
            topic_group="CarStatus",
            topic_type="Status",
            vehicle_id="vid123",
            payload={"evStatus": "charging"},
        )
        vm._on_mqtt_message(status_msg)
        assert status_received == [("vid123", "Status")]

        # DeviceRemote.Res triggers action result (not callback)
        res_msg = MqttMessage(
            topic_group="DeviceRemote",
            topic_type="Res",
            vehicle_id="vid123",
            payload={
                "header": {"tid": "tid-both"},
                "body": {"resCode": 0},
            },
        )
        vm._on_mqtt_message(res_msg)
        assert event.is_set()
        assert vm._mqtt_action_results["tid-both"] == "0"
        # Callback should NOT be called for DeviceRemote
        assert len(status_received) == 1


# ---------------------------------------------------------------------------
# Reconnect tests
# ---------------------------------------------------------------------------


class TestMqttReconnect:
    def test_on_disconnect_triggers_reconnect_thread(self):
        """Unexpected disconnect starts a reconnect thread."""
        vm = _make_vm()
        vm._mqtt_vehicle_id = "vid123"

        # Simulate unexpected disconnect
        vm._on_mqtt_disconnect("unexpected (rc=7)")

        # Reconnect cancel event should be set (exhausted or pending)
        # We can't easily verify the thread without waiting, but we can
        # check that _mqtt_reconnect_cancel was created
        assert vm._mqtt_reconnect_cancel is not None

    def test_clean_disconnect_does_not_reconnect(self):
        """Clean disconnect (rc=0) does not trigger reconnect."""
        vm = _make_vm()
        vm._mqtt_vehicle_id = "vid123"

        vm._on_mqtt_disconnect("clean disconnect")

        # Should not start reconnect — cancel event stays None
        # (it's only set by _reconnect_with_backoff)
        assert vm._mqtt_reconnect_cancel is None

    def test_stop_mqtt_cancels_reconnect(self):
        """stop_mqtt cancels any pending reconnect attempts."""
        vm = _make_vm()
        vm._mqtt_vehicle_id = "vid123"

        # Trigger reconnect
        vm._on_mqtt_disconnect("unexpected (rc=7)")
        assert vm._mqtt_reconnect_cancel is not None

        # Stop MQTT should cancel reconnect
        vm.stop_mqtt()
        assert vm._mqtt_reconnect_cancel is None

    def test_stop_mqtt_clears_vehicle_id(self):
        """stop_mqtt clears the stored vehicle ID."""
        vm = _make_vm()
        vm._mqtt_vehicle_id = "vid123"

        vm.stop_mqtt()
        assert vm._mqtt_vehicle_id is None

    def test_disconnect_cleans_up_old_client(self):
        """Unexpected disconnect cleans up the old client before reconnecting."""
        vm = _make_vm()
        vm._mqtt_vehicle_id = "vid123"
        mock_client = MagicMock()
        vm._mqtt_client = mock_client

        vm._on_mqtt_disconnect("unexpected (rc=7)")

        # Old client should have been cleaned up
        assert vm._mqtt_client is None
        mock_client.disconnect.assert_called_once()

    def test_reconnect_attempts_token_refresh(self):
        """Reconnect calls check_and_refresh_token before start_mqtt."""
        vm = _make_vm()
        vm._mqtt_vehicle_id = "vid123"
        vm._mqtt_reconnect_initial_delay = 0  # No delay for testing

        call_log = []

        def mock_check_refresh():
            call_log.append("check_and_refresh_token")
            # Cancel after first attempt
            if vm._mqtt_reconnect_cancel:
                vm._mqtt_reconnect_cancel.set()
            return True

        vm.check_and_refresh_token = mock_check_refresh
        vm.start_mqtt = MagicMock(return_value=None)

        vm._on_mqtt_disconnect("unexpected (rc=7)")

        import time

        time.sleep(0.3)

        # Token refresh should have been attempted
        assert "check_and_refresh_token" in call_log

    def test_reconnect_continues_after_token_refresh_failure(self):
        """Reconnect continues even if token refresh fails."""
        vm = _make_vm()
        vm._mqtt_vehicle_id = "vid123"
        vm._mqtt_reconnect_initial_delay = 0  # No delay for testing

        call_log = []

        def mock_check_refresh_fail():
            call_log.append("check_and_refresh_token_failed")
            # Cancel after first attempt
            if vm._mqtt_reconnect_cancel:
                vm._mqtt_reconnect_cancel.set()
            raise RuntimeError("Token expired")

        vm.check_and_refresh_token = mock_check_refresh_fail
        vm.start_mqtt = MagicMock(return_value=None)

        vm._on_mqtt_disconnect("unexpected (rc=7)")

        import time

        time.sleep(0.3)

        # Token refresh should have been attempted even though it failed
        assert "check_and_refresh_token_failed" in call_log

    def test_cleanup_mqtt_client_stops_loop(self):
        """_cleanup_mqtt_client disconnects the old client and clears reference."""
        vm = _make_vm()
        mock_client = MagicMock()
        vm._mqtt_client = mock_client

        vm._cleanup_mqtt_client()

        assert vm._mqtt_client is None
        mock_client.disconnect.assert_called_once()

    def test_cleanup_mqtt_client_noop_when_none(self):
        """_cleanup_mqtt_client is safe when no client exists."""
        vm = _make_vm()
        assert vm._mqtt_client is None

        vm._cleanup_mqtt_client()

        assert vm._mqtt_client is None
