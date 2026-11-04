"""Tests for the neutral Service Hub phone-family wire contract."""

from hyundai_kia_connect_api.mqtt_service_hub import (
    MQTT_PROTOCOL_ID_CCU_UPDATE,
    MQTT_PROTOCOL_ID_CONNECTION,
    MQTT_PROTOCOL_ID_RES,
    MQTT_PROTOCOL_ID_VSS,
    TOPIC_PREFIX_CONNECTION,
    TOPIC_PREFIX_LOCATION,
    TOPIC_PREFIX_VSS,
    PushMessage,
    build_car_status_topics,
    build_service_hub_url_with_params,
    classify_phone_topic,
    decode_payload,
    parse_service_hub_message,
)


def test_topic_constant_values():
    """The wire constants moved verbatim (kia_uvo consumers may import them)."""
    assert TOPIC_PREFIX_VSS == "service/phone/_/vss/"
    assert TOPIC_PREFIX_CONNECTION == "service/phone/_/connection/"
    assert TOPIC_PREFIX_LOCATION == "service/phone/_/location/"
    assert MQTT_PROTOCOL_ID_VSS == "service.phone.vss"
    assert MQTT_PROTOCOL_ID_CONNECTION == "service.phone.connection"
    assert MQTT_PROTOCOL_ID_RES == "service.phone.res"
    assert MQTT_PROTOCOL_ID_CCU_UPDATE == "statesync.vehicle.ccu.update"


def test_push_message_defaults():
    message = PushMessage("CarStatus", "Status", "veh1", payload={"a": 1})
    assert message.topic_group == "CarStatus"
    assert message.topic_type == "Status"
    assert message.vehicle_id == "veh1"
    assert message.payload == {"a": 1}
    assert message.is_status is False


def test_build_car_status_topics_exact():
    """vss + connection only; the res topic is excluded (broker rc=128)."""
    assert build_car_status_topics("veh1") == [
        "service/phone/_/vss/veh1",
        "service/phone/_/connection/veh1",
    ]


def test_classify_phone_topic():
    assert classify_phone_topic("service/phone/_/vss/veh1") == (
        "CarStatus",
        "Status",
        "veh1",
    )
    assert classify_phone_topic("service/phone/_/connection/veh1") == (
        "CarStatus",
        "Connect",
        "veh1",
    )
    assert classify_phone_topic("service/phone/_/location/veh1") == (
        "CarStatus",
        "Location",
        "veh1",
    )
    assert classify_phone_topic("device/dev/remotecontroller/connect") is None


def test_classify_phone_topic_trailing_slash():
    assert classify_phone_topic("service/phone/_/vss/veh1/") == (
        "CarStatus",
        "Status",
        "veh1",
    )


def test_decode_payload_json_and_hex():
    assert decode_payload(b'{"a": 1}') == {"a": 1}
    assert decode_payload(b"\x80\x01") == {"raw": "8001"}


def test_parse_service_hub_message_status():
    message = parse_service_hub_message("service/phone/_/vss/veh1", b'{"a": 1}')
    assert message is not None
    assert message.topic_group == "CarStatus"
    assert message.topic_type == "Status"
    assert message.vehicle_id == "veh1"
    assert message.payload == {"a": 1}
    assert message.is_status is True


def test_parse_service_hub_message_non_phone_is_none():
    assert parse_service_hub_message("device/x/remotecontroller/connect", b"{}") is None


def test_url_with_params():
    """The moved encoding behavior: '@' preserved (live-probed), space percent-encoded."""
    assert build_service_hub_url_with_params("https://h", "/p", {}) == "https://h/p"
    assert (
        build_service_hub_url_with_params("https://h", "/p", {"a": "@x y"})
        == "https://h/p?a=@x%20y"
    )
