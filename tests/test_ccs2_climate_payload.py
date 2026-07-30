"""Tests for the CCS2 start_climate payload (ApiImplType1, EU/AU/IN/CN/BR).

Covers the ``sideRearMirrorHeating`` correctness bug in the CCS2 branch of
``ApiImplType1.start_climate``: it was hardcoded to ``1``, so the rear-window
+ side-mirror heaters turned on for every climate start regardless of the
``heating`` option. It must be derived from ``options.heating`` (on for
1/2/4, off for 0 and steering-only 3), mirroring the Kia USA logic. See
Hyundai-Kia-Connect/hyundai_kia_connect_api#1195 and
Hyundai-Kia-Connect/kia_uvo#1739.
"""

from unittest.mock import MagicMock, patch

from hyundai_kia_connect_api.ApiImpl import ClimateRequestOptions
from hyundai_kia_connect_api.KiaUvoApiEU import KiaUvoApiEU
from hyundai_kia_connect_api.Token import Token
from hyundai_kia_connect_api.Vehicle import Vehicle


class _FakeResponse:
    """Minimal fake for requests.Response with the fields start_climate reads."""

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def _make_api():
    """Create a KiaUvoApiEU (CCS2-capable, base _get_drv_seat_loc) sans network."""
    api = object.__new__(KiaUvoApiEU)
    api.SPA_API_URL = "https://example/api/v1/spa/"
    api.SPA_API_URL_V2 = "https://example/api/v2/spa/"
    api.session = MagicMock()
    return api


def _make_vehicle(ccs2: bool = True) -> Vehicle:
    v = Vehicle()
    v.id = "test-vehicle-id"
    v.name = "Kona EV"
    v.ccu_ccs2_protocol_support = ccs2
    return v


def _make_token():
    return MagicMock(spec=Token)


def _post_payload(api, vehicle, options):
    """Call start_climate on the CCS2 path and return the sent JSON payload."""
    api.session.post.return_value = _FakeResponse(
        {"retCode": "S", "resCode": "0000", "msgId": "test-msg"}
    )
    with (
        patch.object(api, "_get_control_headers", return_value={}),
    ):
        api.start_climate(_make_token(), vehicle, options)
    return api.session.post.call_args.kwargs["json"]


class TestSideRearMirrorHeating:
    """sideRearMirrorHeating must follow options.heating, not be hardcoded 1."""

    def setup_method(self):
        self.api = _make_api()
        self.vehicle = _make_vehicle(ccs2=True)

    def _heating(self, options):
        return _post_payload(self.api, self.vehicle, options)["sideRearMirrorHeating"]

    def test_heating_off_disables_rear_mirror(self):
        options = ClimateRequestOptions(set_temp=21, heating=0)
        assert self._heating(options) == 0

    def test_heating_rear_window_only_enables(self):
        options = ClimateRequestOptions(set_temp=21, heating=2)
        assert self._heating(options) == 1

    def test_heating_steering_only_disables_rear_mirror(self):
        options = ClimateRequestOptions(set_temp=21, heating=3)
        assert self._heating(options) == 0

    def test_heating_steering_side_back_enables(self):
        options = ClimateRequestOptions(set_temp=21, heating=4)
        assert self._heating(options) == 1

    def test_heating_eu_steering_side_back_enables(self):
        options = ClimateRequestOptions(set_temp=21, heating=1)
        assert self._heating(options) == 1
