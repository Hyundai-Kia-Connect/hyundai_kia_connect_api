"""Test targetSOC parsing for USA Kia API.

These tests use real API response data from a 2020 Kia Niro EV to verify
that charge limits (targetSOC) are correctly extracted.

Root cause: The cmm/gvi (cached state) endpoint does NOT include targetSOC
for some vehicles, but the rems/rvs (force refresh) endpoint does. The fix
ensures charge limits are parsed from the force refresh response and
preserved across subsequent cached updates.
"""

import datetime as dt
import logging

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
    api.temperature_range = range(62, 83)
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


def test_force_refresh_overwrites_stale_cached_values():
    """Force refresh data should overwrite stale cached values."""
    api = _make_api()
    vehicle = _make_vehicle()
    vehicle.ev_charge_limits_ac = 90
    vehicle.ev_charge_limits_dc = 70

    # Force refresh has ac=80, dc=80 — should overwrite the stale cached values
    api._update_charge_limits_from_force_refresh(vehicle, FORCE_REFRESH_RESPONSE)
    assert vehicle.ev_charge_limits_ac == 80
    assert vehicle.ev_charge_limits_dc == 80


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


def test_force_refresh_preserves_cached_when_response_has_no_valid_values():
    """If force refresh targetSOC has no valid numbers, keep cached values."""
    api = _make_api()
    vehicle = _make_vehicle()
    vehicle.ev_charge_limits_ac = 90
    vehicle.ev_charge_limits_dc = 70

    # Response has targetSOC key but with no matching plugType entries
    bad_response = {
        "payload": {
            "vehicleStatusRpt": {
                "vehicleStatus": {
                    "evStatus": {
                        "targetSOC": [
                            {"plugType": 99, "targetSOClevel": 50},
                        ]
                    }
                }
            }
        }
    }
    api._update_charge_limits_from_force_refresh(vehicle, bad_response)

    # Cached values preserved since force refresh had no valid AC/DC entries
    assert vehicle.ev_charge_limits_ac == 90
    assert vehicle.ev_charge_limits_dc == 70


def test_force_refresh_partial_ac_only():
    """Force refresh returns only AC (plugType=1), no DC entry — DC preserved."""
    api = _make_api()
    vehicle = _make_vehicle()
    vehicle.ev_charge_limits_ac = 90
    vehicle.ev_charge_limits_dc = 70

    partial_response = {
        "payload": {
            "vehicleStatusRpt": {
                "vehicleStatus": {
                    "evStatus": {
                        "targetSOC": [
                            {"plugType": 1, "targetSOClevel": 80},
                        ]
                    }
                }
            }
        }
    }
    api._update_charge_limits_from_force_refresh(vehicle, partial_response)

    assert vehicle.ev_charge_limits_ac == 80  # updated from response
    assert vehicle.ev_charge_limits_dc == 70  # preserved cached value


def test_force_refresh_partial_dc_only():
    """Force refresh returns only DC (plugType=0), no AC entry — AC preserved."""
    api = _make_api()
    vehicle = _make_vehicle()
    vehicle.ev_charge_limits_ac = 90
    vehicle.ev_charge_limits_dc = 70

    partial_response = {
        "payload": {
            "vehicleStatusRpt": {
                "vehicleStatus": {
                    "evStatus": {
                        "targetSOC": [
                            {"plugType": 0, "targetSOClevel": 60},
                        ]
                    }
                }
            }
        }
    }
    api._update_charge_limits_from_force_refresh(vehicle, partial_response)

    assert vehicle.ev_charge_limits_ac == 90  # preserved cached value
    assert vehicle.ev_charge_limits_dc == 60  # updated from response


def test_force_refresh_empty_target_soc_list():
    """Empty targetSOC list [] — cached values preserved."""
    api = _make_api()
    vehicle = _make_vehicle()
    vehicle.ev_charge_limits_ac = 90
    vehicle.ev_charge_limits_dc = 70

    empty_list_response = {
        "payload": {
            "vehicleStatusRpt": {"vehicleStatus": {"evStatus": {"targetSOC": []}}}
        }
    }
    api._update_charge_limits_from_force_refresh(vehicle, empty_list_response)

    assert vehicle.ev_charge_limits_ac == 90
    assert vehicle.ev_charge_limits_dc == 70


def test_force_refresh_empty_list_no_cached():
    """Empty targetSOC list [] with no cached values — stays None."""
    api = _make_api()
    vehicle = _make_vehicle()

    empty_list_response = {
        "payload": {
            "vehicleStatusRpt": {"vehicleStatus": {"evStatus": {"targetSOC": []}}}
        }
    }
    api._update_charge_limits_from_force_refresh(vehicle, empty_list_response)

    assert vehicle.ev_charge_limits_ac is None
    assert vehicle.ev_charge_limits_dc is None


