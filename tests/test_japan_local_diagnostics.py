"""Local protocol summaries never expose session identities or image data."""

from types import SimpleNamespace

from hyundai_kia_connect_api.HyundaiBlueLinkApiJP import HyundaiBlueLinkApiJP


def test_local_diagnostics_are_bounded_and_do_not_serialize_authentication():
    api = HyundaiBlueLinkApiJP.__new__(HyundaiBlueLinkApiJP)
    helper = SimpleNamespace(diagnostics={"closed": True, "result_messages": 0})
    api._mqtt_sessions = {
        str(index): (("synthetic-secret-token",), helper) for index in range(12)
    }
    api._japan_svm_cache = {"synthetic-vehicle": b"synthetic-image"}
    summary = api.get_local_diagnostics()
    assert len(summary["mqtt_sessions"]) == 10
    assert summary["svm_images_cached"] == 1
    assert "synthetic" not in repr(summary)


def test_local_diagnostics_before_any_capture_or_connection():
    api = HyundaiBlueLinkApiJP.__new__(HyundaiBlueLinkApiJP)
    api._mqtt_sessions = {}
    assert api.get_local_diagnostics() == {
        "mqtt_sessions": [],
        "svm_images_cached": 0,
        "last_command": {},
        "last_camera_capture": {"stage": "idle"},
    }
