import base64
import datetime as dt


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
