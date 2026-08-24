"""Brazilian climate requests can use a confirmed vehicle-specific tempCode."""

import unittest
from unittest.mock import MagicMock, patch

from hyundai_kia_connect_api.ApiImpl import ClimateRequestOptions
from hyundai_kia_connect_api.HyundaiBlueLinkApiBR import HyundaiBlueLinkApiBR
from hyundai_kia_connect_api.Vehicle import Vehicle


class _Response:
    def raise_for_status(self):
        return None

    def json(self):
        return {"retCode": "S", "msgId": "test-message"}


def _api() -> HyundaiBlueLinkApiBR:
    api = object.__new__(HyundaiBlueLinkApiBR)
    api.api_v2_url = "https://example.test/api/v2/"
    api.ccsp_device_id = "test-device"
    api.session = MagicMock()
    api.session.post.return_value = _Response()
    return api


def _payload(options: ClimateRequestOptions) -> dict:
    api = _api()
    vehicle = Vehicle(id="vehicle-id", ccu_ccs2_protocol_support=0)
    token = MagicMock()
    token.device_id = "test-device"
    with (
        patch.object(api, "_ensure_control_token", return_value="control-token"),
        patch.object(api, "_get_authenticated_headers", return_value={}),
    ):
        api.start_climate(token, vehicle, options)
    return api.session.post.call_args.kwargs["json"]


class BrazilianClimateTempCodeTests(unittest.TestCase):
    def test_uses_confirmed_raw_temperature_code(self):
        payload = _payload(ClimateRequestOptions(temp_code="feh"))

        self.assertEqual(payload["tempCode"], "FEH")

    def test_keeps_legacy_direct_celsius_encoding_without_raw_code(self):
        payload = _payload(ClimateRequestOptions(set_temp=21))

        self.assertEqual(payload["tempCode"], "15H")

    def test_rejects_an_invalid_raw_temperature_code(self):
        with self.assertRaisesRegex(ValueError, "temp_code"):
            _payload(ClimateRequestOptions(temp_code="HIGH"))


if __name__ == "__main__":
    unittest.main()
