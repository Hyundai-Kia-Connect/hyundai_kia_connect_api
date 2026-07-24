"""Tests for Brazilian Hyundai vehicle status requests."""

from unittest.mock import MagicMock

from hyundai_kia_connect_api.HyundaiBlueLinkApiBR import HyundaiBlueLinkApiBR
from hyundai_kia_connect_api.Token import Token


def _response(status_code: int, payload: dict) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = payload
    return response


def test_status_retries_with_ccs2_header_after_5031() -> None:
    """Retry status when Brazil requires CCS2 despite advertising legacy."""
    api = HyundaiBlueLinkApiBR(region=8, brand=2)
    unavailable = _response(
        503,
        {
            "retCode": "F",
            "resCode": "5031",
            "resMsg": "Unavailable remote control - Service Temporary Unavailable",
        },
    )
    state = {"fuelLevel": 75}
    success = _response(200, {"retCode": "S", "resCode": "0000", "resMsg": state})
    api.session.get = MagicMock(  # type: ignore[assignment]
        side_effect=[unavailable, success]
    )
    vehicle = MagicMock(id="vehicle-id", ccu_ccs2_protocol_support=0)

    result = api._get_vehicle_state(Token(access_token="token"), vehicle)

    assert result == state
    assert api.session.get.call_count == 2
    retry_headers = api.session.get.call_args_list[1].kwargs["headers"]
    assert api.api_headers["ccuCCS2ProtocolSupport"] == "0"
    assert retry_headers["ccuCCS2ProtocolSupport"] == "1"
    success.raise_for_status.assert_called_once_with()


def test_status_does_not_retry_unrelated_503() -> None:
    """Do not mask unrelated server failures with a protocol retry."""
    api = HyundaiBlueLinkApiBR(region=8, brand=2)
    unavailable = _response(
        503,
        {"retCode": "F", "resCode": "5030", "resMsg": "Maintenance"},
    )
    api.session.get = MagicMock(return_value=unavailable)  # type: ignore[assignment]
    vehicle = MagicMock(id="vehicle-id", ccu_ccs2_protocol_support=0)

    api._get_vehicle_state(Token(access_token="token"), vehicle)

    api.session.get.assert_called_once()
    unavailable.raise_for_status.assert_called_once_with()
