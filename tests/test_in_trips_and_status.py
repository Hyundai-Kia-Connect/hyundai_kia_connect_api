"""KiaUvoApiIN trip detail, status extras and location motion.

Payload shapes are taken from a live Kia India Seltos (ICE, 2025) probe with
identifiers and coordinates removed.
"""

from unittest.mock import MagicMock

from hyundai_kia_connect_api.const import ENGINE_TYPES
from hyundai_kia_connect_api.KiaUvoApiIN import KiaUvoApiIN
from hyundai_kia_connect_api.Token import Token
from hyundai_kia_connect_api.Vehicle import Vehicle

OK = {"retCode": "S", "resCode": "0000"}

DAY_TRIP_RESPONSE = {
    **OK,
    "resMsg": {
        "tripPeriodType": 1,
        "dayTripList": [
            {
                "tripDay": "20261017",
                "dayTripCnt": 2,
                "tripDrvTime": 79,
                "tripIdleTime": 24,
                "tripDist": 22,
                "tripAvgSpeed": 16.7,
                "tripMaxSpeed": 67,
                "tripList": [
                    {
                        "tripStartTime": "20261017201503",
                        "tripStartCoord": {"lat": 0, "lon": 0},
                        "tripEndTime": "20261017202457",
                        "tripEndCoord": {"lat": 0, "lon": 0},
                        "serviceTID": 20261017201503,
                    },
                    {
                        "tripStartTime": "20261017081707",
                        "tripStartCoord": {"lat": 0, "lon": 0},
                        "tripEndTime": "20261017084539",
                        "tripEndCoord": {"lat": 0, "lon": 0},
                        "serviceTID": 20261017081707,
                    },
                ],
            }
        ],
    },
}


def _detail_response(start: str, dist: int, hard_braking: int = 0) -> dict:
    return {
        **OK,
        "resMsg": {
            "tripInfo": {
                "tripDay": start[:8],
                "dayTripCnt": 1,
                "tripDrvTime": 5,
                "tripIdleTime": 3,
                "tripDist": dist,
                "tripAvgSpeed": 14,
                "tripMaxSpeed": 45,
                "tripHardBreakingTime": hard_braking,
                "tripRapidAccelationTime": 1,
                "tripExcessiveSpeedTime": 2,
                "tripStartTime": start,
                "tripEndTime": start,
                "tripList": [{"tripTime": "201854", "lat": 0, "lon": 0}],
            }
        },
    }


# Cached status from the same car (subset that matters plus the new fields).
STATUS = {
    "airCtrlOn": False,
    "engine": False,
    "doorLock": True,
    "doorOpen": {"frontLeft": 0, "frontRight": 0, "backLeft": 0, "backRight": 0},
    "trunkOpen": False,
    "airTemp": {"value": "00H", "unit": 0, "hvacTempType": 1},
    "defrost": False,
    "lowFuelLight": False,
    "acc": False,
    "ign3": False,
    "hoodOpen": False,
    "transCond": True,
    "steerWheelHeat": 0,
    "sideBackWindowHeat": 0,
    "dte": {"value": 549, "unit": 1},
    "seatHeaterVentState": {
        "drvSeatHeatState": 2,
        "astSeatHeatState": 2,
        "rlSeatHeatState": 2,
        "rrSeatHeatState": 2,
    },
    "battery": {"batState": 1, "sjbDeliveryMode": 1, "powerAutoCutMode": 3},
    "smartKeyBatteryWarning": False,
    "fuelLevel": 69,
    "washerFluidStatus": False,
    "breakOilStatus": False,
    "engineOilStatus": False,
    "sleepModeCheck": True,
    "time": "20261017220311",
    "hazardStatus": 0,
}

LOCATION = {
    "coord": {"lat": 12.5, "lon": 77.5, "alt": 898, "type": 0},
    "head": 277,
    "speed": {"value": 23, "unit": 0},
    "time": "20261017232207",
}


def _json(payload: dict) -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = payload
    return resp


def _api(brand: int = 1) -> KiaUvoApiIN:
    return KiaUvoApiIN(region=6, brand=brand, language="en")


def _vehicle(engine_type=ENGINE_TYPES.ICE) -> Vehicle:
    vehicle = Vehicle(id="veh-1", name="SELTOS", model="SELTOS")
    vehicle.engine_type = engine_type
    vehicle.ccu_ccs2_protocol_support = 0
    return vehicle


def _token() -> Token:
    return Token(access_token="Bearer atk", device_id="dev")


