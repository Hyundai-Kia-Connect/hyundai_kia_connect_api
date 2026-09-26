"""Tests for the MQTT client module."""

import json

from hyundai_kia_connect_api.mqtt_client import (
    POSTFIX_CLOSE_MEDIA_VEHICLESTATUS,
    POSTFIX_CLOSE_VEHICLESTATUS,
    POSTFIX_OTA_PROGRESS,
    POSTFIX_OTA_SCHEDULEUPDATE,
    POSTFIX_REMOTE_COMMAND,
    SERVICE_HUB_PRODUCTION_BASES,
    SERVICE_HUB_STAGING_BASES,
    TOPIC_PREFIX_CONNECTION,
    TOPIC_PREFIX_DEVICE,
    TOPIC_PREFIX_LOCATION,
    TOPIC_PREFIX_VSS,
    MqttCacheCapabilities,
    MqttConnectionState,
    MqttHVACCommand,
    MqttRCCommandType,
    build_car_close_remote_topics,
    build_car_remote_topics,
    build_car_status_topics,
    build_device_close_remote_topics,
    build_device_remote_topics,
    build_ota_topics,
    build_topic,
    parse_mqtt_message,
)

# ---------------------------------------------------------------------------
# Topic builder tests
# ---------------------------------------------------------------------------


class TestBuildTopic:
    def test_prefix_infix_postfix(self):
        result = build_topic(TOPIC_PREFIX_DEVICE, "client123", POSTFIX_REMOTE_COMMAND)
        assert result == "device/client123/remotecontroller/command"

    def test_prefix_infix_no_postfix(self):
        result = build_topic(TOPIC_PREFIX_VSS, "vehicle1")
        assert result == "service/phone/_/vss/vehicle1"

    def test_empty_postfix(self):
        result = build_topic(TOPIC_PREFIX_DEVICE, "cid", "")
        assert result == "device/cid"


class TestBuildCarStatusTopics:
    def test_basic(self):
        caps = MqttCacheCapabilities()
        topics = build_car_status_topics("v1", caps)
        # Res topic excluded — broker rejects with rc=128
        assert len(topics) == 2
        assert any(TOPIC_PREFIX_VSS in t for t in topics)
        assert any(TOPIC_PREFIX_CONNECTION in t for t in topics)

    def test_with_location(self):
        caps = MqttCacheCapabilities(is_support_speed_event=True)
        topics = build_car_status_topics("v1", caps)
        # 2 base (VSS + Connection, no Res) + 1 location = 3
        assert len(topics) == 3
        assert any(TOPIC_PREFIX_LOCATION in t for t in topics)


class TestBuildDeviceRemoteTopics:
    def test_basic(self):
        topics = build_device_remote_topics("dev1")
        assert len(topics) == 6
        assert all("dev1" in t for t in topics)


class TestBuildCarRemoteTopics:
    def test_basic(self):
        topics = build_car_remote_topics("hu1")
        assert len(topics) == 5
        assert all("hu1" in t for t in topics)


class TestBuildCarCloseRemoteTopics:
    def test_basic(self):
        topics = build_car_close_remote_topics("hu1")
        assert len(topics) == 4


class TestBuildDeviceCloseRemoteTopics:
    def test_minimal(self):
        caps = MqttCacheCapabilities()
        topics = build_device_close_remote_topics("d1", caps)
        # Always: RemoteRes, ConnectionStatusRes, PreCondition
        assert len(topics) == 3

    def test_hvac_close(self):
        caps = MqttCacheCapabilities(has_hvac_close_remote=True)
        topics = build_device_close_remote_topics("d1", caps)
        assert len(topics) == 4
        assert any(POSTFIX_CLOSE_VEHICLESTATUS in t for t in topics)

    def test_media_close(self):
        caps = MqttCacheCapabilities(has_media_close_remote=True)
        topics = build_device_close_remote_topics("d1", caps)
        assert len(topics) == 4
        assert any(POSTFIX_CLOSE_MEDIA_VEHICLESTATUS in t for t in topics)

    def test_all(self):
        caps = MqttCacheCapabilities(
            has_hvac_close_remote=True,
            has_media_close_remote=True,
        )
        topics = build_device_close_remote_topics("d1", caps)
        assert len(topics) == 5


