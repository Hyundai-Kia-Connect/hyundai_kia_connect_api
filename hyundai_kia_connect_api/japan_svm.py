"""Japan's request-correlated surround-view capture protocol.

The public Japan app polls /svm/async with the capture's msgId. No uncorrelated
latest-image endpoint is established, so passive reads use completed captures
held in this adapter's memory and never trigger a capture.
"""

import base64
import binascii
import datetime as dt
import re
import time

from .exceptions import (
    APIError,
    AuthenticationError,
    InvalidAPIResponseError,
    NoDataFound,
    RateLimitingError,
    RequestTimeoutError,
    SafetyAcknowledgmentError,
    UnsupportedControlError,
)
from .svm import (
    SVMDetails,
    _parse_bool,
    _parse_door_open,
    _parse_float,
    _parse_float_list,
    _parse_image_sizes,
    _parse_int,
    redact_svm_metadata,
)
from .utils import get_child_value, parse_datetime


def parse_japan_svm(payload: dict, timezone: dt.tzinfo) -> SVMDetails:
    """Parse a Japan resMsg.scsDetail without logging image or location data."""
    if not isinstance(payload, dict):
        raise InvalidAPIResponseError("Japan camera response is incomplete")
    detail = payload.get("scsDetail")
    if not isinstance(detail, dict):
        raise InvalidAPIResponseError("Japan camera response is incomplete")
    encoded = detail.get("svmImage")
    if not isinstance(encoded, str) or not encoded:
        raise InvalidAPIResponseError("Japan camera image is missing")
    try:
        # MIME-style line wrapping is valid image transport; retain strict
        # alphabet validation after removing only ASCII whitespace.
        image = base64.b64decode(re.sub(r"[ \t\r\n]", "", encoded), validate=True)
    except (ValueError, binascii.Error):
        raise InvalidAPIResponseError("Japan camera image is invalid") from None
    if not image:
        raise InvalidAPIResponseError("Japan camera image is empty")
    raw_time = get_child_value(detail, "gpsDetail.time")
    captured_at = None
    if raw_time:
        try:
            captured_at = parse_datetime(raw_time, timezone)
        except (ValueError, TypeError, AttributeError):
            raise InvalidAPIResponseError("Japan camera timestamp is invalid") from None
    try:
        size, sizes = _parse_image_sizes(detail.get("imageSize"))
    except (OverflowError, ValueError, TypeError):
        raise InvalidAPIResponseError(
            "Japan camera image dimensions are invalid"
        ) from None
    return SVMDetails(
        image_bytes=image,
        captured_at=captured_at,
        captured_at_raw=raw_time,
        latitude=_parse_float(get_child_value(detail, "gpsDetail.coord.lat")),
        longitude=_parse_float(get_child_value(detail, "gpsDetail.coord.lon")),
        heading=_parse_int(get_child_value(detail, "gpsDetail.head")),
        speed=(
            _parse_float(get_child_value(detail, "gpsDetail.speed.value")),
            get_child_value(detail, "gpsDetail.speed.unit"),
        ),
        door_open=_parse_door_open(detail.get("doorOpen")),
        trunk_open=_parse_bool(detail.get("trunkOpen")),
        image_size=size,
        image_sizes=sizes,
        valid_angle_of_view=_parse_float_list(detail.get("validAngleofView")),
        raw_metadata=redact_svm_metadata(payload, gps=False),
    )