def _route_posts(api: KiaUvoApiIN, detail_hard_braking: int = 0) -> MagicMock:
    def post(url, json=None, headers=None, **kwargs):
        if url.endswith("/tripinfo/detail"):
            return _json(
                _detail_response(
                    json["tripStartTime"],
                    dist=int(json["tripStartTime"][-2:]),
                    hard_braking=detail_hard_braking,
                )
            )
        if url.endswith("/tripinfo"):
            return _json(DAY_TRIP_RESPONSE)
        raise AssertionError(url)

    api.session.post = MagicMock(side_effect=post)  # type: ignore[assignment]
    return api.session.post


def _detail_calls(post: MagicMock) -> int:
    return sum(1 for c in post.call_args_list if c.args[0].endswith("/detail"))


def test_kia_ice_day_trips_use_detail_with_behaviour_counts():
    api = _api(brand=1)
    _route_posts(api, detail_hard_braking=3)
    vehicle = _vehicle()

    api.update_day_trip_info(_token(), vehicle, "20261017")

    info = vehicle.day_trip_info
    assert info.summary.distance == 22
    assert info.summary.drive_time == 79
    newest = info.trip_list[0]
    assert newest.hhmmss == "201503"
    assert newest.end_hhmmss == "202457"
    assert newest.distance == 3  # from detail, not the day summary
    assert newest.hard_braking_count == 3
    assert newest.rapid_acceleration_count == 1
    assert newest.excessive_speed_count == 2
    assert [t.hhmmss for t in info.trip_list] == ["201503", "081707"]


def test_trip_detail_is_fetched_once_per_trip():
    api = _api(brand=1)
    post = _route_posts(api)
    vehicle = _vehicle()

    api.update_day_trip_info(_token(), vehicle, "20261017")
    api.update_day_trip_info(_token(), vehicle, "20261017")

    assert _detail_calls(post) == 2  # two trips, each fetched once


def test_hyundai_ice_skips_detail_but_keeps_trip_times():
    """Previously the skipped detail left None entries, and Vehicle's
    day_trip_info setter crashed sorting them."""
    api = _api(brand=2)
    post = _route_posts(api)
    vehicle = _vehicle(ENGINE_TYPES.ICE)

    api.update_day_trip_info(_token(), vehicle, "20261017")

    assert _detail_calls(post) == 0
    trips = vehicle.day_trip_info.trip_list
    assert [(t.hhmmss, t.end_hhmmss) for t in trips] == [
        ("201503", "202457"),
        ("081707", "084539"),
    ]
    assert trips[0].distance is None


def test_status_maps_oil_warning_and_hazards():
    api = _api()
    vehicle = _vehicle()

    api._update_vehicle_properties(vehicle, dict(STATUS))
    assert vehicle.oil_level_warning_is_on is False
    assert vehicle.hazard_lights_on is False

    api._update_vehicle_properties(
        vehicle, {**STATUS, "engineOilStatus": True, "hazardStatus": 1}
    )
    assert vehicle.oil_level_warning_is_on is True
    assert vehicle.hazard_lights_on is True


def test_status_without_new_fields_leaves_them_unset():
    api = _api()
    vehicle = _vehicle()
    status = {
        k: v for k, v in STATUS.items() if k not in ("engineOilStatus", "hazardStatus")
    }

    api._update_vehicle_properties(vehicle, status)

    assert vehicle.oil_level_warning_is_on is None
    assert vehicle.hazard_lights_on is None


def test_location_sets_speed_heading_altitude():
    api = _api()
    vehicle = _vehicle()

    api._update_vehicle_location(vehicle, LOCATION)

    assert vehicle.location_latitude == 12.5
    assert vehicle.location_speed == 23
    assert vehicle.location_heading == 277
    assert vehicle.location_altitude == 898


def test_location_speed_ignored_for_unknown_unit():
    api = _api()
    vehicle = _vehicle()

    api._update_vehicle_location(
        vehicle, {**LOCATION, "speed": {"value": 14, "unit": 1}}
    )

    assert vehicle.location_speed is None
    assert vehicle.location_heading == 277


def test_forced_refresh_updates_location():
    api = _api()
    vehicle = _vehicle()
    api._get_forced_vehicle_state = MagicMock(return_value=dict(STATUS))  # type: ignore[assignment]
    api._get_location = MagicMock(return_value=LOCATION)  # type: ignore[assignment]

    api.force_refresh_vehicle_state(_token(), vehicle)

    assert vehicle.location_latitude == 12.5
    assert vehicle.location_speed == 23


