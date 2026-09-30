"""Fixture-driven parser tests, run against every file in ``tests/fixtures/``.

Each fixture names its parser in ``_fixture_meta.parser`` (see
``tests/fixture_helpers.PARSERS``), so dropping in a new JSON file is all it
takes to cover a new vehicle. Every fixture gets:

* ``test_fixture_meta`` — the metadata is well-formed;
* ``test_expected_fields`` — every ``_fixture_meta.expected`` value matches the
  parsed ``Vehicle`` (hand-picked, reviewed values);
* ``test_raw_payload_retained`` — the parser keeps the raw payload on
  ``vehicle.data``;
* ``test_vehicle_snapshot`` — the full parsed ``Vehicle`` matches the syrupy
  snapshot, so any change to any field shows up as a diff.

To update snapshots after an intentional change::

    pytest tests/test_fixture_parsing.py --snapshot-update
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.fixture_helpers import (
    PARSERS,
    discover_fixtures,
    fixture_parser,
    get_fixture_meta,
    load_fixture,
    parse_fixture,
)
from tests.vehicle_snapshot_serializer import serialize_value, vehicle_to_dict

if TYPE_CHECKING:
    from syrupy.assertion import SnapshotAssertion

FIXTURE_FILES = discover_fixtures()

REQUIRED_META_KEYS = {"parser", "description"}

pytestmark = pytest.mark.parametrize("fixture_file", FIXTURE_FILES)


def test_fixture_meta(fixture_file):
    meta = get_fixture_meta(load_fixture(fixture_file))
    missing = REQUIRED_META_KEYS - meta.keys()
    assert not missing, f"_fixture_meta is missing {sorted(missing)}"
    assert meta["parser"] in PARSERS, (
        f"unknown parser {meta['parser']!r}; expected one of {sorted(PARSERS)}"
    )


def test_expected_fields(fixture_file):
    expected = get_fixture_meta(load_fixture(fixture_file)).get("expected")
    if not expected:
        pytest.skip("fixture has no _fixture_meta.expected block (snapshot only)")
    vehicle, _ = parse_fixture(fixture_file)
    actual = {name: serialize_value(getattr(vehicle, name)) for name in expected}
    assert actual == expected


def test_raw_payload_retained(fixture_file):
    vehicle, payload = parse_fixture(fixture_file)
    key = fixture_parser(fixture_file).raw_data_key
    if key is None:
        assert vehicle.data is payload
    else:
        assert vehicle.data[key] is payload[key]


def test_vehicle_snapshot(fixture_file, snapshot: SnapshotAssertion):
    vehicle, _ = parse_fixture(fixture_file)
    assert vehicle_to_dict(vehicle) == snapshot
