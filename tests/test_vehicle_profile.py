"""Tests for VehicleProfile dataclass and capability properties."""

import json
import pathlib

import pytest

from hyundai_kia_connect_api.KiaUvoApiEU import KiaUvoApiEU
from hyundai_kia_connect_api.Vehicle import Vehicle, VehicleProfile
from zoneinfo import ZoneInfo

FIXTURES_DIR = pathlib.Path(__file__).parent / "fixtures"


class TestVehicleProfile:
    def test_vehicle_profile_is_dataclass(self):
        p = VehicleProfile()
        assert p.brand is None
        assert p.country is None
        assert p.head_unit_version is None

    def test_vehicle_profile_populated(self):
        p = VehicleProfile(
            brand="H",
            country="pl",
            head_unit_version="MX5.EUR.ccNC.001.002.250601",
            heating_steering_wheel="1",
            sunroof_option="1",
            dtc_categories=[{"ecuIdx": "ABS", "factoryValue": 1}],
        )
        assert p.brand == "H"
        assert p.country == "pl"
        assert p.head_unit_version == "MX5.EUR.ccNC.001.002.250601"
        assert p.heating_steering_wheel == "1"
        assert p.sunroof_option == "1"
        assert len(p.dtc_categories) == 1

    def test_vehicle_with_profile(self):
        v = Vehicle()
        assert v.profile is None

        v.profile = VehicleProfile(brand="H", country="pl")
        assert v.profile.brand == "H"


class TestCapabilityProperties:
    def test_capability_properties_none_when_no_profile(self):
        v = Vehicle()
        assert v.steering_wheel_heater_supported is None
        assert v.side_mirror_heater_supported is None
        assert v.rear_window_heater_supported is None
        assert v.sunroof_supported is None
        assert v.digital_key_supported is None
        assert v.air_purifier_supported is None
        assert v.remote_heat_control_supported is None
        assert v.ignition_control_supported is None
        assert v.horn_light_supported is None
        assert v.light_only_supported is None
        assert v.ev_alarm_supported is None
        assert v.front_window_heating_supported is None
        assert v.is_left_hand_drive is None

    def test_steering_wheel_heater_supported_true(self):
        v = Vehicle()
        v.profile = VehicleProfile(heating_steering_wheel="1")
        assert v.steering_wheel_heater_supported is True

    def test_steering_wheel_heater_supported_false(self):
        v = Vehicle()
        v.profile = VehicleProfile(heating_steering_wheel="0")
        assert v.steering_wheel_heater_supported is False

    def test_sunroof_supported_true(self):
        v = Vehicle()
        v.profile = VehicleProfile(sunroof_option="1")
        assert v.sunroof_supported is True

    def test_digital_key_supported_nonzero(self):
        v = Vehicle()
        v.profile = VehicleProfile(digital_key2="3")
        assert v.digital_key_supported is True

    def test_digital_key_supported_zero(self):
        v = Vehicle()
        v.profile = VehicleProfile(digital_key2="0")
        assert v.digital_key_supported is False

    def test_is_left_hand_drive(self):
        v = Vehicle()
        v.profile = VehicleProfile(driver_seat_location="L")
        assert v.is_left_hand_drive is True

    def test_is_not_left_hand_drive(self):
        v = Vehicle()
        v.profile = VehicleProfile(driver_seat_location="R")
        assert v.is_left_hand_drive is False

    def test_ev_alarm_supported_nonzero(self):
        v = Vehicle()
        v.profile = VehicleProfile(ev_alarm_option_info="1")
        assert v.ev_alarm_supported is True

    def test_ev_alarm_supported_zero(self):
        v = Vehicle()
        v.profile = VehicleProfile(ev_alarm_option_info="0")
        assert v.ev_alarm_supported is False


