"""Tests for svm_image rendering (requires the [image] extra)."""

import datetime as dt
import io

import pytest

from hyundai_kia_connect_api.svm import SVMDetails
from hyundai_kia_connect_api.svm_image import camera_fov_deg

pytest.importorskip("PIL", reason="svm_image tests need the [image] extra")
pytest.importorskip("numpy", reason="svm_image tests need the [image] extra")

# Mirrors the live USA capture layout: composite 4472x720, cameras 960 wide,
# top (bird's-eye) 632 wide. image_sizes = (panorama_w, panorama_h,
# camera_w, camera_h, top_w, top_h) — width_index 2 for cameras, 4 for top.
PANORAMA = (4472, 720)
SEGMENT_WIDTHS = {"front": 960, "rear": 960, "left": 960, "right": 960, "top": 632}
SEGMENT_COLORS = {
    "front": (255, 0, 0),
    "rear": (0, 255, 0),
    "left": (0, 0, 255),
    "right": (255, 255, 0),
    "top": (255, 0, 255),
}

VIEW_KEYS = ("front", "rear", "left", "right", "top")


def _composite_jpeg() -> bytes:
    """Build a synthetic SVM composite: 5 solid-color segments side by side."""
    from PIL import Image

    img = Image.new("RGB", PANORAMA, (0, 0, 0))
    x = 0
    for key in VIEW_KEYS:
        width = SEGMENT_WIDTHS[key]
        img.paste(Image.new("RGB", (width, 720), SEGMENT_COLORS[key]), (x, 0))
        x += width
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=100, subsampling=0)
    return buf.getvalue()


def _calibration() -> tuple[float, ...]:
    """4 cams x 7 floats; [order*7+1] is the horizontal FOV (live EU values)."""
    fovs = (76.0, 78.0, 87.0, 81.0)
    values = [0.0] * 28
    for order in range(4):
        values[order * 7] = float(order)
        values[order * 7 + 1] = fovs[order]
        values[order * 7 + 2] = 60.0
    return tuple(values)


def _make_details(
    image_sizes: tuple[int, ...] | None = (4472, 720, 960, 720, 632, 720),
    valid_angle_of_view: tuple[float, ...] | None = None,
    image_bytes: bytes | None = None,
) -> SVMDetails:
    return SVMDetails(
        image_bytes=image_bytes if image_bytes is not None else _composite_jpeg(),
        captured_at=dt.datetime(2026, 9, 15, 12, 0, 0, tzinfo=dt.UTC),
        image_size=(4472, 720),
        image_sizes=image_sizes,
        valid_angle_of_view=valid_angle_of_view,
    )


def _close(actual, expected, tol=2):
    return all(abs(x - y) <= tol for x, y in zip(actual, expected))


def test_camera_fov_deg_reads_calibration():
    calib = _calibration()
    assert camera_fov_deg(calib, 0) == 76.0
    assert camera_fov_deg(calib, 1) == 78.0
    assert camera_fov_deg(calib, 2) == 87.0
    assert camera_fov_deg(calib, 3) == 81.0


def test_camera_fov_deg_guards():
    assert camera_fov_deg(None, 0) is None
    assert camera_fov_deg(_calibration(), 4) is None
    assert camera_fov_deg((1.5, 2.5), 0) is None  # shorter than the 4-cam layout
    zero_fov = list(_calibration())
    zero_fov[1] = 0.0  # outside the (0, 180] range
    assert camera_fov_deg(tuple(zero_fov), 0) is None
    big_fov = list(_calibration())
    big_fov[1] = 180.5
    assert camera_fov_deg(tuple(big_fov), 0) is None
