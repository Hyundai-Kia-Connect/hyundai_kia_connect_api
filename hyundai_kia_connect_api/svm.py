"""SVM / Find My Car data model and response helpers."""

from __future__ import annotations

import base64
import datetime as dt
import logging
from dataclasses import dataclass

from .utils import get_child_value, parse_datetime

_LOGGER = logging.getLogger(__name__)


def _parse_bool(value: str | int | bool | None) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    return str(value).lower() in ("true", "1", "yes", "on")


def _parse_int(value: str | int | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def _parse_float(value: str | float | int | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


@dataclass
class SVMDetails:
    image_bytes: bytes
    captured_at: dt.datetime | None = None
    captured_at_raw: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    heading: int | None = None
    speed: tuple[float | None, str | None] = (None, None)
    door_open: dict[str, bool] | None = None
    trunk_open: bool | None = None
    image_size: tuple[int, int] | None = None
    raw_metadata: dict | None = None


def _parse_door_open(door_open: dict | None) -> dict[str, bool] | None:
    if not door_open:
        return None
    mapping = {
        "frontLeft": "frontLeft",
        "frontRight": "frontRight",
        "backLeft": "backLeft",
        "backRight": "backRight",
    }
    result = {}
    for our_key, api_key in mapping.items():
        result[our_key] = _parse_bool(door_open.get(api_key))
    return result


def parse_svm_response(response: dict, timezone: dt.timezone) -> SVMDetails:
    """Parse a getSVMDetails response into SVMDetails.

    Args:
        response: parsed JSON from `GET /ac/v2/svm/getSVMDetails`.
        timezone: timezone to use when parsing the capture timestamp.

    Returns:
        SVMDetails with decoded image bytes and metadata.
    """
    detail = get_child_value(response, "svmDetails.0.svmDetail") or {}
    image_b64 = detail.get("svmImage", "")
    image_bytes = base64.b64decode(image_b64) if image_b64 else b""

    captured_at_raw = get_child_value(detail, "gpsDetail.time")
    captured_at = None
    if captured_at_raw:
        try:
            captured_at = parse_datetime(captured_at_raw, timezone)
        except (ValueError, TypeError):
            _LOGGER.debug("Unable to parse SVM capture timestamp: %s", captured_at_raw)

    image_size_raw = detail.get("imageSize")
    image_size = None
    if isinstance(image_size_raw, list) and len(image_size_raw) >= 2:
        width = _parse_int(image_size_raw[0])
        height = _parse_int(image_size_raw[1])
        if width is not None and height is not None:
            image_size = (width, height)

    speed_value = _parse_float(get_child_value(detail, "gpsDetail.speed.value"))
    speed_unit = get_child_value(detail, "gpsDetail.speed.unit")

    # Store metadata for debugging, but never persist the base64 image bytes.
    safe_metadata = dict(detail) if detail else {}
    safe_metadata["svmImage"] = "<redacted>"

    return SVMDetails(
        image_bytes=image_bytes,
        captured_at=captured_at,
        captured_at_raw=captured_at_raw,
        latitude=_parse_float(get_child_value(detail, "gpsDetail.coord.lat")),
        longitude=_parse_float(get_child_value(detail, "gpsDetail.coord.lon")),
        heading=_parse_int(get_child_value(detail, "gpsDetail.head")),
        speed=(speed_value, speed_unit),
        door_open=_parse_door_open(detail.get("doorOpen")),
        trunk_open=_parse_bool(detail.get("trunkOpen")),
        image_size=image_size,
        raw_metadata=safe_metadata,
    )


def redact_svm_response_for_log(response: dict) -> dict:
    """Return a copy of an SVM response safe for debug logging.

    Removes the base64 image and GPS coordinates.
    """
    safe = dict(response) if response else {}
    if "svmDetails" in safe and isinstance(safe["svmDetails"], list):
        safe["svmDetails"] = [
            _redact_svm_detail_entry(entry) for entry in safe["svmDetails"]
        ]
    return safe


def _redact_svm_detail_entry(entry: dict) -> dict:
    if not isinstance(entry, dict):
        return entry
    safe_entry = dict(entry)
    if "svmDetail" in safe_entry and isinstance(safe_entry["svmDetail"], dict):
        safe_detail = dict(safe_entry["svmDetail"])
        safe_detail["svmImage"] = "<redacted>"
        gps = safe_detail.get("gpsDetail")
        if isinstance(gps, dict):
            safe_gps = dict(gps)
            coord = safe_gps.get("coord")
            if isinstance(coord, dict):
                safe_coord = dict(coord)
                safe_coord["lat"] = "<redacted>"
                safe_coord["lon"] = "<redacted>"
                safe_coord["alt"] = "<redacted>"
                safe_gps["coord"] = safe_coord
            safe_gps["head"] = "<redacted>"
            safe_detail["gpsDetail"] = safe_gps
        safe_entry["svmDetail"] = safe_detail
    return safe_entry