class TestMapVehicleProfile:
    @pytest.fixture
    def eu_api(self):
        api = KiaUvoApiEU.__new__(KiaUvoApiEU)
        api.data_timezone = ZoneInfo("Europe/Berlin")
        api.temperature_range = KiaUvoApiEU.temperature_range
        return api

    def _load_vin_info(self):
        fixture_path = FIXTURES_DIR / "eu_profile_kia_ev6.json"
        data = json.loads(fixture_path.read_text())
        return data["resMsg"]["vinInfo"][0]

    def test_map_profile_basic_fields(self, eu_api):
        vin_info = self._load_vin_info()
        profile = eu_api._map_vehicle_profile(vin_info)

        assert profile.brand == "K"
        assert profile.country == "de"
        assert profile.ota_update_supported is True
        assert profile.remote_ota_update_supported is False

    def test_map_profile_device_fields(self, eu_api):
        vin_info = self._load_vin_info()
        profile = eu_api._map_vehicle_profile(vin_info)

        assert profile.sim_status == "A"
        assert profile.sim_start_date == "15012023"
        assert profile.sim_end_date == "15012033"
        assert profile.head_unit_type == "04"
        assert profile.platform == "ccNC"
        assert profile.navi_applied is True

    def test_map_profile_option_fields(self, eu_api):
        vin_info = self._load_vin_info()
        profile = eu_api._map_vehicle_profile(vin_info)

        assert profile.heating_steering_wheel == "1"
        assert profile.heating_side_mirror == "1"
        assert profile.sunroof_option == "1"
        assert profile.digital_key2 == "3"
        assert profile.air_purifier_option == "0"
        assert profile.seat_heater_vent_front_left == 6
        assert profile.seat_heater_vent_rear_left == 2

    def test_map_profile_service_fields(self, eu_api):
        vin_info = self._load_vin_info()
        profile = eu_api._map_vehicle_profile(vin_info)

        assert profile.battery_warning_service is True
        assert profile.send2car_option_info == 10
        assert profile.media_streaming_service == [4, 6]

    def test_map_profile_detail_fields(self, eu_api):
        vin_info = self._load_vin_info()
        profile = eu_api._map_vehicle_profile(vin_info)

        assert profile.body_type == "3"
        assert profile.exterior_color == "A2B"

    def test_map_profile_dtc_categories(self, eu_api):
        vin_info = self._load_vin_info()
        profile = eu_api._map_vehicle_profile(vin_info)

        assert len(profile.dtc_categories) == 5
        assert profile.dtc_categories[0]["ecuIdx"] == "ABS"

    def test_capability_properties_work_with_mapped_profile(self, eu_api):
        vin_info = self._load_vin_info()
        profile = eu_api._map_vehicle_profile(vin_info)
        v = Vehicle()
        v.profile = profile

        assert v.steering_wheel_heater_supported is True
        assert v.side_mirror_heater_supported is True
        assert v.sunroof_supported is True
        assert v.digital_key_supported is True
        assert v.air_purifier_supported is False
        assert v.is_left_hand_drive is True
        assert v.ev_alarm_supported is False


class TestGetVehiclesProfileFetch:
    """get_vehicles fetches profiles only when supports_vehicle_profile is set."""

    def _make_api(self, supported: bool):
        from hyundai_kia_connect_api.ApiImplType1 import ApiImplType1
        from unittest.mock import MagicMock

        api = object.__new__(ApiImplType1)
        api.supports_vehicle_profile = supported
        api.SPA_API_URL = "https://example.test/api/v1/spa/"
        # get_vehicles uses self.session.get (ApiImplSession, PR #1160); the
        # base __init__ is bypassed via object.__new__, so wire a mock session.
        resp = MagicMock()
        resp.json.return_value = {"resMsg": {"vehicles": []}}
        api.session = MagicMock()
        api.session.get.return_value = resp
        return api

    def test_get_vehicles_fetches_profiles_when_supported(self):
        import hyundai_kia_connect_api.ApiImplType1 as type1_mod
        from hyundai_kia_connect_api.ApiImplType1 import ApiImplType1
        from unittest.mock import patch, MagicMock

        api = self._make_api(supported=True)
        with (
            patch.object(ApiImplType1, "_get_authenticated_headers", return_value={}),
            patch.object(type1_mod, "_check_response_for_errors", return_value=None),
            patch.object(ApiImplType1, "_fetch_vehicle_profiles") as fetch,
        ):
            result = ApiImplType1.get_vehicles(api, token=MagicMock())
            assert result == []
            fetch.assert_called_once()

    def test_get_vehicles_skips_profiles_when_not_supported(self):
        import hyundai_kia_connect_api.ApiImplType1 as type1_mod
        from hyundai_kia_connect_api.ApiImplType1 import ApiImplType1
        from unittest.mock import patch, MagicMock

        api = self._make_api(supported=False)
        with (
            patch.object(ApiImplType1, "_get_authenticated_headers", return_value={}),
            patch.object(type1_mod, "_check_response_for_errors", return_value=None),
            patch.object(ApiImplType1, "_fetch_vehicle_profiles") as fetch,
        ):
            ApiImplType1.get_vehicles(api, token=MagicMock())
            fetch.assert_not_called()