AIR_CLEANING = {
    "airPurifierStatus": 1,
    "fineDustLevel": [],
    "dateTime": "20261017215820",
    "fineDustColor": 2,
    "fineDustLevel2": [24, 21, 21, 21],
}


def test_cabin_pm25_from_air_cleaning():
    api = _api()
    vehicle = _vehicle()

    api._update_vehicle_properties(vehicle, {**STATUS, "airCleaning": AIR_CLEANING})

    assert vehicle.cabin_pm25 == 24  # latest reading
    assert vehicle.cabin_air_quality_level == 2  # green band, 16-35
    assert vehicle.cabin_air_quality_measured_at.isoformat() == (
        "2026-10-17T21:58:20+05:30"
    )


def test_cabin_pm25_cleared_when_no_readings():
    api = _api()
    vehicle = _vehicle()
    api._update_vehicle_properties(vehicle, {**STATUS, "airCleaning": AIR_CLEANING})

    api._update_vehicle_properties(
        vehicle, {**STATUS, "airCleaning": {**AIR_CLEANING, "fineDustLevel2": []}}
    )

    assert vehicle.cabin_pm25 is None
    assert vehicle.cabin_air_quality_level is None
    assert vehicle.cabin_air_quality_measured_at is None


VEHICLE_ENTRY = {
    "vin": "VIN",
    "vehicleId": "veh-1",
    "vehicleName": "SELTOS",
    "type": "GN",
    "nickname": "SELTOS",
    "year": "2025",
    "regDate": "2025-09-01 09:19:43.418",
    "detailInfo": {
        "inColor": "WK",
        "outColor": "EWE",
        "carPackageType": "GL",
        "saleCarmdlEnNm": "SELTOS",
        "prodCarmdlCd": "TI",
        "bodyType": "2",
        "saleCarmdlCd": "TI",
    },
    "protocolType": 0,
    "ccuCCS2ProtocolSupport": 0,
}


def test_vehicle_list_reads_year_trim_and_colours():
    api = _api()
    api.session.get = MagicMock(  # type: ignore[assignment]
        return_value=_json({**OK, "resMsg": {"vehicles": [VEHICLE_ENTRY]}})
    )

    (vehicle,) = api.get_vehicles(_token())

    assert vehicle.year == 2025
    assert vehicle.trim_code == "GL"
    assert vehicle.exterior_color_code == "EWE"
    assert vehicle.interior_color_code == "WK"


def test_vehicle_list_tolerates_missing_details():
    api = _api()
    entry = {k: v for k, v in VEHICLE_ENTRY.items() if k not in ("year", "detailInfo")}
    api.session.get = MagicMock(  # type: ignore[assignment]
        return_value=_json({**OK, "resMsg": {"vehicles": [entry]}})
    )

    (vehicle,) = api.get_vehicles(_token())

    assert vehicle.year is None
    assert vehicle.trim_code is None
    assert vehicle.exterior_color_code is None


MAINTENANCE = {
    **OK,
    "resMsg": {
        "odometer": 11192,
        "mainList": [
            {
                "itemId": "Engine oil",
                "enable": True,
                "items": {"distValue": 10000, "termValue": 12},
            },
            {
                "itemId": "Oil Filter",
                "enable": True,
                "items": {"distValue": 10000, "termValue": 12},
            },
            {
                "itemId": "Cooling water",
                "enable": True,
                "items": {"distValue": 210000, "termValue": 0},
            },
            {
                "itemId": "Ignition plug",
                "enable": True,
                "items": {"distValue": 60000, "termValue": 0},
            },
        ],
    },
}


def test_maintenance_alerts_parsed_with_intervals():
    api = _api()
    vehicle = _vehicle()
    api.session.get = MagicMock(return_value=_json(MAINTENANCE))  # type: ignore[assignment]

    state = api._get_maintenance_alert(_token(), vehicle)
    api._update_vehicle_maintenance_alert(vehicle, state)

    assert vehicle.odometer == 11192
    items = {a.name: a for a in vehicle.maintenance_alerts}
    assert items["Engine oil"].interval_km == 10000
    assert items["Engine oil"].interval_months == 12
    # termValue 0 means "no time interval" -> None, not 0.
    assert items["Cooling water"].interval_months is None
    assert items["Cooling water"].interval_km == 210000
    assert items["Ignition plug"].interval_months is None


def test_maintenance_alerts_none_when_no_list():
    api = _api()
    vehicle = _vehicle()
    api._update_vehicle_maintenance_alert(vehicle, {"odometer": 100})
    assert vehicle.maintenance_alerts is None
