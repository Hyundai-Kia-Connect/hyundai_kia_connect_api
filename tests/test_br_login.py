from requests import HTTPError
from requests.cookies import RequestsCookieJar

from hyundai_kia_connect_api.HyundaiBlueLinkApiBR import HyundaiBlueLinkApiBR
from hyundai_kia_connect_api.exceptions import APIError


class DummyResponse:
    def __init__(
        self,
        payload=None,
        status_code: int = 200,
        text: str | None = None,
        headers: dict | None = None,
        cookies: dict | None = None,
    ) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = text if text is not None else ""
        self.headers = headers or {}
        self.cookies = RequestsCookieJar()
        for key, value in (cookies or {}).items():
            self.cookies.set(key, value)

    def json(self):
        if self._payload is None:
            raise ValueError("No JSON payload")
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise HTTPError(f"{self.status_code} error", response=self)


def test_br_signin_returns_authorization_code(monkeypatch):
    api = HyundaiBlueLinkApiBR(region=8, brand=2)
    response = DummyResponse(
        payload={
            "redirectUrl": (
                "https://br-ccapi.hyundai.com.br/api/v1/user/oauth2/redirect"
                "?code=test-auth-code"
            )
        }
    )
    monkeypatch.setattr(api.session, "post", lambda *args, **kwargs: response)

    code = api._get_authorization_code(
        cookies={"account": "cookie-value"},
        username="user@example.com",
        password="secret-password",
    )

    assert code == "test-auth-code"


def test_br_signin_step_error_is_descriptive(monkeypatch):
    api = HyundaiBlueLinkApiBR(region=8, brand=2)
    response = DummyResponse(payload={"step": 5, "errMsg": "Password reset needed"})
    monkeypatch.setattr(api.session, "post", lambda *args, **kwargs: response)

    try:
        api._get_authorization_code(
            cookies={"account": "cookie-value"},
            username="user@example.com",
            password="secret-password",
        )
    except APIError as exc:
        assert "no redirectUrl" in str(exc)
        assert "step=5 (password change required)" in str(exc)
        assert "Password reset needed" in str(exc)
    else:
        raise AssertionError("Expected APIError for step-based sign-in response")


def test_br_signin_http_error_surfaces_errcode(monkeypatch):
    api = HyundaiBlueLinkApiBR(region=8, brand=2)
    response = DummyResponse(
        payload={"errCode": "4003", "errMsg": "Invalid values"},
        status_code=400,
        headers={"Content-Type": "application/json"},
    )
    monkeypatch.setattr(api.session, "post", lambda *args, **kwargs: response)

    try:
        api._get_authorization_code(
            cookies={"account": "cookie-value"},
            username="user@example.com",
            password="secret-password",
        )
    except APIError as exc:
        assert "HTTP 400" in str(exc)
        assert "errCode=4003" in str(exc)
        assert "Invalid values" in str(exc)
    else:
        raise AssertionError("Expected APIError for HTTP sign-in failure")
