import base64
import datetime as dt
from unittest.mock import MagicMock, patch

import pytest

from hyundai_kia_connect_api.exceptions import (
    APIError,
    DuplicateRequestError,
    RequestTimeoutError,
    SafetyAcknowledgmentError,
)
from hyundai_kia_connect_api.HyundaiBlueLinkApiUSA import HyundaiBlueLinkApiUSA
from hyundai_kia_connect_api.svm import SVMDetails
from hyundai_kia_connect_api.Token import Token
from hyundai_kia_connect_api.Vehicle import Vehicle


def test_vehicle_has_supports_svm_field_default_none():
    vehicle = _make_vehicle()
    assert vehicle.supports_svm is None


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


def test_api_impl_supports_svm_returns_cached_true():
    from hyundai_kia_connect_api.ApiImpl import ApiImpl

    api = ApiImpl()
    token = MagicMock()
    vehicle = _make_vehicle()
    vehicle.supports_svm = True
    assert api.supports_svm(token, vehicle) is True


def test_api_impl_supports_svm_returns_cached_false():
    from hyundai_kia_connect_api.ApiImpl import ApiImpl

    api = ApiImpl()
    token = MagicMock()
    vehicle = _make_vehicle()
    vehicle.supports_svm = False
    assert api.supports_svm(token, vehicle) is False


def test_api_impl_supports_svm_default_false_when_not_cached():
    from hyundai_kia_connect_api.ApiImpl import ApiImpl

    api = ApiImpl()
    token = MagicMock()
    vehicle = _make_vehicle()
    assert vehicle.supports_svm is None
    assert api.supports_svm(token, vehicle) is False
    assert vehicle.supports_svm is None


def _make_api():
    api = object.__new__(HyundaiBlueLinkApiUSA)
    api.API_URL = "https://api.telematics.hyundaiusa.com/ac/v2/"
    api.API_HEADERS = {}
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
        if isinstance(self.json_data, str):
            raise ValueError("No JSON object could be decoded")
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


def test_usa_supports_svm_true_when_image_present():
    api = _make_api()
    vehicle = _make_vehicle()
    response = _make_svm_response(b"\xff\xd8\xff\xe0fakejpg", "2026-06-23T12:34:56Z")
    api.session.get.return_value = _FakeResponse(response)

    with patch.object(api, "_get_vehicle_headers", return_value={"x": "y"}):
        assert api.supports_svm(_make_token(), vehicle) is True
    assert vehicle.supports_svm is True


def test_usa_supports_svm_false_when_image_empty():
    api = _make_api()
    vehicle = _make_vehicle()
    response = _make_svm_response(b"", "2026-06-23T12:34:56Z")
    api.session.get.return_value = _FakeResponse(response)

    with patch.object(api, "_get_vehicle_headers", return_value={"x": "y"}):
        assert api.supports_svm(_make_token(), vehicle) is False
    assert vehicle.supports_svm is False


def test_usa_supports_svm_false_on_api_error():
    api = _make_api()
    vehicle = _make_vehicle()
    api.session.get.return_value = _FakeResponse(
        {"errorCode": "502", "errorMessage": "nope"}, status_code=200
    )

    with patch.object(api, "_get_vehicle_headers", return_value={"x": "y"}):
        assert api.supports_svm(_make_token(), vehicle) is False
    assert vehicle.supports_svm is False


def test_usa_supports_svm_caches_result():
    api = _make_api()
    vehicle = _make_vehicle()
    response = _make_svm_response(b"\xff\xd8\xff\xe0fakejpg", "2026-06-23T12:34:56Z")
    api.session.get.return_value = _FakeResponse(response)

    with patch.object(api, "_get_vehicle_headers", return_value={"x": "y"}):
        assert api.supports_svm(_make_token(), vehicle) is True
        assert api.supports_svm(_make_token(), vehicle) is True

    assert api.session.get.call_count == 1


def test_request_svm_capture_requires_acknowledgment():
    api = _make_api()
    api.session.get.return_value = _FakeResponse(_make_svm_response(b"", "0"))

    with pytest.raises(SafetyAcknowledgmentError):
        api.request_svm_capture(_make_token(), _make_vehicle())


def test_request_svm_capture_maps_ht_533_to_duplicate_request():
    api = _make_api()
    api.session.get.return_value = _FakeResponse(_make_svm_response(b"", "0"))
    api.session.post.return_value = _FakeResponse(
        {
            "errorCode": "502",
            "errorSubCode": "HT_533",
            "errorMessage": "Unable to send your request because a previous request is pending...",
            "serviceName": "FindMyCarSVM",
        },
        status_code=502,
    )

    with patch("hyundai_kia_connect_api.HyundaiBlueLinkApiUSA.time.sleep"):
        with pytest.raises(DuplicateRequestError):
            api.request_svm_capture(
                _make_token(), _make_vehicle(), acknowledged_warning=True
            )


def test_request_svm_capture_non_ht_533_502_is_api_error():
    from hyundai_kia_connect_api.exceptions import APIError

    api = _make_api()
    api.session.get.return_value = _FakeResponse(_make_svm_response(b"", "0"))
    api.session.post.return_value = _FakeResponse(
        {
            "errorCode": "502",
            "errorSubCode": "HT_123",
            "errorMessage": "Something else went wrong",
            "serviceName": "FindMyCarSVM",
        },
        status_code=502,
    )

    with patch("hyundai_kia_connect_api.HyundaiBlueLinkApiUSA.time.sleep"):
        with pytest.raises(APIError, match="Something else went wrong"):
            api.request_svm_capture(
                _make_token(), _make_vehicle(), acknowledged_warning=True
            )


