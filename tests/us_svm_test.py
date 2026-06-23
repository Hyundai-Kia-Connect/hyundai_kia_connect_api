import base64
import datetime as dt
from unittest.mock import MagicMock, patch

import pytest

from hyundai_kia_connect_api.HyundaiBlueLinkApiUSA import HyundaiBlueLinkApiUSA
from hyundai_kia_connect_api.Token import Token
from hyundai_kia_connect_api.Vehicle import Vehicle


def test_svm_details_can_be_imported():
    from hyundai_kia_connect_api.svm import SVMDetails, parse_svm_response

    assert SVMDetails is not None
    assert parse_svm_response is not None


def _make_svm_response(image_bytes: bytes, captured_at: str):
    return {
        "svmDetails": [
            {
                "svmDetail": {
                    "svmImage": base64.b64encode(image_bytes).decode("ascii"),
                    "gpsDetail": {
                        "coord": {
                            "lat": "12.345678",
                            "lon": "-98.765432",
                            "alt": "123",
                            "type": "0",
                        },
                        "head": "149",
                        "speed": {"value": "0", "unit": "0"},
                        "time": captured_at,
                    },
                    "doorOpen": {
                        "frontLeft": "0",
                        "frontRight": "1",
                        "backLeft": "0",
                        "backRight": "1",
                    },
                    "trunkOpen": "false",
                    "imageSize": ["4472", "720", "960", "720", "632", "720"],
                }
            }
        ]
    }


def test_parse_svm_response_decodes_image_and_metadata():
    from hyundai_kia_connect_api.svm import parse_svm_response

    image = b"\xff\xd8\xff\xe0fakejpg"
    tz = dt.timezone.utc
    response = _make_svm_response(image, "2026-06-23T12:34:56Z")

    details = parse_svm_response(response, tz)

    assert details.image_bytes == image
    assert details.captured_at_raw == "2026-06-23T12:34:56Z"
    assert details.captured_at == dt.datetime(
        2026, 6, 23, 12, 34, 56, tzinfo=dt.timezone.utc
    )
    assert details.latitude == 12.345678
    assert details.longitude == -98.765432
    assert details.heading == 149
    assert details.speed == (0.0, "0")
    assert details.door_open == {
        "frontLeft": False,
        "frontRight": True,
        "backLeft": False,
        "backRight": True,
    }
    assert details.trunk_open is False
    assert details.image_size == (4472, 720)


def test_parse_svm_response_unknown_timestamp_does_not_crash():
    from hyundai_kia_connect_api.svm import parse_svm_response

    image = b"\xff\xd8\xff\xe0fakejpg"
    response = _make_svm_response(image, "not-a-date")
    details = parse_svm_response(response, dt.timezone.utc)

    assert details.image_bytes == image
    assert details.captured_at is None
    assert details.captured_at_raw == "not-a-date"


def test_redact_svm_response_for_log_strips_image_and_gps():
    from hyundai_kia_connect_api.svm import redact_svm_response_for_log

    image = b"\xff\xd8\xff\xe0fakejpg"
    response = _make_svm_response(image, "2026-06-23T12:34:56Z")
    safe = redact_svm_response_for_log(response)

    detail = safe["svmDetails"][0]["svmDetail"]
    assert detail["svmImage"] == "<redacted>"
    assert detail["gpsDetail"]["coord"]["lat"] == "<redacted>"
    assert detail["gpsDetail"]["coord"]["lon"] == "<redacted>"
    assert detail["gpsDetail"]["head"] == "<redacted>"


def test_safety_acknowledgment_error_is_api_error():
    from hyundai_kia_connect_api.exceptions import (
        APIError,
        SafetyAcknowledgmentError,
    )

    assert issubclass(SafetyAcknowledgmentError, APIError)


def test_api_impl_svm_stubs_raise_not_implemented():
    from hyundai_kia_connect_api.ApiImpl import ApiImpl
    from unittest.mock import MagicMock

    api = ApiImpl()
    token = MagicMock()
    vehicle = MagicMock()

    with pytest.raises(NotImplementedError):
        api.get_svm_details(token, vehicle)

    with pytest.raises(NotImplementedError):
        api.request_svm_capture(token, vehicle, acknowledged_warning=True)


def _make_api():
    api = object.__new__(HyundaiBlueLinkApiUSA)
    api.API_URL = "https://api.telematics.hyundaiusa.com/ac/v2/"
    api.data_timezone = dt.timezone.utc
    api.session = MagicMock()
    return api


def _make_token():
    token = MagicMock(spec=Token)
    token.username = "test-user"
    token.access_token = "test-token"
    token.pin = "1234"
    return token


def _make_vehicle():
    return Vehicle(
        id="test-id",
        name="Ioniq 5",
        model="IONIQ 5",
        VIN="KM8XXXX",
        generation=3,
        key="test-key",
        timezone=dt.timezone(dt.timedelta(hours=-5)),
    )


class _FakeResponse:
    def __init__(self, json_data, status_code=200):
        self.json_data = json_data
        self.status_code = status_code
        self.text = str(json_data)

    def json(self):
        return self.json_data


def test_get_svm_details_calls_correct_endpoint():
    api = _make_api()
    image = b"\xff\xd8\xff\xe0fakejpg"
    response = _make_svm_response(image, "2026-06-23T12:34:56Z")
    api.session.get.return_value = _FakeResponse(response)

    with patch.object(api, "_get_vehicle_headers", return_value={"x": "y"}):
        details = api.get_svm_details(_make_token(), _make_vehicle())

    api.session.get.assert_called_once()
    call_url = api.session.get.call_args[0][0]
    assert call_url.endswith("svm/getSVMDetails")
    assert details.image_bytes == image
