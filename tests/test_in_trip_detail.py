"""KiaUvoApiIN trip detail for Kia vehicles and its driving-behaviour counters.

Payload shapes from a live Kia India account, identifiers removed.
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

    assert _detail_calls(post) == 2
