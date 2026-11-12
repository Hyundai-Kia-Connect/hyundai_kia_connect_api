"""EU OneApp signin must send a current browser User-Agent.

Kia's signin endpoint started answering the 2012-era Chrome/18 string with a
302 back to the login page on 2026-10-22, so every EU password login failed
before any token exchange and Home Assistant only saw a generic auth error.
A current Android Chrome string is accepted (see #1397).
"""

from __future__ import annotations

from typing import Any, ClassVar
from unittest.mock import patch

import pytest

from hyundai_kia_connect_api.ApiImpl import ApiImplSession
from hyundai_kia_connect_api.exceptions import AuthenticationError
from hyundai_kia_connect_api.GspaApiEU import USER_AGENT_MOZILLA as GSPA_USER_AGENT
from hyundai_kia_connect_api.KiaCciApiEU import KiaCciApiEU
from hyundai_kia_connect_api.KiaUvoApiEU import USER_AGENT_MOZILLA as EU_USER_AGENT
from hyundai_kia_connect_api.KiaUvoApiEU import KiaUvoApiEU

APP_SUFFIX = "_CCS_APP_AOS"
WAF_BLOCK_URL = "https://idpconnect-eu.kia.com/error?status=400"


class _BlockedAuthorizeResponse:
    """Authorize answer that trips the WAF check right after the session exists."""

    status_code = 200
    text = ""
    url = WAF_BLOCK_URL
    headers: ClassVar[dict[str, str]] = {}

    def json(self) -> dict[str, Any]:
        return {}


def _capture_signin_user_agent(api: Any, module: str, method: str) -> str:
    """Run the login until the first request and return its User-Agent."""
    captured: dict[str, str] = {}

    class _SpySession(ApiImplSession):
        def request(self, method: str, url: str, **kwargs: Any) -> Any:
            captured["user_agent"] = str(self.headers.get("User-Agent", ""))
            return _BlockedAuthorizeResponse()

    with (
        patch(f"{module}.ApiImplSession", _SpySession),
        pytest.raises(AuthenticationError),
    ):
        getattr(api, method)("user@example.invalid", "secret", "device-id")

    assert "user_agent" in captured, "login never issued a request"
    return captured["user_agent"]


def test_both_eu_paths_share_one_user_agent() -> None:
    # GspaApiEU carries its own copy of the constant; they must not drift.
    assert EU_USER_AGENT == GSPA_USER_AGENT


# region/brand take the numeric keys VehicleManager passes (1 = Europe, Kia).
@pytest.mark.parametrize(
    ("region", "api_class", "module", "method"),
    [
        (
            1,
            KiaUvoApiEU,
            "hyundai_kia_connect_api.KiaUvoApiEU",
            "_login_with_password_cci",
        ),
        (9, KiaCciApiEU, "hyundai_kia_connect_api.GspaApiEU", "_login_with_password"),
    ],
    ids=["europe", "europe-cci-gspa"],
)
def test_signin_sends_current_browser_user_agent(
    region: int, api_class: Any, module: str, method: str
) -> None:
    user_agent = _capture_signin_user_agent(api_class(region, 1, "en"), module, method)

    # The composition with the app marker stays as before.
    assert user_agent.endswith(APP_SUFFIX)

    # Kia rejects the pre-2020 browser strings.
    assert "Galaxy Nexus" not in user_agent
    assert "AppleWebKit/535" not in user_agent

    assert "Chrome/" in user_agent
    chrome_major = user_agent.split("Chrome/")[1].split(".")[0]
    assert int(chrome_major) >= 100
