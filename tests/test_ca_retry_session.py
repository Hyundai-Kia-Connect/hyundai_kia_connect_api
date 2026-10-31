"""CA RetrySession inherits ApiImplSession's default timeout."""

from unittest.mock import MagicMock, patch

import pytest
import requests

from hyundai_kia_connect_api.ApiImpl import ApiImplSession
from hyundai_kia_connect_api.exceptions import RequestTimeoutError
from hyundai_kia_connect_api.KiaUvoApiCA import RetrySession

URL = "https://mybluelink.ca/tods/api/v2/login"


def test_post_applies_default_timeout():
    session = RetrySession()
    with patch.object(requests.Session, "request") as base:
        session.post(URL, json={})

    assert base.call_args.kwargs["timeout"] == (
        ApiImplSession.HTTP_CONNECT_TIMEOUT,
        ApiImplSession.HTTP_READ_TIMEOUT,
    )


def test_timeout_raises_request_timeout_error_without_retry():
    session = RetrySession(max_retries=4)
    with (
        patch.object(
            requests.Session, "request", side_effect=requests.exceptions.ReadTimeout
        ) as base,
        pytest.raises(RequestTimeoutError),
    ):
        session.post(URL, json={})

    assert base.call_count == 1


def test_connection_error_is_still_retried():
    session = RetrySession(max_retries=3, delay=0)
    response = MagicMock()
    with (
        patch.object(
            requests.Session,
            "request",
            side_effect=[requests.exceptions.ConnectionError, response],
        ) as base,
        patch("hyundai_kia_connect_api.KiaUvoApiCA.time.sleep"),
    ):
        assert session.post(URL, json={}) is response

    assert base.call_count == 2
