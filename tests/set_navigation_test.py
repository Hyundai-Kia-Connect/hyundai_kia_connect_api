"""Tests for set_navigation method in ApiImplType1 and POIInfo dataclass."""

import datetime as dt
from unittest.mock import MagicMock, patch

from hyundai_kia_connect_api.ApiImpl import POICoord, POIInfo
from hyundai_kia_connect_api.ApiImplType1 import ApiImplType1
from hyundai_kia_connect_api.Token import Token
from hyundai_kia_connect_api.Vehicle import Vehicle


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeResponse:
    """Minimal fake for requests.Response."""

    def __init__(self, json_data=None, status_code=200):
        self.json_data = json_data
        self.status_code = status_code

    def json(self):
        return self.json_data


def _make_vehicle():
    v = Vehicle(
        id="test-vehicle-id",
        name="Ioniq 5",
        model="IONIQ 5",
        key="test-key",
        timezone=dt.timezone(dt.timedelta(hours=1)),
    )
    v.ccu_ccs2_protocol_support = 1
    return v


def _make_token():
    t = MagicMock(spec=Token)
    t.device_id = "test-device-id"
    t.access_token = "Bearer test-access"
    return t


def _make_api():
    """Create an ApiImplType1 without calling __init__."""
    api = object.__new__(ApiImplType1)
    api.SPA_API_URL_V2 = "https://prd.eu-ccapi.kia.com:8080/api/v2/spa/"
    api._get_control_headers = MagicMock(return_value={})
    api._get_device_id = MagicMock(return_value="new-device-id")
    api._get_stamp = MagicMock(return_value="test-stamp")
    return api


# ---------------------------------------------------------------------------
# POIInfo / POICoord dataclass tests
# ---------------------------------------------------------------------------


class TestPOICoord:
    def test_defaults(self):
        c = POICoord()
        assert c.lat is None
        assert c.lon is None
        assert c.alt == 0
        assert c.type == 0

    def test_custom_values(self):
        c = POICoord(lat=52.52, lon=13.405, alt=34, type=1)
        assert c.lat == 52.52
        assert c.lon == 13.405
        assert c.alt == 34
        assert c.type == 1


class TestPOIInfo:
    def test_defaults(self):
        p = POIInfo()
        assert p.phone == ""
        assert p.waypoint_id == 1
        assert p.lang == 1
        assert p.src == "HERE"
        assert p.coord is None
        assert p.addr == ""
        assert p.zip == ""
        assert p.place_id == ""
        assert p.name == ""

    def test_to_dict_camelCase_keys(self):
        p = POIInfo(
            phone="+491234",
            waypoint_id=2,
            coord=POICoord(lat=52.52, lon=13.405, alt=34),
            addr="Berlin, Germany",
            zip="10115",
            place_id="here:af:street:example",
            name="Berlin Central Station",
        )
        d = p.to_dict()

        assert d["phone"] == "+491234"
        assert d["waypointID"] == 2
        assert d["lang"] == 1
        assert d["src"] == "HERE"
        assert d["coord"]["lat"] == 52.52
        assert d["coord"]["lon"] == 13.405
        assert d["coord"]["alt"] == 34
        assert d["coord"]["type"] == 0
        assert d["addr"] == "Berlin, Germany"
        assert d["zip"] == "10115"
        assert d["placeid"] == "here:af:street:example"
        assert d["name"] == "Berlin Central Station"

    def test_to_dict_no_snake_case_keys(self):
        """Ensure to_dict never leaks Python snake_case keys."""
        p = POIInfo(coord=POICoord(lat=1.0, lon=2.0))
        d = p.to_dict()

        assert "waypoint_id" not in d
        assert "place_id" not in d
        assert "waypointID" in d
        assert "placeid" in d

    def test_to_dict_fixed_values(self):
        """lang=1 and src='HERE' are always sent per bluelinky spec."""
        p = POIInfo(coord=POICoord(lat=0, lon=0))
        d = p.to_dict()
        assert d["lang"] == 1
        assert d["src"] == "HERE"


# ---------------------------------------------------------------------------
# set_navigation API method tests
# ---------------------------------------------------------------------------


