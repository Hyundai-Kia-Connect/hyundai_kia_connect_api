"""Tests for the push-contract no-op defaults on the ApiImpl base."""


from hyundai_kia_connect_api.ApiImpl import ApiImpl
from hyundai_kia_connect_api.Token import Token
from hyundai_kia_connect_api.Vehicle import Vehicle


def test_default_supports_mqtt_push_false():
    impl = ApiImpl()
    assert impl.supports_mqtt_push is False


def test_get_push_broker_info_none():
    assert ApiImpl().get_push_broker_info(Token()) is None


def test_register_push_client_none():
    assert ApiImpl().register_push_client(Token()) is None


def test_register_push_vehicle_none():
    assert ApiImpl().register_push_vehicle(Token(), Vehicle()) is None


def test_get_push_vehicle_identity_none():
    assert ApiImpl().get_push_vehicle_identity(Token(), Vehicle()) is None


def test_get_push_connection_state_none():
    assert ApiImpl().get_push_connection_state(Token()) is None


def test_get_push_topics_empty():
    assert ApiImpl().get_push_topics(Token(), Vehicle()) == []


def test_parse_push_message_none():
    assert ApiImpl().parse_push_message("topic", b"payload") is None
