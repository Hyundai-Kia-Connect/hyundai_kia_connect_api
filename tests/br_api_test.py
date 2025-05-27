"""Tests for the Brazilian implementation of the Hyundai BlueLink API."""

import datetime
import pytest
import pytz

from hyundai_kia_connect_api.HyundaiBlueLinkApiBR import HyundaiBlueLinkApiBR
from hyundai_kia_connect_api.Token import Token
from hyundai_kia_connect_api.Vehicle import Vehicle, VehicleLocation
from hyundai_kia_connect_api.const import (
    Brand,
    EngineType,
    DISTANCE_UNITS,
    Region,
)
from hyundai_kia_connect_api.exceptions import APIError, AuthenticationError


@pytest.fixture
def api():
    """Create an instance of the Brazilian API implementation."""
    return HyundaiBlueLinkApiBR(region=Region.BRAZIL, brand=Brand.HYUNDAI)


@pytest.fixture
def token():
    """Create a mock Token."""
    return Token(
        access_token="mock_access_token",
        refresh_token="mock_refresh_token",
        valid_until=datetime.datetime.now(pytz.utc) + datetime.timedelta(hours=1),
        username="test@example.com",
        password="password",
        pin=None,
    )


@pytest.fixture
def vehicle():
    """Create a mock Vehicle."""
    return Vehicle(
        id="mock_vehicle_id",
        name="Test Vehicle",
        model="Test Model",
        registration_date="2023-01-01",
        VIN="TESTVEHICLEVIN12345",
        timezone=pytz.utc,
        engine_type=EngineType.ICE,
        generation="GEN2",
        ccu_ccs2_protocol_support=0,
    )


