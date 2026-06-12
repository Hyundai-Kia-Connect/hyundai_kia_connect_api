"""Tests for HyundaiBlueLinkApiUSA._check_response_for_errors.

Covers:
- 502 errorCode (HATA server error) raises APIError, NOT AuthenticationError
- int and string errorCodes are both handled
- Responses without errorCode pass through
"""

import pytest

from hyundai_kia_connect_api.HyundaiBlueLinkApiUSA import _check_response_for_errors
from hyundai_kia_connect_api.exceptions import APIError


class TestCheckResponseForErrors:
    def test_no_error_code_passes(self):
        _check_response_for_errors({"access_token": "abc123"})

    def test_502_int_raises_api_error(self):
        """HATA returns errorCode as int 502 (not string '502')."""
        with pytest.raises(APIError, match="502"):
            _check_response_for_errors(
                {"errorCode": 502, "errorMessage": "Server error"}
            )

    def test_502_string_raises_api_error(self):
        with pytest.raises(APIError, match="502"):
            _check_response_for_errors(
                {"errorCode": "502", "errorMessage": "Server error"}
            )

    def test_502_never_raises_authentication_error(self):
        """502 from HATA is a server error, not an auth failure."""
        for code in [502, "502"]:
            with pytest.raises(APIError):
                _check_response_for_errors(
                    {"errorCode": code, "errorMessage": "Server error"}
                )

    def test_unknown_error_code_raises_api_error(self):
        with pytest.raises(APIError, match="999"):
            _check_response_for_errors({"errorCode": 999, "errorMessage": "Unknown"})

    def test_real_hata_502_response(self):
        """Exact response structure from jdhume's Genesis USA diagnostics."""
        response = {
            "errorSubCode": "GEN",
            "systemName": "HATA",
            "functionName": "remoteVehicleStatus",
            "errorSubMessage": (
                "HATA remoteVehicleStatus service failed while "
                "performing the operation RemoteVehicleStatus"
            ),
            "errorMessage": (
                "We're sorry, but we could not complete your request. "
                "Please try again later."
            ),
            "errorCode": 502,
            "serviceName": "RemoteVehicleStatus",
        }
        with pytest.raises(APIError, match="502"):
            _check_response_for_errors(response)