def test_request_svm_capture_polls_until_new_timestamp():
    api = _make_api()
    baseline = _make_svm_response(b"old", "2026-06-23T12:00:00Z")
    fresh = _make_svm_response(b"new", "2026-06-23T12:05:00Z")
    api.session.get.side_effect = [
        _FakeResponse(baseline),
        _FakeResponse(fresh),
    ]
    api.session.post.return_value = _FakeResponse({"tid": "abc-123"})

    with patch("hyundai_kia_connect_api.HyundaiBlueLinkApiUSA.time.sleep"):
        details = api.request_svm_capture(
            _make_token(), _make_vehicle(), acknowledged_warning=True
        )

    assert details.image_bytes == b"new"
    assert details.captured_at_raw == "2026-06-23T12:05:00Z"

    posted_json = api.session.post.call_args.kwargs["json"]
    assert posted_json["vin"] == "KM8XXXX"
    assert posted_json["username"] == "test-user"
    assert posted_json["gen"] == "3"
    assert posted_json["blueLinkServicePin"] == "1234"


def test_request_svm_capture_times_out_when_timestamp_never_changes():
    api = _make_api()
    baseline = _make_svm_response(b"old", "2026-06-23T12:00:00Z")
    api.session.get.return_value = _FakeResponse(baseline)
    api.session.post.return_value = _FakeResponse({"tid": "abc-123"})

    with patch("hyundai_kia_connect_api.HyundaiBlueLinkApiUSA.time.sleep"):
        with pytest.raises(RequestTimeoutError):
            api.request_svm_capture(
                _make_token(), _make_vehicle(), acknowledged_warning=True
            )


def test_get_svm_details_logs_do_not_contain_image_or_gps(caplog):
    import logging

    api = _make_api()
    image = b"\xff\xd8\xff\xe0secretimage"
    response = _make_svm_response(image, "2026-06-23T12:34:56Z")
    api.session.get.return_value = _FakeResponse(response)

    with caplog.at_level(logging.DEBUG):
        with patch.object(api, "_get_vehicle_headers", return_value={"x": "y"}):
            api.get_svm_details(_make_token(), _make_vehicle())

    # The logged image is base64-encoded, not the raw bytes.
    image_b64 = base64.b64encode(image).decode("ascii")
    assert image_b64 not in caplog.text
    assert "secretimage" not in caplog.text
    assert "12.345678" not in caplog.text
    assert "-98.765432" not in caplog.text
    assert "<redacted>" in caplog.text


def test_svm_details_exported_from_package_root():
    from hyundai_kia_connect_api import SVMDetails as ExportedSVMDetails

    assert ExportedSVMDetails is SVMDetails


def test_parse_svm_response_raw_metadata_preserves_full_response():
    from hyundai_kia_connect_api.svm import parse_svm_response

    image = b"\xff\xd8\xff\xe0fakejpg"
    response = _make_svm_response(image, "2026-06-23T12:34:56Z")
    response["topLevelField"] = "preserved"

    details = parse_svm_response(response, dt.timezone.utc)

    assert details.raw_metadata is not None
    assert details.raw_metadata["topLevelField"] == "preserved"
    assert (
        details.raw_metadata["svmDetails"][0]["svmDetail"]["svmImage"] == "<redacted>"
    )
    # GPS coordinates must be preserved in raw_metadata.
    assert (
        details.raw_metadata["svmDetails"][0]["svmDetail"]["gpsDetail"]["coord"]["lat"]
        == "12.345678"
    )


def test_request_svm_capture_empty_502_is_api_error():
    api = _make_api()
    api.session.get.return_value = _FakeResponse(_make_svm_response(b"", "0"))
    api.session.post.return_value = _FakeResponse("not json", status_code=502)

    with patch("hyundai_kia_connect_api.HyundaiBlueLinkApiUSA.time.sleep"):
        with pytest.raises(APIError, match="SVM request failed with HTTP 502"):
            api.request_svm_capture(
                _make_token(), _make_vehicle(), acknowledged_warning=True
            )


def test_svm_is_fresh_uses_raw_string_when_parsed_time_is_none():
    baseline = SVMDetails(
        image_bytes=b"", captured_at=None, captured_at_raw="baseline-raw"
    )
    fresh = SVMDetails(
        image_bytes=b"new", captured_at=None, captured_at_raw="fresh-raw"
    )

    assert HyundaiBlueLinkApiUSA._svm_is_fresh(fresh, None, "baseline-raw") is True
    assert HyundaiBlueLinkApiUSA._svm_is_fresh(baseline, None, "baseline-raw") is False


def test_request_svm_capture_detects_freshness_from_raw_string():
    api = _make_api()
    baseline = _make_svm_response(b"old", "not-a-parseable-date-v1")
    fresh = _make_svm_response(b"new", "not-a-parseable-date-v2")
    api.session.get.side_effect = [
        _FakeResponse(baseline),
        _FakeResponse(fresh),
    ]
    api.session.post.return_value = _FakeResponse({"tid": "abc-123"})

    with patch("hyundai_kia_connect_api.HyundaiBlueLinkApiUSA.time.sleep"):
        details = api.request_svm_capture(
            _make_token(), _make_vehicle(), acknowledged_warning=True
        )

    assert details.image_bytes == b"new"
    assert details.captured_at is None
    assert details.captured_at_raw == "not-a-parseable-date-v2"