class TestBuildOtaTopics:
    def test_no_caps(self):
        caps = MqttCacheCapabilities()
        topics = build_ota_topics("v1", caps)
        assert len(topics) == 0

    def test_ota_progress(self):
        caps = MqttCacheCapabilities(is_support_ota_progress=True)
        topics = build_ota_topics("v1", caps)
        assert len(topics) == 1
        assert POSTFIX_OTA_PROGRESS in topics[0]

    def test_schedule_update(self):
        caps = MqttCacheCapabilities(is_support_schedule_update=True)
        topics = build_ota_topics("v1", caps)
        assert len(topics) == 1
        assert POSTFIX_OTA_SCHEDULEUPDATE in topics[0]

    def test_both(self):
        caps = MqttCacheCapabilities(
            is_support_ota_progress=True,
            is_support_schedule_update=True,
        )
        topics = build_ota_topics("v1", caps)
        assert len(topics) == 2


# ---------------------------------------------------------------------------
# Message parser tests
# ---------------------------------------------------------------------------


class TestParseMqttMessage:
    def _make_payload(self, body: dict) -> bytes:
        return json.dumps(body).encode("utf-8")

    def test_car_status_vss(self):
        topic = "service/phone/_/vss/vehicle123"
        payload = self._make_payload({"status": "OK"})
        msg = parse_mqtt_message(topic, payload)
        assert msg.topic_group == "CarStatus"
        assert msg.topic_type == "Status"
        assert msg.vehicle_id == "vehicle123"

    def test_car_status_connect(self):
        topic = "service/phone/_/connection/vehicle123"
        payload = self._make_payload({"connState": "ONLINE"})
        msg = parse_mqtt_message(topic, payload)
        assert msg.topic_group == "CarStatus"
        assert msg.topic_type == "Connect"

    def test_car_status_res(self):
        topic = "service/phone/_/res/vehicle123"
        payload = self._make_payload({"resCode": 0})
        msg = parse_mqtt_message(topic, payload)
        assert msg.topic_group == "CarStatus"
        assert msg.topic_type == "Res"

    def test_car_status_location(self):
        topic = "service/phone/_/location/vehicle123"
        payload = self._make_payload({"lat": 52.0, "lon": 20.0})
        msg = parse_mqtt_message(topic, payload)
        assert msg.topic_group == "CarStatus"
        assert msg.topic_type == "Location"

    def test_device_remote_command(self):
        topic = "device/clientABC/remotecontroller/command"
        payload = self._make_payload({"header": {}, "body": {}})
        msg = parse_mqtt_message(topic, payload)
        assert msg.topic_group == "DeviceRemote"
        assert msg.topic_type == "Command"
        assert msg.vehicle_id == "clientABC"

    def test_car_remote_connect(self):
        topic = "vehicle/huClient456/remotecontroller/connect"
        payload = self._make_payload({"header": {"tid": "123"}})
        msg = parse_mqtt_message(topic, payload)
        assert msg.topic_group == "CarRemote"
        assert msg.topic_type == "Connect"

    def test_car_close_remote(self):
        topic = "vehicle/huClient456/closeremote/connectionstatus/req"
        payload = self._make_payload({})
        msg = parse_mqtt_message(topic, payload)
        assert msg.topic_group == "CarCloseRemote"

    def test_device_close_remote_vehiclestatus(self):
        topic = "device/clientABC/closeremote/vehiclestatus"
        payload = self._make_payload({})
        msg = parse_mqtt_message(topic, payload)
        assert msg.topic_group == "DeviceCloseRemote"
        assert msg.vehicle_id == "clientABC"

    def test_ota_progress(self):
        topic = "vehicle/vehicle123/_/ota/otaprogress"
        payload = self._make_payload({"progress": 50})
        msg = parse_mqtt_message(topic, payload)
        assert msg.topic_group == "CarOta"
        assert msg.topic_type == "Progress"

    def test_ota_schedule_update(self):
        topic = "vehicle/vehicle123/_/ota/scheduleupdate"
        payload = self._make_payload({"operation": "schedule"})
        msg = parse_mqtt_message(topic, payload)
        assert msg.topic_group == "CarOta"
        assert msg.topic_type == "ScheduleUpdate"

    def test_invalid_json(self):
        topic = "service/phone/_/vss/vehicle123"
        payload = b"\xff\xfe invalid"
        msg = parse_mqtt_message(topic, payload)
        assert msg.topic_group == "CarStatus"
        assert "raw" in msg.payload

    def test_unknown_topic(self):
        topic = "unknown/prefix/data"
        payload = self._make_payload({"test": True})
        msg = parse_mqtt_message(topic, payload)
        assert msg.topic_group == "Unknown"
        assert msg.topic_type == "Unknown"