class TestSetNavigation:
    def test_posts_to_correct_url(self):
        api = _make_api()
        vehicle = _make_vehicle()
        token = _make_token()
        poi = POIInfo(coord=POICoord(lat=52.52, lon=13.405), name="Berlin")

        mock_response = _FakeResponse(json_data={"retCode": "S", "msgId": "nav-msg-1"})
        with patch("hyundai_kia_connect_api.ApiImplType1.requests.post") as mock_post:
            mock_post.return_value = mock_response
            api.set_navigation(token, vehicle, [poi])

        call_url = mock_post.call_args[0][0]
        assert "vehicles/test-vehicle-id/location/routes" in call_url

    def test_sends_deviceID_in_body(self):
        api = _make_api()
        vehicle = _make_vehicle()
        token = _make_token()
        poi = POIInfo(coord=POICoord(lat=52.52, lon=13.405), name="Berlin")

        mock_response = _FakeResponse(json_data={"retCode": "S", "msgId": "nav-msg-1"})
        with patch("hyundai_kia_connect_api.ApiImplType1.requests.post") as mock_post:
            mock_post.return_value = mock_response
            api.set_navigation(token, vehicle, [poi])

        call_payload = mock_post.call_args[1]["json"]
        assert call_payload["deviceID"] == "test-device-id"

    def test_sends_poiInfoList_in_body(self):
        api = _make_api()
        vehicle = _make_vehicle()
        token = _make_token()
        poi = POIInfo(coord=POICoord(lat=52.52, lon=13.405), name="Berlin")

        mock_response = _FakeResponse(json_data={"retCode": "S", "msgId": "nav-msg-1"})
        with patch("hyundai_kia_connect_api.ApiImplType1.requests.post") as mock_post:
            mock_post.return_value = mock_response
            api.set_navigation(token, vehicle, [poi])

        call_payload = mock_post.call_args[1]["json"]
        assert len(call_payload["poiInfoList"]) == 1
        assert call_payload["poiInfoList"][0]["name"] == "Berlin"

    def test_returns_msgId(self):
        api = _make_api()
        vehicle = _make_vehicle()
        token = _make_token()
        poi = POIInfo(coord=POICoord(lat=52.52, lon=13.405))

        mock_response = _FakeResponse(json_data={"retCode": "S", "msgId": "nav-msg-42"})
        with patch("hyundai_kia_connect_api.ApiImplType1.requests.post") as mock_post:
            mock_post.return_value = mock_response
            result = api.set_navigation(token, vehicle, [poi])

        assert result == "nav-msg-42"

    def test_multiple_pois(self):
        api = _make_api()
        vehicle = _make_vehicle()
        token = _make_token()
        pois = [
            POIInfo(
                waypoint_id=1, coord=POICoord(lat=52.52, lon=13.405), name="Berlin"
            ),
            POIInfo(waypoint_id=2, coord=POICoord(lat=48.85, lon=2.35), name="Paris"),
        ]

        mock_response = _FakeResponse(
            json_data={"retCode": "S", "msgId": "nav-msg-multi"}
        )
        with patch("hyundai_kia_connect_api.ApiImplType1.requests.post") as mock_post:
            mock_post.return_value = mock_response
            api.set_navigation(token, vehicle, pois)

        call_payload = mock_post.call_args[1]["json"]
        assert len(call_payload["poiInfoList"]) == 2
        assert call_payload["poiInfoList"][0]["waypointID"] == 1
        assert call_payload["poiInfoList"][1]["waypointID"] == 2

    def test_uses_control_headers(self):
        api = _make_api()
        vehicle = _make_vehicle()
        token = _make_token()
        poi = POIInfo(coord=POICoord(lat=52.52, lon=13.405))

        mock_response = _FakeResponse(json_data={"retCode": "S", "msgId": "nav-msg-1"})
        with patch("hyundai_kia_connect_api.ApiImplType1.requests.post") as mock_post:
            mock_post.return_value = mock_response
            api.set_navigation(token, vehicle, [poi])

        api._get_control_headers.assert_called_once_with(token, vehicle)

    def test_regenerates_device_id_after_call(self):
        api = _make_api()
        vehicle = _make_vehicle()
        token = _make_token()
        poi = POIInfo(coord=POICoord(lat=52.52, lon=13.405))

        mock_response = _FakeResponse(json_data={"retCode": "S", "msgId": "nav-msg-1"})
        with patch("hyundai_kia_connect_api.ApiImplType1.requests.post") as mock_post:
            mock_post.return_value = mock_response
            api.set_navigation(token, vehicle, [poi])

        api._get_device_id.assert_called_once()


# ---------------------------------------------------------------------------
# NotImplementedError for unsupported regions
# ---------------------------------------------------------------------------


class TestSetNavigationNotImplemented:
    def test_base_api_impl_raises(self):
        from hyundai_kia_connect_api.ApiImpl import ApiImpl

        api = ApiImpl()
        token = MagicMock(spec=Token)
        vehicle = _make_vehicle()
        poi = POIInfo(coord=POICoord(lat=0, lon=0))

        try:
            api.set_navigation(token, vehicle, [poi])
            assert False, "Should have raised NotImplementedError"
        except NotImplementedError as e:
            assert "set_navigation" in str(e)
