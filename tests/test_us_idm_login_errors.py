import datetime as dt
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from hyundai_kia_connect_api.exceptions import (
    AuthenticationBlockedError,
    AuthenticationError,
    AuthenticationInvalidCredentialsError,
    ServiceTemporaryUnavailable,
)
from hyundai_kia_connect_api.HyundaiBlueLinkApiUSA import (
    HyundaiBlueLinkApiUSA,
    _check_response_for_errors,
)
from hyundai_kia_connect_api.Token import Token

INVALID_CREDENTIALS = "Incorrect username or password"
LOCKED_PERMANENTLY = "Your password has been locked permanently."
LOCKED_TEMPORARILY = (
    "Your account has been locked because of invalid login attempts. "
    "Please wait 5 minutes before trying to login again."
)
OTHER_IDM_ERROR = (
    "We're sorry, but we could not complete your request. Please try again later."
)


def _idm_error(message: str) -> dict[str, Any]:
    return {
        "errorCode": 502,
        "errorMessage": message,
        "systemName": "IDM",
        "functionName": "customerLogin",
    }


def _response(body: dict[str, Any]) -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = body
    return resp


def _make_token() -> Token:
    return Token(
        username="user@test.com",
        password="OldPassword123!",
        access_token="old-access-token",
        refresh_token="OLDREFRESHTOKEN1234567890123456789012345678",
        valid_until=dt.datetime.now(dt.UTC) + dt.timedelta(hours=1),
        pin="1234",
    )


def _make_api() -> HyundaiBlueLinkApiUSA:
    return HyundaiBlueLinkApiUSA(region=3, brand=2, language="en")


def test_idm_incorrect_password_raises_invalid_credentials() -> None:
    with pytest.raises(AuthenticationInvalidCredentialsError, match="IDM"):
        _check_response_for_errors(_idm_error(INVALID_CREDENTIALS))


@pytest.mark.parametrize("message", [LOCKED_PERMANENTLY, LOCKED_TEMPORARILY])
def test_idm_locked_raises_blocked(message: str) -> None:
    with pytest.raises(AuthenticationBlockedError, match="locked"):
        _check_response_for_errors(_idm_error(message))


def test_idm_other_error_stays_service_unavailable() -> None:
    with pytest.raises(ServiceTemporaryUnavailable) as exc_info:
        _check_response_for_errors(_idm_error(OTHER_IDM_ERROR))
    assert not isinstance(exc_info.value, AuthenticationError)


def test_login_wrong_password_raises_authentication_error() -> None:
    api = _make_api()
    with (
        patch.object(
            api.session, "post", return_value=_response(_idm_error(INVALID_CREDENTIALS))
        ),
        pytest.raises(AuthenticationInvalidCredentialsError),
    ):
        api.login("user@test.com", "OldPassword123!", "1234")


@pytest.mark.parametrize(
    "message", [INVALID_CREDENTIALS, LOCKED_PERMANENTLY, LOCKED_TEMPORARILY]
)
def test_refresh_rejected_credentials_does_not_fall_back_to_login(
    message: str,
) -> None:
    api = _make_api()
    with (
        patch.object(
            api.session, "post", return_value=_response(_idm_error(message))
        ) as mock_post,
        pytest.raises(AuthenticationError),
    ):
        api.refresh_access_token(_make_token())
    mock_post.assert_called_once()


def test_refresh_other_idm_error_falls_back_to_login() -> None:
    api = _make_api()
    login_ok = _response(
        {
            "access_token": "from-login",
            "refresh_token": "new-refresh-token",
            "expires_in": "1799",
        }
    )
    with patch.object(
        api.session,
        "post",
        side_effect=[_response(_idm_error(OTHER_IDM_ERROR)), login_ok],
    ) as mock_post:
        result = api.refresh_access_token(_make_token())
    assert mock_post.call_count == 2
    assert result.access_token == "from-login"
