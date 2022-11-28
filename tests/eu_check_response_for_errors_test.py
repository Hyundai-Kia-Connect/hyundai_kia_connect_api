import pytest

from hyundai_kia_connect_api.KiaUvoApiEU import _check_response_for_errors
from hyundai_kia_connect_api.exceptions import *


def test_invalid_api_response():
    response = {"invalid": "response"}
    with pytest.raises(InvalidAPIResponseError):
        _check_response_for_errors(response)


def test_rate_limiting():
    response = {
        "retCode": "F",
        "resCode": "5091",
        "resMsg": "Exceeds number of requests - Exceeds Number of Requests.",
    }
    with pytest.raises(RateLimitingError):
        _check_response_for_errors(response)


def test_unknown_error_code():
    response = {"retCode": "F", "resCode": "9999", "resMsg": "New error"}
    with pytest.raises(APIError):
        _check_response_for_errors(response)
