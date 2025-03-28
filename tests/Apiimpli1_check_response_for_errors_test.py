import pytest

from hyundai_kia_connect_api.ApiImplType1 import ApiImplType1

from hyundai_kia_connect_api.exceptions import (
    RateLimitingError,
    InvalidAPIResponseError,
    APIError,
)


def test_invalid_api_response():
    response = {"invalid": "response"}
    api = ApiImplType1()
    with pytest.raises(InvalidAPIResponseError):
        api._check_response_for_errors(response)


def test_rate_limiting():
    response = {
        "retCode": "F",
        "resCode": "5091",
        "resMsg": "Exceeds number of requests - Exceeds Number of Requests.",
    }
    api = ApiImplType1()
    with pytest.raises(RateLimitingError):
        api._check_response_for_errors(response)


def test_unknown_error_code():
    response = {"retCode": "F", "resCode": "9999", "resMsg": "New error"}
    api = ApiImplType1()
    with pytest.raises(APIError):
        api._check_response_for_errors(response)