def test_force_refresh_garbage_string_values():
    """targetSOClevel is a string like 'unknown' — cached values preserved."""
    api = _make_api()
    vehicle = _make_vehicle()
    vehicle.ev_charge_limits_ac = 90
    vehicle.ev_charge_limits_dc = 70

    garbage_response = {
        "payload": {
            "vehicleStatusRpt": {
                "vehicleStatus": {
                    "evStatus": {
                        "targetSOC": [
                            {"plugType": 0, "targetSOClevel": "unknown"},
                            {"plugType": 1, "targetSOClevel": "N/A"},
                        ]
                    }
                }
            }
        }
    }
    api._update_charge_limits_from_force_refresh(vehicle, garbage_response)

    assert vehicle.ev_charge_limits_ac == 90
    assert vehicle.ev_charge_limits_dc == 70


def test_force_refresh_none_target_soc_level():
    """targetSOClevel is None — cached values preserved."""
    api = _make_api()
    vehicle = _make_vehicle()
    vehicle.ev_charge_limits_ac = 90
    vehicle.ev_charge_limits_dc = 70

    none_response = {
        "payload": {
            "vehicleStatusRpt": {
                "vehicleStatus": {
                    "evStatus": {
                        "targetSOC": [
                            {"plugType": 0, "targetSOClevel": None},
                            {"plugType": 1, "targetSOClevel": None},
                        ]
                    }
                }
            }
        }
    }
    api._update_charge_limits_from_force_refresh(vehicle, none_response)

    assert vehicle.ev_charge_limits_ac == 90
    assert vehicle.ev_charge_limits_dc == 70


def test_force_refresh_missing_target_soc_level_key():
    """targetSOC entries missing targetSOClevel key — exception caught, cached preserved."""
    api = _make_api()
    vehicle = _make_vehicle()
    vehicle.ev_charge_limits_ac = 90
    vehicle.ev_charge_limits_dc = 70

    malformed_response = {
        "payload": {
            "vehicleStatusRpt": {
                "vehicleStatus": {
                    "evStatus": {
                        "targetSOC": [
                            {"plugType": 0},
                            {"plugType": 1},
                        ]
                    }
                }
            }
        }
    }
    api._update_charge_limits_from_force_refresh(vehicle, malformed_response)

    # Exception handler catches KeyError, cached values preserved
    assert vehicle.ev_charge_limits_ac == 90
    assert vehicle.ev_charge_limits_dc == 70


def test_force_refresh_non_list_target_soc(caplog):
    """targetSOC is a non-list type (e.g. dict) — exception caught, cached preserved."""
    api = _make_api()
    vehicle = _make_vehicle()
    vehicle.ev_charge_limits_ac = 90
    vehicle.ev_charge_limits_dc = 70

    dict_response = {
        "payload": {
            "vehicleStatusRpt": {
                "vehicleStatus": {
                    "evStatus": {"targetSOC": {"plugType": 0, "targetSOClevel": 80}}
                }
            }
        }
    }
    with caplog.at_level(logging.WARNING):
        api._update_charge_limits_from_force_refresh(vehicle, dict_response)

    # Exception handler catches TypeError, cached values preserved
    assert vehicle.ev_charge_limits_ac == 90
    assert vehicle.ev_charge_limits_dc == 70
    assert "Failed to parse targetSOC" in caplog.text


def test_force_refresh_garbage_values_emit_warnings(caplog):
    """Garbage targetSOClevel values should emit WARNING logs."""
    api = _make_api()
    vehicle = _make_vehicle()
    vehicle.ev_charge_limits_ac = 90
    vehicle.ev_charge_limits_dc = 70

    garbage_response = {
        "payload": {
            "vehicleStatusRpt": {
                "vehicleStatus": {
                    "evStatus": {
                        "targetSOC": [
                            {"plugType": 0, "targetSOClevel": "unknown"},
                            {"plugType": 1, "targetSOClevel": "N/A"},
                        ]
                    }
                }
            }
        }
    }
    with caplog.at_level(logging.WARNING):
        api._update_charge_limits_from_force_refresh(vehicle, garbage_response)

    assert vehicle.ev_charge_limits_ac == 90
    assert vehicle.ev_charge_limits_dc == 70
    assert "invalid AC charge limit" in caplog.text
    assert "invalid DC charge limit" in caplog.text


def test_force_refresh_empty_list_no_warning(caplog):
    """Empty targetSOC list should NOT emit warnings (absence, not corruption)."""
    api = _make_api()
    vehicle = _make_vehicle()
    vehicle.ev_charge_limits_ac = 90
    vehicle.ev_charge_limits_dc = 70

    empty_list_response = {
        "payload": {
            "vehicleStatusRpt": {"vehicleStatus": {"evStatus": {"targetSOC": []}}}
        }
    }
    with caplog.at_level(logging.WARNING):
        api._update_charge_limits_from_force_refresh(vehicle, empty_list_response)

    assert vehicle.ev_charge_limits_ac == 90
    assert vehicle.ev_charge_limits_dc == 70
    assert "invalid" not in caplog.text


