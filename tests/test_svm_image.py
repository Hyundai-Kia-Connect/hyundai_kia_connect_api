"""Tests for svm_image rendering (requires the [image] extra)."""

import datetime as dt
import io
import sys

import pytest

from hyundai_kia_connect_api.svm import SVMDetails
from hyundai_kia_connect_api.svm_image import (
    camera_fov_deg,
    crop_view,
    render_views,
    to_jpeg_bytes,
)

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


def test_render_views_raw_returns_five_views():
    views = render_views(_make_details())
    assert set(views) == set(VIEW_KEYS)


def test_render_views_crop_sizes_and_colors():
    from PIL import Image

    views = render_views(_make_details())
    for key in ("front", "rear", "left", "right"):
        img = Image.open(io.BytesIO(views[key]))
        assert img.size == (960, 720)
        assert _close(img.getpixel((10, 10)), SEGMENT_COLORS[key])
    img = Image.open(io.BytesIO(views["top"]))
    assert img.size == (632, 720)
    assert _close(img.getpixel((10, 10)), SEGMENT_COLORS["top"])


def test_render_views_omits_views_when_image_sizes_missing():
    views = render_views(_make_details(image_sizes=None))
    assert views == {}


def test_render_views_omits_views_when_sizes_too_short():
    views = render_views(_make_details(image_sizes=(4472, 720)))
    assert views == {}


def test_crop_view_unknown_view_returns_none():
    assert crop_view(_make_details(), "hood") is None


def test_crop_view_without_image_bytes_returns_none():
    assert crop_view(_make_details(image_bytes=b""), "front") is None


def test_crop_view_missing_pillow_raises_with_hint(monkeypatch):
    # Build the fixture before hiding PIL: _composite_jpeg() itself imports
    # Pillow, so constructing details inside the block would raise the raw
    # "import of PIL halted" error before crop_view is ever reached.
    details = _make_details()
    monkeypatch.setitem(sys.modules, "PIL", None)
    with pytest.raises(ImportError, match=r"hyundai_kia_connect_api\[image\]"):
        crop_view(details, "front")


def test_render_views_dewarp_changes_cameras_not_top():
    # Solid-color segments dewarp to themselves: the 0.78 output-FOV zoom
    # keeps the output rectangle inside the fisheye circle, so a constant
    # field resamples to the same constant and the JPEG bytes stay identical.
    # Use a gradient composite instead — dewarp resamples it, so the camera
    # bytes must change, while TOP (never dewarped) stays raw-identical.
    from PIL import Image

    gradient = Image.linear_gradient("L").resize(PANORAMA).convert("RGB")
    buf = io.BytesIO()
    gradient.save(buf, format="JPEG", quality=100, subsampling=0)
    details = _make_details(
        image_bytes=buf.getvalue(), valid_angle_of_view=_calibration()
    )
    raw = render_views(details)
    dewarped = render_views(details, dewarp=True)
    assert set(dewarped) == set(raw)
    assert dewarped["top"] == raw["top"]
    for key in ("front", "rear", "left", "right"):
        assert dewarped[key] != raw[key]


def test_render_views_dewarp_without_calibration_serves_raw():
    raw = render_views(_make_details())
    dewarped = render_views(_make_details(), dewarp=True)
    assert set(dewarped) == set(VIEW_KEYS)
    for key in raw:
        assert dewarped[key] == raw[key]


def test_render_views_missing_numpy_raises_with_hint(monkeypatch):
    monkeypatch.setitem(sys.modules, "numpy", None)
    with pytest.raises(ImportError, match=r"hyundai_kia_connect_api\[image\]"):
        render_views(_make_details(valid_angle_of_view=_calibration()), dewarp=True)


def test_render_views_degenerate_sizes_omits_views():
    views = render_views(_make_details(image_sizes=(4472, 0, 960, 0, 632, 0)))
    assert views == {}


def test_to_jpeg_bytes_roundtrip():
    from PIL import Image

    img = Image.new("RGB", (8, 8), (10, 200, 30))
    data = to_jpeg_bytes(img)
    assert data[:2] == b"\xff\xd8"  # JPEG magic
    assert _close(Image.open(io.BytesIO(data)).getpixel((0, 0)), (10, 200, 30))


def test_render_views_sizes_larger_than_image_omit_views():
    # Internally consistent sizes that exceed the real JPEG: the crop box
    # would run past the panorama edge and PIL pads out-of-bounds with black
    # — the spec omits such views instead of serving padded ones.
    from PIL import Image

    img = Image.new("RGB", (100, 50), (5, 5, 5))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=100, subsampling=0)
    views = render_views(_make_details(image_bytes=buf.getvalue()))
    assert views == {}
