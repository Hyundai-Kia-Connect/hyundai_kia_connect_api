"""Tests for the refresh_access_token Stamp hook (kia_uvo #1778, AU)."""

from hyundai_kia_connect_api.KiaUvoApiCN import KiaUvoApiCN
from hyundai_kia_connect_api.KiaUvoApiIN import KiaUvoApiIN


def test_cn_refresh_hook_default_empty() -> None:
    """CN inherits the base hook and must not send Stamp on refresh."""
    api = KiaUvoApiCN(region=4, brand=1, language="en")
    assert api._refresh_access_token_headers() == {}


def test_in_refresh_hook_default_empty() -> None:
    """IN inherits the base hook and must not send Stamp on refresh."""
    api = KiaUvoApiIN(brand=2)
    assert api._refresh_access_token_headers() == {}
