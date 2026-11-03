"""A real generated JPEG survives the Japan camera-to-view pipeline offline."""

import base64
import io
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest

from hyundai_kia_connect_api.exceptions import NoDataFound
from hyundai_kia_connect_api.japan_svm import JapanSVM
from hyundai_kia_connect_api.svm_image import render_views
from hyundai_kia_connect_api.Vehicle import Vehicle

Image = pytest.importorskip("PIL.Image")


@pytest.mark.parametrize("line_wrapped", [False, True])
def test_completed_camera_capture_decodes_and_renders_all_five_views(
    monkeypatch, line_wrapped
):
    # The segments have different colours and a different top width: this
    # independently verifies crop offsets and does not merely round-trip bytes.
    camera_width, top_width, height = 24, 20, 16
    colors = [
        (240, 20, 20),
        (20, 240, 20),
        (20, 20, 240),
        (240, 240, 20),
        (20, 240, 240),
    ]
    composite = Image.new("RGB", (4 * camera_width + top_width, height))
    for index, color in enumerate(colors):
        width = top_width if index == 4 else camera_width
        composite.paste(
            color, (index * camera_width, 0, index * camera_width + width, height)
        )
    encoded_image = io.BytesIO()
    composite.save(encoded_image, format="JPEG", quality=100, subsampling=0)
    encoded = base64.b64encode(encoded_image.getvalue()).decode("ascii")
    if line_wrapped:
        encoded = "\r\n".join(
            encoded[index : index + 76] for index in range(0, len(encoded), 76)
        )
    response = {
        "resMsg": {
            "scsDetail": {
                "svmImage": encoded,
                "imageSize": [
                    composite.width,
                    height,
                    camera_width,
                    height,
                    top_width,
                    height,
                ],
                # Missing GPS time is legitimate; image rendering must not depend on it.
                "gpsDetail": {},
            }
        }
    }
    adapter = JapanSVM()
    adapter.data_timezone = ZoneInfo("Asia/Tokyo")
    adapter._control_headers = Mock(return_value={"Authorization": "control"})
    adapter._vehicle_headers = Mock(return_value={"Authorization": "oauth"})
    adapter._payload = lambda body: body["resMsg"]
    adapter._request = Mock(
        side_effect=[{"msgId": "synthetic-capture"}, NoDataFound("pending"), response]
    )
    monkeypatch.setattr("hyundai_kia_connect_api.japan_svm.time.sleep", lambda _: None)
    vehicle = Vehicle(id="synthetic-car", supports_svm=True)
    details = adapter.request_svm_capture(None, vehicle, True)
    assert details.captured_at is None
    assert adapter.get_svm_details(None, vehicle) is details
    assert adapter.svm_diagnostics["stage"] == "complete"
    assert adapter.svm_diagnostics["poll_count"] == 2
    assert sum(call.args[0] == "POST" for call in adapter._request.call_args_list) == 1
    assert all(
        call.kwargs.get("params") == {"msgId": "synthetic-capture"}
        for call in adapter._request.call_args_list[1:]
    )
    views = render_views(details)
    assert set(views) == {"front", "rear", "left", "right", "top"}
    for index, key in enumerate(("front", "rear", "left", "right", "top")):
        with Image.open(io.BytesIO(views[key])) as view:
            assert view.format == "JPEG"
            assert view.size == (top_width if key == "top" else camera_width, height)
            actual = view.getpixel((view.width // 2, view.height // 2))
            assert all(
                abs(component - expected) <= 3
                for component, expected in zip(actual, colors[index])
            )
    assert details.raw_metadata["scsDetail"]["svmImage"] == "<redacted>"