def test_force_refresh_missing_key_emits_warning(caplog):
    """Missing targetSOClevel key should emit WARNING from exception handler."""
    api = _make_api()
    vehicle = _make_vehicle()
    vehicle.ev_charge_limits_ac = 90
    vehicle.ev_charge_limits_dc = 70

    malformed_response = {
        "payload": {
            "vehicleStatusRpt": {
                "vehicleStatus": {
                    "evStatus": {
                        "targetSOC": [
                            {"plugType": 0},
                            {"plugType": 1},
                        ]
                    }
                }
            }
        }
    }
    with caplog.at_level(logging.WARNING):
        api._update_charge_limits_from_force_refresh(vehicle, malformed_response)

    assert vehicle.ev_charge_limits_ac == 90
    assert vehicle.ev_charge_limits_dc == 70
    assert "Failed to parse targetSOC" in caplog.text


def test_force_refresh_bool_values_rejected():
    """Bool values should not be accepted as valid charge limits."""
    api = _make_api()
    vehicle = _make_vehicle()
    vehicle.ev_charge_limits_ac = 90
    vehicle.ev_charge_limits_dc = 70

    bool_response = {
        "payload": {
            "vehicleStatusRpt": {
                "vehicleStatus": {
                    "evStatus": {
                        "targetSOC": [
                            {"plugType": 0, "targetSOClevel": True},
                            {"plugType": 1, "targetSOClevel": False},
                        ]
                    }
                }
            }
        }
    }
    api._update_charge_limits_from_force_refresh(vehicle, bool_response)

    # bool values rejected, cached values preserved
    assert vehicle.ev_charge_limits_ac == 90
    assert vehicle.ev_charge_limits_dc == 70


# --- /evc/gts (Get Target SOC) endpoint tests ---

EVC_GTS_RESPONSE_SUCCESS = {
    "status": {
        "statusCode": 0,
        "errorType": 0,
        "errorCode": 0,
        "errorMessage": "Success with response body",
    },
    "payload": {
        "targetSOClist": [
            {"plugType": 0, "targetSOClevel": 100},
            {"plugType": 1, "targetSOClevel": 80},
        ]
    },
}


class _FakeResponse:
    """Minimal fake for requests.Response."""

    def __init__(self, json_data, status_code=200):
        self._json = json_data
        self.status_code = status_code
        self.headers = {}

    def json(self):
        return self._json


def _make_api_with_fake_get(response_json):
    """Create a KiaUvoApiUSA with a fake GET that returns the given JSON."""
    api = _make_api()
    api.API_URL = "https://api.owners.kia.com/apigw/v1/"

    def fake_get(token, url, vehicle):
        return _FakeResponse(response_json)

    api.get_request_with_logging_and_active_session = fake_get
    return api


def test_evc_gts_sets_charge_limits():
    """Verify /evc/gts response correctly sets AC and DC charge limits."""
    api = _make_api_with_fake_get(EVC_GTS_RESPONSE_SUCCESS)
    vehicle = _make_vehicle()

    api._get_charge_targets(None, vehicle)

    assert vehicle.ev_charge_limits_dc == 100
    assert vehicle.ev_charge_limits_ac == 80


def test_evc_gts_overwrites_stale_values():
    """Verify /evc/gts overwrites previously cached charge limits."""
    api = _make_api_with_fake_get(EVC_GTS_RESPONSE_SUCCESS)
    vehicle = _make_vehicle()
    vehicle.ev_charge_limits_ac = 60
    vehicle.ev_charge_limits_dc = 60

    api._get_charge_targets(None, vehicle)

    assert vehicle.ev_charge_limits_dc == 100
    assert vehicle.ev_charge_limits_ac == 80


def test_evc_gts_error_response_preserves_cached():
    """Verify error response from /evc/gts doesn't clear cached values."""
    error_response = {
        "status": {
            "statusCode": 1,
            "errorType": 1,
            "errorCode": 1043,
            "errorMessage": "This feature is not supported by vehicle",
        }
    }
    api = _make_api_with_fake_get(error_response)
    vehicle = _make_vehicle()
    vehicle.ev_charge_limits_ac = 90
    vehicle.ev_charge_limits_dc = 70

    api._get_charge_targets(None, vehicle)

    assert vehicle.ev_charge_limits_ac == 90
    assert vehicle.ev_charge_limits_dc == 70


