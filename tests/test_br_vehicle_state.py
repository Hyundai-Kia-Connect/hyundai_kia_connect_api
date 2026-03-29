from hyundai_kia_connect_api.HyundaiBlueLinkApiBR import HyundaiBlueLinkApiBR
from hyundai_kia_connect_api.const import TEMPERATURE_C
from hyundai_kia_connect_api.Vehicle import Vehicle


def test_br_vehicle_state_maps_extended_diagnostics():
    api = HyundaiBlueLinkApiBR(region=8, brand=2)
    vehicle = Vehicle(id="vehicle-1")
    state = {
        "time": "20260329123505",
        "engine": False,
        "airCtrlOn": False,
        "airTemp": {"value": "15H", "unit": 0, "hvacTempType": 1},
        "acc": False,
        "transCond": True,
        "sleepModeCheck": False,
        "engineOilStatus": False,
        "tailLampStatus": 1,
        "hazardStatus": 0,
        "remoteWaitingTimeAlert": {
            "remoteControlAvailable": 1,
            "remoteControlWaitingTime": 168,
        },
        "lampWireStatus": {
            "stopLamp": {"leftLamp": False, "rightLamp": False},
            "headLamp": {
                "headLampStatus": False,
                "leftLowLamp": True,
                "rightLowLamp": False,
            },
            "turnSignalLamp": {
                "leftFrontLamp": False,
                "rightFrontLamp": True,
                "leftRearLamp": False,
                "rightRearLamp": False,
            },
        },
    }

    api._update_vehicle_properties(vehicle, state)

    assert vehicle.air_temperature == 21
    assert vehicle._air_temperature_unit == TEMPERATURE_C
    assert vehicle.accessory_on is False
    assert vehicle.transmission_condition is True
    assert vehicle.sleep_mode_check is False
    assert vehicle.engine_oil_warning_is_on is False
    assert vehicle.tail_lamps_are_on is True
    assert vehicle.hazard_lights_are_on is False
    assert vehicle.remote_control_available is True
    assert vehicle.remote_control_waiting_time == 168
    assert vehicle.headlamp_status is False
    assert vehicle.headlamp_left_low is True
    assert vehicle.headlamp_right_low is False
    assert vehicle.stop_lamp_left is False
    assert vehicle.stop_lamp_right is False
    assert vehicle.turn_signal_right_front is True
