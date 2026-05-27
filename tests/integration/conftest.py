"""Integration test fixtures — real API calls with real credentials.

Requires .env file in tests/integration/ with HYUNDAI_USERNAME, HYUNDAI_PASSWORD,
HYUNDAI_PIN (and optionally KIA_* variants).

Run:  pytest -m integration tests/integration/ -v
"""

import os

import pytest
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))


def _get_creds(prefix: str):
    """Load credentials from env vars for a brand prefix (HYUNDAI or KIA)."""
    username = os.environ.get(f"{prefix}_USERNAME")
    password = os.environ.get(f"{prefix}_PASSWORD")
    pin = os.environ.get(f"{prefix}_PIN")
    if not all([username, password, pin]):
        pytest.skip(f"Set {prefix}_USERNAME, {prefix}_PASSWORD, {prefix}_PIN to run")
    return username, password, pin


@pytest.fixture(scope="session")
def hyundai_api_and_token():
    """Login and return (api, token) — login() returns Token, not stored on self."""
    from hyundai_kia_connect_api.KiaUvoApiEU import KiaUvoApiEU

    username, password, pin = _get_creds("HYUNDAI")
    api = KiaUvoApiEU(region=1, brand=2, language="en")
    token = api.login(username=username, password=password, pin=pin)
    return api, token


@pytest.fixture(scope="session")
def hyundai_api(hyundai_api_and_token):
    """KiaUvoApiEU (Hyundai) instance."""
    return hyundai_api_and_token[0]


@pytest.fixture(scope="session")
def hyundai_token(hyundai_api_and_token):
    """Authenticated Token from session login."""
    return hyundai_api_and_token[1]


@pytest.fixture(scope="session")
def hyundai_vehicle(hyundai_api, hyundai_token):
    """First vehicle from account."""
    vehicles = hyundai_api.get_vehicles(hyundai_token)
    if not vehicles:
        pytest.skip("No vehicles registered on this account")
    return vehicles[0]


@pytest.fixture(scope="session")
def kia_api_and_token():
    """Login and return (api, token) for Kia."""
    from hyundai_kia_connect_api.KiaUvoApiEU import KiaUvoApiEU

    username, password, pin = _get_creds("KIA")
    api = KiaUvoApiEU(region=1, brand=1, language="en")
    token = api.login(username=username, password=password, pin=pin)
    return api, token
