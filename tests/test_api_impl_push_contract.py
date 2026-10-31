"""Tests for the push-contract defaults on the ApiImpl base."""

import pytest

from hyundai_kia_connect_api.ApiImpl import ApiImpl
from hyundai_kia_connect_api.const import MQTT_PUSH_CONNECTION_STATE
from hyundai_kia_connect_api.Token import Token
from hyundai_kia_connect_api.Vehicle import Vehicle


def test_capabilities_live_on_vehicle_supports():
    vehicle = Vehicle(id="v1", name="test", model="test")
    # Tri-state: unknown until the region's parser sets it.
    assert vehicle.supports.mqtt_push is None


@pytest.mark.parametrize(
    "call",
    [
        lambda impl: impl.get_push_broker_info(Token()),
        lambda impl: impl.register_push_client(Token()),
        lambda impl: impl.register_push_vehicle(Token(), Vehicle()),
        lambda impl: impl.get_push_vehicle_identity(Token(), Vehicle()),
        lambda impl: impl.get_push_connection_state(Token()),
        lambda impl: impl.get_push_topics(Token(), Vehicle()),
        lambda impl: impl.parse_push_message("topic", b"payload"),
    ],
)
def test_base_push_methods_not_implemented(call):
    """Base defaults raise NotImplementedError like the rest of ApiImpl."""
    with pytest.raises(NotImplementedError):
        call(ApiImpl())


def test_connection_state_enum_values():
    expected = {v.value for v in MQTT_PUSH_CONNECTION_STATE}
    assert expected == {"ONLINE", "OFFLINE", "UNKNOWN"}
