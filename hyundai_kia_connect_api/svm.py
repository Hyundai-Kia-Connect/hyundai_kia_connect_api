"""SVM shared data model and helpers (region-agnostic).

Region-specific response parsing lives in the region API classes
(HyundaiBlueLinkApiUSA, and the EU GSPA implementation later); this module
holds only what works for all regions.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

from .utils import float_or_none, int_or_none

# Keys redacted wherever they appear in an SVM payload. "coord" covers
# lat/lon/alt for every region's response shape.
SVM_IMAGE_REDACT_KEYS = ("svmImage",)
SVM_LOG_REDACT_KEYS = ("svmImage", "coord", "head")
SVM_REDACTED = "<redacted>"


def _parse_bool(value: str | int | bool | None) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    return str(value).lower() in ("true", "1", "yes", "on")


def _parse_image_sizes(
    raw: Any,
) -> tuple[tuple[int, int] | None, tuple[int, ...] | None]:
    """Parse an SVM ``imageSize`` array.

    Returns ``(image_size, image_sizes)``: ``image_size`` is the first two
    elements as the ``(width, height)`` panorama size; ``image_sizes`` is
    the full array — panorama followed by the individual camera segments —
    and is only returned when every element parses as an integer, since
    consumers use it to compute segment crop offsets.
    """
    if not isinstance(raw, list) or len(raw) < 2:
        return None, None
    width = int_or_none(raw[0])
    height = int_or_none(raw[1])
    image_size = (width, height) if width is not None and height is not None else None
    parsed: list[int] = []
    for value in raw:
        item = int_or_none(value)
        if item is None:
            return image_size, None
        parsed.append(item)
    return image_size, tuple(parsed)


def _parse_float_list(raw: Any) -> tuple[float, ...] | None:
    """Parse a float array (e.g. ``validAngleofView``); None if malformed."""
    if not isinstance(raw, list):
        return None
    parsed: list[float] = []
    for value in raw:
        item = float_or_none(value)
        if item is None:
            return None
        parsed.append(item)
    return tuple(parsed)


def _parse_door_open(door_open: dict[str, Any] | None) -> dict[str, bool | None] | None:
    """Map an SVM doorOpen object onto the shared door keys.

    Both the USA and EU GSPA responses carry the same door keys; door
    values are booleans in the USA response and 0/1 ints in the EU
    response — ``_parse_bool`` normalizes both.
    """
    if not isinstance(door_open, dict) or not door_open:
        return None
    return {
        key: _parse_bool(door_open.get(key))
        for key in ("frontLeft", "frontRight", "backLeft", "backRight")
    }


@dataclass
class SVMDetails:
    image_bytes: bytes
    captured_at: dt.datetime | None = None
    captured_at_raw: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    heading: int | None = None
    speed: tuple[float | None, str | None] = (None, None)
    door_open: dict[str, bool | None] | None = None
    trunk_open: bool | None = None
    image_size: tuple[int, int] | None = None
    # Full imageSize array: panorama size followed by the width of each
    # camera segment. Consumers use it to crop individual views.
    image_sizes: tuple[int, ...] | None = None
    # GSPA responses carry a per-camera field-of-view array; present only
    # on some regions, None when absent or malformed.
    valid_angle_of_view: tuple[float, ...] | None = None
    raw_metadata: dict[str, Any] | None = None


def redact_svm_metadata(data: dict[str, Any], *, gps: bool = True) -> dict[str, Any]:
    """Return a copy of an SVM payload with sensitive keys redacted.

    Recursively redacts the base64 image wherever it appears, so one helper
    covers every region's response shape. With ``gps=True`` (the default,
    for debug logging) GPS-bearing keys are redacted too; with ``gps=False``
    coordinates are preserved for ``SVMDetails.raw_metadata`` consumers —
    the typed fields on ``SVMDetails`` already carry the coordinates.
    Non-dict input yields an empty dict.
    """
    if not isinstance(data, dict):
        return {}
    redact_keys = SVM_LOG_REDACT_KEYS if gps else SVM_IMAGE_REDACT_KEYS

    def _redact(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: SVM_REDACTED if key in redact_keys else _redact(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [_redact(item) for item in value]
        return value

    redacted: dict[str, Any] = _redact(data)
    return redacted
