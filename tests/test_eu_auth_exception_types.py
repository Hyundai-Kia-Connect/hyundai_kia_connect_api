"""Check typed EU authentication errors and CCS exchange failures."""

from unittest.mock import MagicMock, patch

import pytest

from hyundai_kia_connect_api.exceptions import (
    AuthenticationBlockedError,
    AuthenticationCciTokenExchangeError,
    AuthenticationCcsTokenExchangeError,
    AuthenticationCertificateError,
    AuthenticationError,
    AuthenticationRedirectError,
    AuthenticationSigninError,
    AuthenticationTokenExchangeError,
)
from hyundai_kia_connect_api.HyundaiCciApiEU import HyundaiCciApiEU


@pytest.mark.parametrize(
    "error_type",
    [
        AuthenticationBlockedError,
        AuthenticationCciTokenExchangeError,
        AuthenticationCcsTokenExchangeError,
        AuthenticationCertificateError,
        AuthenticationRedirectError,
        AuthenticationSigninError,
        AuthenticationTokenExchangeError,
    ],
)
def test_child_error_is_authentication_error(
    error_type: type[AuthenticationError],
) -> None:
    assert issubclass(error_type, AuthenticationError)


@pytest.mark.parametrize("status", [200, 503])
def test_ccs_exchange_failure_has_typed_exception(status: int) -> None:
    api = HyundaiCciApiEU(region=9, brand=2, language="en")
    response = MagicMock(status_code=status, text="upstream failure")
    response.json.return_value = {}  # HTTP 200 but no CCS access token

    with (
        patch.object(api, "_cci_fp_headers", return_value={}),
        patch("hyundai_kia_connect_api.cci_auth.requests.post", return_value=response),
        pytest.raises(AuthenticationCcsTokenExchangeError),
    ):
        api._exchange_ccs_token("test-device", "access", "non-ccs", "exchangeable")