class JapanSVM:
    """Capture mixin using the Japan adapter's sanitized HTTP helpers."""

    _SVM_POLL_INTERVAL_SECONDS = 5
    # Japan's app allows 145 seconds for the vehicle to upload its capture.
    _SVM_POLL_TIMEOUT_SECONDS = 145

    @property
    def svm_diagnostics(self) -> dict:
        """Describe only the latest local capture workflow, without private data."""
        summary = dict(getattr(self, "_japan_svm_diagnostics", {"stage": "idle"}))
        started = getattr(self, "_japan_svm_started", None)
        if started is not None:
            summary["elapsed_seconds"] = round(max(0, time.perf_counter() - started), 1)
        return summary

    def get_svm_details(self, token, vehicle) -> SVMDetails:
        """Return this session's last completed capture without waking the car."""
        details = getattr(self, "_japan_svm_cache", {}).get(vehicle.id)
        if details is None:
            raise NoDataFound("Japan camera requires a new manual capture")
        return details

    def request_svm_capture(
        self, token, vehicle, acknowledged_warning: bool = False
    ) -> SVMDetails:
        """Keep a secret-free workflow outcome even when a capture fails."""
        self._japan_svm_started = time.perf_counter()
        self._japan_svm_diagnostics = {
            "stage": "validation",
            "poll_count": 0,
            "image_present": False,
            "dimensions_present": False,
            "failure_category": None,
        }
        try:
            return self._request_japan_svm_capture(token, vehicle, acknowledged_warning)
        except Exception as exc:
            category = "unexpected"
            for error_type, label in (
                (AuthenticationError, "authentication"),
                (SafetyAcknowledgmentError, "acknowledgment"),
                (UnsupportedControlError, "unsupported"),
                (RequestTimeoutError, "timeout"),
                (InvalidAPIResponseError, "invalid_response"),
                (RateLimitingError, "rate_limit"),
                (APIError, "api_rejected"),
            ):
                if isinstance(exc, error_type):
                    category = label
                    break
            self._japan_svm_diagnostics.update(
                failed_stage=self._japan_svm_diagnostics["stage"],
                stage="failed",
                failure_category=category,
            )
            raise
        finally:
            self._japan_svm_diagnostics["elapsed_seconds"] = round(
                max(0, time.perf_counter() - self._japan_svm_started), 1
            )
            self._japan_svm_started = None

    def _request_japan_svm_capture(
        self, token, vehicle, acknowledged_warning: bool
    ) -> SVMDetails:
        """Request once and poll only that request; an ACK is not an image."""
        if acknowledged_warning is not True:
            raise SafetyAcknowledgmentError(
                "request_svm_capture requires acknowledged_warning=True"
            )
        if vehicle.supports_svm is not True:
            raise UnsupportedControlError(
                "Japan vehicle does not support camera capture"
            )
        if not isinstance(vehicle.id, str) or not re.fullmatch(
            r"[A-Za-z0-9_-]+", vehicle.id
        ):
            raise InvalidAPIResponseError("Japan vehicle identifier is invalid")
        # The app's camera flow validates its PIN grant before requesting an
        # image, but SVMApi deliberately uses the ordinary OAuth authorization.
        self._japan_svm_diagnostics["stage"] = "pin"
        self._control_headers(token, vehicle)
        path = f"/api/v1/spa/vehicles/{vehicle.id}/svm/async"
        self._japan_svm_diagnostics["stage"] = "submission"
        result = self._request(
            "POST", path, headers=self._vehicle_headers(token, vehicle)
        )
        message_id = result.get("msgId")
        if not isinstance(message_id, str) or not message_id:
            raise InvalidAPIResponseError("Japan camera request identifier is missing")
        self._japan_svm_diagnostics["stage"] = "submitted"
        deadline = time.monotonic() + self._SVM_POLL_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            self._japan_svm_diagnostics["stage"] = "polling"
            self._japan_svm_diagnostics["poll_count"] += 1
            try:
                result = self._request(
                    "GET",
                    path,
                    headers=self._vehicle_headers(token, vehicle),
                    params={"msgId": message_id},
                )
            except NoDataFound:
                remaining = deadline - time.monotonic()
                if remaining > 0:
                    time.sleep(min(self._SVM_POLL_INTERVAL_SECONDS, remaining))
                continue
            details = parse_japan_svm(self._payload(result), self.data_timezone)
            self._japan_svm_diagnostics.update(
                stage="complete",
                image_present=bool(details.image_bytes),
                dimensions_present=details.image_size is not None,
            )
            if not hasattr(self, "_japan_svm_cache"):
                self._japan_svm_cache = {}
            self._japan_svm_cache[vehicle.id] = details
            return details
        raise RequestTimeoutError("Japan camera capture timed out")