@pytest.mark.br
class TestHyundaiBlueLinkApiBR:
    """Tests for the Brazilian implementation of the Hyundai BlueLink API."""

    def test_init(self):
        """Test the initialization of the API client."""
        api = HyundaiBlueLinkApiBR(region=Region.BRAZIL, brand=Brand.HYUNDAI)
        assert api.base_url == "br-ccapi.hyundai.com.br"
        assert api.api_url == "https://br-ccapi.hyundai.com.br/api/v1/"

        # Test initialization with an invalid brand
        with pytest.raises(APIError):
            HyundaiBlueLinkApiBR(region=Region.BRAZIL, brand=Brand.KIA)  # not supported

    def test_login(self, api, mocker):
        """Test the login flow."""
        # Mock the get cookies request
        mock_cookie_response = mocker.MagicMock()
        mock_cookie_response.raise_for_status.return_value = None
        mock_cookie_response.cookies.get_dict.return_value = {"cookie1": "value1"}

        # Mock the authorization code request
        mock_auth_code_response = mocker.MagicMock()
        mock_auth_code_response.raise_for_status.return_value = None
        mock_auth_code_response.json.return_value = {
            "redirectUrl": "https://example.com/?code=mock_authorization_code"
        }

        # Mock the token request
        mock_token_response = mocker.MagicMock()
        mock_token_response.raise_for_status.return_value = None
        mock_token_response.json.return_value = {
            "access_token": "mock_access_token",
            "refresh_token": "mock_refresh_token",
            "expires_in": 3600,
        }

        # Apply the mocks
        mocker.patch("requests.get", return_value=mock_cookie_response)
        mocker.patch.object(
            api.session,
            "post",
            side_effect=[mock_auth_code_response, mock_token_response],
        )

        # Call the login method
        token = api.login("test@example.com", "password")

        # Verify the token
        assert token.access_token == "mock_access_token"
        assert token.refresh_token == "mock_refresh_token"
        assert token.username == "test@example.com"
        assert token.password == "password"
        assert token.pin is None

    def test_login_failed_cookies(self, api, mocker):
        """Test login failure when cookies request fails."""
        # Mock the get cookies request to fail
        mock_response = mocker.MagicMock()
        mock_response.raise_for_status.side_effect = Exception("Failed to get cookies")
        mocker.patch("requests.get", return_value=mock_response)

        # Call the login method and expect an exception
        with pytest.raises(AuthenticationError):
            api.login("test@example.com", "password")

    def test_login_failed_auth_code(self, api, mocker):
        """Test login failure when authorization code request fails."""
        # Mock the get cookies request
        mock_cookie_response = mocker.MagicMock()
        mock_cookie_response.raise_for_status.return_value = None
        mock_cookie_response.cookies.get_dict.return_value = {"cookie1": "value1"}

        # Mock the authorization code request to fail
        mock_auth_code_response = mocker.MagicMock()
        mock_auth_code_response.raise_for_status.side_effect = Exception(
            "Failed to get auth code"
        )
        mock_auth_code_response.text = "Error"

        # Apply the mocks
        mocker.patch("requests.get", return_value=mock_cookie_response)
        mocker.patch.object(api.session, "post", return_value=mock_auth_code_response)

        # Call the login method and expect an exception
        with pytest.raises(AuthenticationError):
            api.login("test@example.com", "password")

    def test_get_vehicles(self, api, token, mocker):
        """Test retrieving the list of vehicles."""
        # Mock the API response
        mock_response = mocker.MagicMock()
        mock_response.json.return_value = {
            "resMsg": {
                "vehicles": [
                    {
                        "vehicleId": "vehicle1",
                        "nickname": "My Hyundai",
                        "vehicleName": "Hyundai HB20",
                        "regDate": "2022-01-01",
                        "vin": "VIN123456789",
                        "type": "GN",  # ICE
                        "ccuCCS2ProtocolSupport": 0,
                    },
                    {
                        "vehicleId": "vehicle2",
                        "nickname": "My EV",
                        "vehicleName": "Hyundai Kona Electric",
                        "regDate": "2023-01-01",
                        "vin": "VIN987654321",
                        "type": "EV",  # Electric Vehicle
                        "ccuCCS2ProtocolSupport": 1,
                    },
                ]
            }
        }
        mocker.patch.object(api.session, "get", return_value=mock_response)

        # Call the get_vehicles method
        vehicles = api.get_vehicles(token)

        # Verify the vehicles
        assert len(vehicles) == 2

        # Check first vehicle
        assert vehicles[0].id == "vehicle1"
        assert vehicles[0].name == "My Hyundai"
        assert vehicles[0].model == "Hyundai HB20"
        assert vehicles[0].registration_date == "2022-01-01"
        assert vehicles[0].VIN == "VIN123456789"
        assert vehicles[0].engine_type == EngineType.ICE
        assert vehicles[0].ccu_ccs2_protocol_support == 0

        # Check second vehicle
        assert vehicles[1].id == "vehicle2"
        assert vehicles[1].name == "My EV"
        assert vehicles[1].model == "Hyundai Kona Electric"
        assert vehicles[1].registration_date == "2023-01-01"
        assert vehicles[1].VIN == "VIN987654321"
        assert vehicles[1].engine_type == EngineType.EV
        assert vehicles[1].ccu_ccs2_protocol_support == 1

    def test_update_vehicle_with_cached_state(self, api, token, vehicle, mocker):
        """Test updating a vehicle with cached state."""
        # Mock the vehicle details response
        mock_details_response = mocker.MagicMock()
        mock_details_response.json.return_value = {
            "resMsg": {"vehicleDetails": "mock_details"}
        }

        # Mock the vehicle state response
        mock_state_response = mocker.MagicMock()
        mock_state_response.json.return_value = {
            "resMsg": {
                "time": "2023-06-01T12:00:00Z",
                "engine": False,
                "doorLock": True,
                "dte": {
                    "value": 500,
                    "unit": 1,  # km
                },
                "fuelLevel": 75,
                "lowFuelLight": False,
                "defrost": False,
                "steerWheelHeat": False,
                "washerFluidStatus": False,
                "seatHeaterVentState": {
                    "flSeatHeatState": 0,
                    "frSeatHeatState": 0,
                    "rlSeatHeatState": 0,
                    "rrSeatHeatState": 0,
                },
                "tirePressureLamp": {
                    "tirePressureWarningLampFrontLeft": 0,
                    "tirePressureWarningLampFrontRight": 0,
                    "tirePressureWarningLampRearLeft": 0,
                    "tirePressureWarningLampRearRight": 0,
                    "tirePressureWarningLampAll": 0,
                },
                "windowOpen": {
                    "frontLeft": False,
                    "frontRight": False,
                    "backLeft": False,
                    "backRight": False,
                },
                "doorOpen": {
                    "frontLeft": False,
                    "frontRight": False,
                    "backLeft": False,
                    "backRight": False,
                },
                "hoodOpen": False,
                "trunkOpen": False,
            }
        }

        # Mock the vehicle location response
        mock_location_response = mocker.MagicMock()
        mock_location_response.json.return_value = {
            "resMsg": {
                "coord": {"lat": -23.5505, "lng": -46.6333},
                "time": "2023-06-01T12:00:00Z",
            }
        }

        # Setup mock responses
        mocker.patch.object(
            api.session,
            "get",
            side_effect=[
                mock_details_response,
                mock_state_response,
                mock_location_response,
            ],
        )

        # Call the update_vehicle_with_cached_state method
        api.update_vehicle_with_cached_state(token, vehicle)

        # Verify vehicle properties were updated
        assert vehicle.is_locked is True
        assert vehicle.engine_is_running is False
        assert vehicle.fuel_level == 75
        assert vehicle.fuel_level_is_low is False
        assert vehicle.fuel_driving_range == (500, DISTANCE_UNITS[1])  # 500 km
        assert vehicle.location[0] == -23.5505  # latitude
        assert vehicle.location[1] == -46.6333  # longitude
        assert isinstance(vehicle.location[2], datetime.datetime)  # timestamp

    def test_get_vehicle_location(self, api, token, vehicle, mocker):
        """Test retrieving the vehicle location."""
        # Mock the API response
        mock_response = mocker.MagicMock()
        mock_response.json.return_value = {
            "resMsg": {
                "coord": {"lat": -23.5505, "lng": -46.6333},
                "time": "2023-06-01T12:00:00Z",
            }
        }
        mocker.patch.object(api.session, "get", return_value=mock_response)

        # Call the _get_vehicle_location method
        location = api._get_vehicle_location(token, vehicle)

        # Verify the location
        assert isinstance(location, VehicleLocation)
        assert location.lat == -23.5505
        assert location.long == -46.6333
        assert isinstance(location.time, datetime.datetime)

    def test_force_refresh_vehicle_state(self, api, token, vehicle, mocker):
        """Test forcing a refresh of the vehicle state."""
        # Mock the vehicle state response
        mock_state_response = mocker.MagicMock()
        mock_state_response.json.return_value = {
            "resMsg": {
                "time": "2023-06-01T12:00:00Z",
                "engine": True,  # Engine is now running
                "doorLock": False,  # Vehicle is unlocked
                "dte": {
                    "value": 450,  # Range decreased
                    "unit": 1,
                },
                "fuelLevel": 70,  # Fuel level decreased
            }
        }

        # Mock the vehicle location response
        mock_location_response = mocker.MagicMock()
        mock_location_response.json.return_value = {
            "resMsg": {
                "coord": {"lat": -23.5505, "lng": -46.6333},
                "time": "2023-06-01T12:00:00Z",
            }
        }

        # Setup mock responses
        mocker.patch.object(
            api.session,
            "get",
            side_effect=[mock_state_response, mock_location_response],
        )

        # Call the force_refresh_vehicle_state method
        api.force_refresh_vehicle_state(token, vehicle)

        # Verify vehicle properties were updated with refreshed data
        assert vehicle.is_locked is False  # Was not set before
        assert vehicle.engine_is_running is True  # Was not set before
        assert vehicle.fuel_level == 70  # Was not set before

        # Verify the REFRESH header was used
        api.session.get.assert_called_with(
            f"{api.api_url}spa/vehicles/{vehicle.id}/status/latest", headers=mocker.ANY
        )

        # Get the headers used in the request
        headers = api.session.get.call_args[1]["headers"]
        assert "REFRESH" in headers
        assert headers["REFRESH"] == "true"

    def test_update_month_trip_info(self, api, token, vehicle, mocker):
        """Test updating the monthly trip information."""
        # Mock the API response
        mock_response = mocker.MagicMock()
        mock_response.json.return_value = {
            "resMsg": {
                "tripPeriodType": 1,  # Month
                "monthTripDayCnt": 20,
                "tripDrvTime": 1500,  # 25 hours
                "tripIdleTime": 300,  # 5 hours
                "tripDist": 1200,  # 1200 km
                "tripAvgSpeed": 80,  # 80 km/h
                "tripMaxSpeed": 120,  # 120 km/h
                "tripDayList": [
                    {"tripDayInMonth": "2023-06-01", "tripCntDay": 2},
                    {"tripDayInMonth": "2023-06-02", "tripCntDay": 3},
                ],
            }
        }
        mocker.patch.object(api.session, "post", return_value=mock_response)

        # Call the update_month_trip_info method
        api.update_month_trip_info(token, vehicle, datetime.date(2023, 6, 1))

        # Verify month_trip_info was updated
        assert vehicle.month_trip_info is not None
        assert vehicle.month_trip_info.yyyymm == "202306"
        assert len(vehicle.month_trip_info.day_list) == 2
        assert vehicle.month_trip_info.day_list[0].yyyymmdd == "20230601"
        assert vehicle.month_trip_info.day_list[0].trip_count == 2
        assert vehicle.month_trip_info.day_list[1].yyyymmdd == "20230602"
        assert vehicle.month_trip_info.day_list[1].trip_count == 3
        assert vehicle.month_trip_info.summary.drive_time == 1500
        assert vehicle.month_trip_info.summary.idle_time == 300
        assert vehicle.month_trip_info.summary.distance == 1200
        assert vehicle.month_trip_info.summary.avg_speed == 80
        assert vehicle.month_trip_info.summary.max_speed == 120

    def test_update_day_trip_info(self, api, token, vehicle, mocker):
        """Test updating the daily trip information."""
        # Mock the API response
        mock_response = mocker.MagicMock()
        mock_response.json.return_value = {
            "resMsg": {
                "tripPeriodType": 0,  # Day
                "monthTripDayCnt": 1,
                "tripDrvTime": 120,  # 2 hours
                "tripIdleTime": 30,  # 30 minutes
                "tripDist": 150,  # 150 km
                "tripAvgSpeed": 75,  # 75 km/h
                "tripMaxSpeed": 110,  # 110 km/h
                "tripDayList": [],  # Empty for day trip info
            }
        }
        mocker.patch.object(api.session, "post", return_value=mock_response)

        # Call the update_day_trip_info method
        api.update_day_trip_info(token, vehicle, datetime.date(2023, 6, 1))

        # Verify day_trip_info was updated
        assert vehicle.day_trip_info is not None
        assert vehicle.day_trip_info.yyyymmdd == "20230601"
        assert len(vehicle.day_trip_info.trip_list) == 0
        assert vehicle.day_trip_info.summary.drive_time == 120
        assert vehicle.day_trip_info.summary.idle_time == 30
        assert vehicle.day_trip_info.summary.distance == 150
        assert vehicle.day_trip_info.summary.avg_speed == 75
        assert vehicle.day_trip_info.summary.max_speed == 110

    def test_get_vehicle_engine_type(self, api):
        """Test the vehicle engine type mapping."""
        assert api._get_vehicle_engine_type("GN") == EngineType.ICE
        assert api._get_vehicle_engine_type("EV") == EngineType.EV
        assert api._get_vehicle_engine_type("PHEV") == EngineType.PHEV
        assert api._get_vehicle_engine_type("HV") == EngineType.HEV
        assert api._get_vehicle_engine_type("PE") == EngineType.PHEV

        with pytest.raises(APIError):
            api._get_vehicle_engine_type("INVALID")