# ---------------------------------------------------------------------------
# Enum tests
# ---------------------------------------------------------------------------


class TestEnums:
    def test_connection_state(self):
        assert MqttConnectionState.ONLINE == "ONLINE"
        assert MqttConnectionState.OFFLINE == "OFFLINE"
        assert MqttConnectionState.UNKNOWN == "UNKNOWN"

    def test_rc_command_type(self):
        assert MqttRCCommandType.CONNECT == -1
        assert MqttRCCommandType.DISCONNECT == 0
        assert MqttRCCommandType.MODE_CHANGE == 1

    def test_hvac_command(self):
        assert MqttHVACCommand.PLAY == "PLAY"
        assert MqttHVACCommand.STOP == "STOP"


# ---------------------------------------------------------------------------
# Data class tests
# ---------------------------------------------------------------------------


class TestMqttCacheCapabilities:
    def test_defaults(self):
        caps = MqttCacheCapabilities()
        assert caps.has_hvac_close_remote is False
        assert caps.has_media_close_remote is False
        assert caps.is_support_speed_event is False
        assert caps.is_support_ota_progress is False
        assert caps.is_support_schedule_update is False
        assert caps.ccu_client_id is None
        assert caps.hu_client_id is None
        assert caps.vehicle_id is None
        assert caps.client_id is None

    def test_custom_values(self):
        caps = MqttCacheCapabilities(
            has_hvac_close_remote=True,
            vehicle_id="v123",
            client_id="c456",
        )
        assert caps.has_hvac_close_remote is True
        assert caps.vehicle_id == "v123"
        assert caps.client_id == "c456"


# ---------------------------------------------------------------------------
# Staging URL tests
# ---------------------------------------------------------------------------


class TestStagingUrls:
    def test_staging_bases_exist(self):
        assert len(SERVICE_HUB_STAGING_BASES) >= 12

    def test_hyundai_eu(self):
        key = "H_EU"
        assert key in SERVICE_HUB_STAGING_BASES
        assert "svchub" in SERVICE_HUB_STAGING_BASES[key]
        assert "31010" in SERVICE_HUB_STAGING_BASES[key]


class TestProductionUrls:
    def test_production_bases_exist(self):
        assert len(SERVICE_HUB_PRODUCTION_BASES) >= 17

    def test_hyundai_eu_production(self):
        key = "H_EU"
        assert key in SERVICE_HUB_PRODUCTION_BASES
        url = SERVICE_HUB_PRODUCTION_BASES[key]
        assert "egw-svchub-ccs-h-eu" in url
        assert "31010" in url
        assert not url.startswith("stg-")

    def test_kia_eu_production(self):
        key = "K_EU"
        assert key in SERVICE_HUB_PRODUCTION_BASES
        url = SERVICE_HUB_PRODUCTION_BASES[key]
        assert "egw-svchub-ccs-k-eu" in url

    def test_genesis_eu_production(self):
        key = "G_EU"
        assert key in SERVICE_HUB_PRODUCTION_BASES
        url = SERVICE_HUB_PRODUCTION_BASES[key]
        assert "egw-svchub-ccs-g-eu" in url

    def test_us_regions_exist(self):
        for key in ("H_US", "K_US", "G_US"):
            assert key in SERVICE_HUB_PRODUCTION_BASES