def test_evc_gts_empty_list_preserves_cached():
    """Empty targetSOClist from /evc/gts preserves cached values."""
    empty_response = {
        "status": {
            "statusCode": 0,
            "errorType": 0,
            "errorCode": 0,
            "errorMessage": "Success with response body",
        },
        "payload": {"targetSOClist": []},
    }
    api = _make_api_with_fake_get(empty_response)
    vehicle = _make_vehicle()
    vehicle.ev_charge_limits_ac = 90
    vehicle.ev_charge_limits_dc = 70

    api._get_charge_targets(None, vehicle)

    assert vehicle.ev_charge_limits_ac == 90
    assert vehicle.ev_charge_limits_dc == 70


def test_evc_gts_zero_values_ignored():
    """Zero targetSOClevel from /evc/gts should be ignored (pre-refresh state)."""
    zero_response = {
        "status": {
            "statusCode": 0,
            "errorType": 0,
            "errorCode": 0,
            "errorMessage": "Success with response body",
        },
        "payload": {
            "targetSOClist": [
                {"plugType": 0, "targetSOClevel": 0},
                {"plugType": 1, "targetSOClevel": 0},
            ]
        },
    }
    api = _make_api_with_fake_get(zero_response)
    vehicle = _make_vehicle()
    vehicle.ev_charge_limits_ac = 90
    vehicle.ev_charge_limits_dc = 70

    api._get_charge_targets(None, vehicle)

    assert vehicle.ev_charge_limits_ac == 90
    assert vehicle.ev_charge_limits_dc == 70


def test_evc_gts_exception_preserves_cached(caplog):
    """Network exception during /evc/gts should be caught, cached values preserved."""
    api = _make_api()
    api.API_URL = "https://api.owners.kia.com/apigw/v1/"

    def fake_get_raises(token, url, vehicle):
        raise ConnectionError("network down")

    api.get_request_with_logging_and_active_session = fake_get_raises
    vehicle = _make_vehicle()
    vehicle.ev_charge_limits_ac = 90
    vehicle.ev_charge_limits_dc = 70

    with caplog.at_level(logging.DEBUG):
        api._get_charge_targets(None, vehicle)

    assert vehicle.ev_charge_limits_ac == 90
    assert vehicle.ev_charge_limits_dc == 70
    assert "Failed to get charge targets" in caplog.text


def test_evc_gts_bool_values_rejected():
    """Bool targetSOClevel from /evc/gts should be rejected."""
    bool_response = {
        "status": {
            "statusCode": 0,
            "errorType": 0,
            "errorCode": 0,
            "errorMessage": "Success with response body",
        },
        "payload": {
            "targetSOClist": [
                {"plugType": 0, "targetSOClevel": True},
                {"plugType": 1, "targetSOClevel": False},
            ]
        },
    }
    api = _make_api_with_fake_get(bool_response)
    vehicle = _make_vehicle()

    api._get_charge_targets(None, vehicle)

    assert vehicle.ev_charge_limits_ac is None
    assert vehicle.ev_charge_limits_dc is None


def test_update_cached_state_skips_evc_gts_for_ice_vehicle():
    """Verify /evc/gts is NOT called for ICE vehicles (no ev_battery_percentage)."""
    api = _make_api()
    api.API_URL = "https://api.owners.kia.com/apigw/v1/"

    call_count = {"n": 0}

    def fake_get(token, url, vehicle):
        call_count["n"] += 1
        return _FakeResponse(EVC_GTS_RESPONSE_SUCCESS)

    api.get_request_with_logging_and_active_session = fake_get
    api._get_cached_vehicle_state = lambda token, vehicle: {}
    api._update_vehicle_properties = lambda vehicle, state: None

    vehicle = _make_vehicle()
    assert vehicle.ev_battery_percentage is None  # ICE vehicle

    api.update_vehicle_with_cached_state(None, vehicle)

    assert call_count["n"] == 0
    assert vehicle.ev_charge_limits_ac is None
    assert vehicle.ev_charge_limits_dc is None


def test_update_cached_state_calls_evc_gts_for_ev_vehicle():
    """Verify /evc/gts IS called for EV vehicles (ev_battery_percentage set)."""
    api = _make_api()
    api.API_URL = "https://api.owners.kia.com/apigw/v1/"

    call_count = {"n": 0}

    def fake_get(token, url, vehicle):
        call_count["n"] += 1
        return _FakeResponse(EVC_GTS_RESPONSE_SUCCESS)

    api.get_request_with_logging_and_active_session = fake_get
    api._get_cached_vehicle_state = lambda token, vehicle: {}

    def fake_update_props(vehicle, state):
        vehicle.ev_battery_percentage = 67  # simulate EV

    api._update_vehicle_properties = fake_update_props

    vehicle = _make_vehicle()
    api.update_vehicle_with_cached_state(None, vehicle)

    assert call_count["n"] == 1
    assert vehicle.ev_charge_limits_dc == 100
    assert vehicle.ev_charge_limits_ac == 80
