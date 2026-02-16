"""Test targetSOC parsing for USA Kia API.

These tests use real API response data from a 2020 Kia Niro EV to verify
that charge limits (targetSOC) are correctly extracted.

Root cause: The cmm/gvi (cached state) endpoint does NOT include targetSOC
for some vehicles, but the rems/rvs (force refresh) endpoint does. The fix
ensures charge limits are parsed from the force refresh response and
preserved across subsequent cached updates.
"""

import datetime as dt

from hyundai_kia_connect_api.KiaUvoApiUSA import KiaUvoApiUSA
from hyundai_kia_connect_api.Vehicle import Vehicle

# Real cmm/gvi response from a 2020 Kia Niro EV (USA) - note: no targetSOC
CACHED_RESPONSE_PAYLOAD = {
    "vinKey": "969ee79b-test-key",
    "lastVehicleInfo": {
        "vehicleNickName": "My NIRO EV",
        "vehicleStatusRpt": {
            "statusType": "2",
            "reportDate": {"utc": "20260131002545", "offset": -8.0},
            "vehicleStatus": {
                "climate": {
                    "airCtrl": False,
                    "defrost": False,
                    "airTemp": {"value": "72", "unit": 1},
                    "heatingAccessory": {
                        "steeringWheel": 0,
                        "sideMirror": 0,
                        "rearWindow": 0,
                    },
                },
                "engine": False,
                "doorLock": True,
                "doorStatus": {
                    "frontLeft": 0,
                    "frontRight": 0,
                    "backLeft": 0,
                    "backRight": 0,
                    "trunk": 0,
                    "hood": 0,
                },
                "lowFuelLight": False,
                "evStatus": {
                    "batteryCharge": False,
                    "batteryStatus": 81,
                    "batteryPlugin": 2,
                    "remainChargeTime": [
                        {
                            "remainChargeType": 3,
                            "timeInterval": {"value": 0, "unit": 4},
                        }
                    ],
                    "drvDistance": [
                        {
                            "type": 1,
                            "rangeByFuel": {
                                "evModeRange": {"value": 214.372995, "unit": 3},
                                "totalAvailableRange": {
                                    "value": 214.372995,
                                    "unit": 3,
                                },
                            },
                        }
                    ],
                    "syncDate": {"utc": "20260131002507", "offset": -5.0},
                    "wirelessCharging": False,
                    # NOTE: no "targetSOC" key here - this is the bug
                },
                "ign3": True,
                "transCond": True,
                "tirePressure": {
                    "all": 0,
                    "frontLeft": 0,
                    "frontRight": 0,
                    "rearLeft": 0,
                    "rearRight": 0,
                },
                "syncDate": {"utc": "20260131002507", "offset": -5.0},
                "batteryStatus": {
                    "stateOfCharge": 86,
                    "sensorStatus": 0,
                    "deliveryMode": 0,
                },
            },
        },
    },
}

# Real rems/rvs response from the same vehicle - note: HAS targetSOC
FORCE_REFRESH_RESPONSE = {
    "status": {
        "statusCode": 0,
        "errorType": 0,
        "errorCode": 0,
        "errorMessage": "Success with response body",
    },
    "payload": {
        "vehicleStatusRpt": {
            "statusType": "0",
            "reportDate": {"utc": "20260131002544", "offset": -8.0},
            "vehicleStatus": {
                "evStatus": {
                    "batteryCharge": False,
                    "batteryStatus": 81,
                    "batteryPlugin": 2,
                    "targetSOC": [
                        {
                            "plugType": 0,
                            "targetSOClevel": 80,
                            "dte": {
                                "type": 1,
                                "rangeByFuel": {
                                    "gasModeRange": {"value": 0.0, "unit": 0},
                                    "evModeRange": {
                                        "value": 214.372995,
                                        "unit": 3,
                                    },
                                    "totalAvailableRange": {
                                        "value": 214.372995,
                                        "unit": 3,
                                    },
                                },
                            },
                        },
                        {
                            "plugType": 1,
                            "targetSOClevel": 80,
                            "dte": {
                                "type": 1,
                                "rangeByFuel": {
                                    "gasModeRange": {"value": 0.0, "unit": 0},
                                    "evModeRange": {
                                        "value": 214.372995,
                                        "unit": 3,
                                    },
                                    "totalAvailableRange": {
                                        "value": 214.372995,
                                        "unit": 3,
                                    },
                                },
                            },
                        },
                    ],
                },
            },
        }
    },
}


