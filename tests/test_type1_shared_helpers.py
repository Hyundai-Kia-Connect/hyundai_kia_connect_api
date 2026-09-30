"""Tests for helpers consolidated into ApiImplType1 from its region children."""

from unittest.mock import MagicMock

from hyundai_kia_connect_api.ApiImplType1 import ApiImplType1
from hyundai_kia_connect_api.KiaUvoApiAU import KiaUvoApiAU
from hyundai_kia_connect_api.KiaUvoApiCN import KiaUvoApiCN
from hyundai_kia_connect_api.KiaUvoApiEU import KiaUvoApiEU
from hyundai_kia_connect_api.KiaUvoApiIN import KiaUvoApiIN
from hyundai_kia_connect_api.Token import Token
from hyundai_kia_connect_api.Vehicle import TripInfo, Vehicle

SHARED = (
    "_get_charge_limits",
    "_get_trip_info",
    "update_month_trip_info",
    "update_day_trip_info",
    "_get_driving_info",
    "_update_vehicle_drive_info",
)


def _token() -> Token:
    return Token(access_token="Bearer abc", device_id="dev-1")


def _vehicle() -> Vehicle:
    return Vehicle(id="veh-1", ccu_ccs2_protocol_support=1)


def test_children_inherit_shared_helpers():
    for cls in (KiaUvoApiAU, KiaUvoApiCN, KiaUvoApiEU, KiaUvoApiIN):
        for name in SHARED:
            if cls is KiaUvoApiIN and name == "update_month_trip_info":
                continue  # IN keeps a more lenient month parser
            assert getattr(cls, name) is getattr(ApiImplType1, name), (cls, name)


def test_vehicle_headers_carry_ccs2_support_by_default():
    api = KiaUvoApiEU(region=1, brand=1, language="en")
    headers = api._get_vehicle_headers(_token(), _vehicle())
    assert headers["Ccuccs2protocolsupport"] == "1"


def test_au_vehicle_headers_omit_ccs2_support():
    api = KiaUvoApiAU(region=5, brand=2, language="en")
    headers = api._get_vehicle_headers(_token(), _vehicle())
    assert headers["Ccuccs2protocolsupport"] == "0"


def test_cn_vehicle_headers_omit_ccs2_support():
    api = KiaUvoApiCN(region=4, brand=1, language="en")
    headers = api._get_vehicle_headers(_token(), _vehicle())
    assert "Ccuccs2protocolsupport" not in headers


def test_au_stamp_uses_shared_cfb():
    api = KiaUvoApiAU(region=5, brand=2, language="en")
    assert api._get_stamp()


def _driving_payloads(all_time_key: str):
    all_time = {"resMsg": {all_time_key: [{"totalPwrCsp": 100, "regenPwr": 20}]}}
    month = {
        "resMsg": {
            "drivingInfoDetail": [],
            "drivingInfo": [
                {"drivingPeriod": 0, "totalPwrCsp": 500, "calculativeOdo": 50}
            ],
        }
    }
    return all_time, month


def _mock_posts(api, *payloads):
    api.session = MagicMock()
    api.session.post.side_effect = [
        MagicMock(json=MagicMock(return_value=p)) for p in payloads
    ]


def test_driving_info_all_time_key_per_region():
    for api, key in (
        (KiaUvoApiEU(region=1, brand=1, language="en"), "drivingInfo"),
        (KiaUvoApiAU(region=5, brand=2, language="en"), "drivingInfoDetail"),
    ):
        _mock_posts(api, *_driving_payloads(key))
        info = api._get_driving_info(_token(), _vehicle())
        assert info["totalPwrCsp"] == 100
        assert info["consumption30d"] == 10


def _day_trip_payload(trip: dict) -> dict:
    return {
        "resMsg": {
            "dayTripList": [
                {
                    "tripDrvTime": 10,
                    "tripIdleTime": 1,
                    "tripDist": 5,
                    "tripAvgSpeed": 30,
                    "tripMaxSpeed": 60,
                    "tripList": [trip],
                }
            ]
        }
    }


FULL_TRIP = {
    "tripTime": "101010",
    "tripDrvTime": 10,
    "tripIdleTime": 1,
    "tripDist": 5,
    "tripAvgSpeed": 30,
    "tripMaxSpeed": 60,
}


def test_day_trip_uses_trip_fields_by_default():
    api = KiaUvoApiEU(region=1, brand=1, language="en")
    _mock_posts(api, _day_trip_payload(FULL_TRIP))
    vehicle = _vehicle()
    api.update_day_trip_info(_token(), vehicle, "20260930")
    assert vehicle.day_trip_info.trip_list[0].hhmmss == "101010"


def test_in_day_trip_falls_back_to_detailed_trip_info():
    api = KiaUvoApiIN(brand=2)
    _mock_posts(api, _day_trip_payload({"tripId": "t1"}))
    sentinel = TripInfo(hhmmss="121212")
    api._get_detailed_trip_info = MagicMock(return_value=sentinel)
    vehicle = _vehicle()
    api.update_day_trip_info(_token(), vehicle, "20260930")
    assert vehicle.day_trip_info.trip_list == [sentinel]
