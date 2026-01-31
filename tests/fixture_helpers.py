"""Helpers for loading and discovering JSON test fixtures.

These are plain functions (not pytest fixtures) so they can be imported
directly by test modules at module level for use with ``pytest.mark.parametrize``.
"""

import json
import pathlib

FIXTURES_DIR = pathlib.Path(__file__).parent / "fixtures"


def load_fixture(filename: str) -> dict:
    """Load a JSON fixture file from the fixtures directory."""
    filepath = FIXTURES_DIR / filename
    with open(filepath, encoding="utf-8") as f:
        return json.load(f)


def discover_fixtures(prefix: str) -> list[str]:
    """Discover all fixture files matching a prefix.

    This enables parameterized tests: add a new JSON file with the right
    prefix and it will automatically be picked up by tests that use this
    helper. For example, all files matching ``us_*_cached.json`` will be
    found by ``discover_fixtures("us_")``.
    """
    return sorted(p.name for p in FIXTURES_DIR.glob(f"{prefix}*.json"))


def get_fixture_expected(fixture_data: dict) -> dict:
    """Return the ``_fixture_meta.expected`` block from a fixture."""
    return fixture_data.get("_fixture_meta", {}).get("expected", {})


def get_fixture_meta(fixture_data: dict) -> dict:
    """Return the ``_fixture_meta`` block from a fixture."""
    return fixture_data.get("_fixture_meta", {})