def _make_vehicle():
    return Vehicle(
        id="test-id",
        name="My NIRO EV",
        model="NIRO EV",
        key="test-key",
        timezone=dt.timezone(dt.timedelta(hours=-5)),
    )


def _make_api():
    api = object.__new__(KiaUvoApiUSA)
    api.data_timezone = dt.timezone(dt.timedelta(hours=-5))
    api.temperature_range = list(range(62, 82))
    return api


def test_cached_response_has_no_target_soc():
    """Verify the cached (cmm/gvi) response does NOT include targetSOC."""
    api = _make_api()
    vehicle = _make_vehicle()

    api._update_vehicle_properties(vehicle, CACHED_RESPONSE_PAYLOAD)

    # EV battery data should be parsed fine
    assert vehicle.ev_battery_percentage == 81
    # But charge limits should remain None since cmm/gvi lacks targetSOC
    assert vehicle.ev_charge_limits_ac is None
    assert vehicle.ev_charge_limits_dc is None


def test_force_refresh_response_has_target_soc():
    """Verify charge limits are parsed from the rems/rvs response."""
    api = _make_api()
    vehicle = _make_vehicle()

    # First, simulate a cached update (no targetSOC)
    api._update_vehicle_properties(vehicle, CACHED_RESPONSE_PAYLOAD)
    assert vehicle.ev_charge_limits_ac is None

    # Now simulate parsing the force refresh response
    api._update_charge_limits_from_force_refresh(vehicle, FORCE_REFRESH_RESPONSE)

    assert vehicle.ev_charge_limits_ac == 80
    assert vehicle.ev_charge_limits_dc == 80


def test_charge_limits_preserved_across_cached_updates():
    """Verify charge limits from force refresh survive subsequent cached updates."""
    api = _make_api()
    vehicle = _make_vehicle()

    # Force refresh populates charge limits
    api._update_charge_limits_from_force_refresh(vehicle, FORCE_REFRESH_RESPONSE)
    assert vehicle.ev_charge_limits_ac == 80
    assert vehicle.ev_charge_limits_dc == 80

    # Subsequent cached update should NOT overwrite them with None
    api._update_vehicle_properties(vehicle, CACHED_RESPONSE_PAYLOAD)
    assert vehicle.ev_charge_limits_ac == 80
    assert vehicle.ev_charge_limits_dc == 80


def test_force_refresh_skips_when_cached_already_has_values():
    """If cached response already populated charge limits, skip force refresh parsing."""
    api = _make_api()
    vehicle = _make_vehicle()
    vehicle.ev_charge_limits_ac = 90
    vehicle.ev_charge_limits_dc = 70

    # Should not overwrite existing values
    api._update_charge_limits_from_force_refresh(vehicle, FORCE_REFRESH_RESPONSE)
    assert vehicle.ev_charge_limits_ac == 90
    assert vehicle.ev_charge_limits_dc == 70


def test_force_refresh_with_missing_target_soc():
    """Handle force refresh response that also lacks targetSOC."""
    api = _make_api()
    vehicle = _make_vehicle()

    empty_response = {
        "payload": {"vehicleStatusRpt": {"vehicleStatus": {"evStatus": {}}}}
    }
    api._update_charge_limits_from_force_refresh(vehicle, empty_response)

    assert vehicle.ev_charge_limits_ac is None
    assert vehicle.ev_charge_limits_dc is None
