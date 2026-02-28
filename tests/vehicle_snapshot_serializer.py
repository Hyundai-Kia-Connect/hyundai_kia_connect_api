"""Custom serializer that converts a Vehicle dataclass to a sorted dict for snapshots.

Excludes ``vehicle.data`` (the raw API response, already captured in fixtures)
and converts non-JSON-native types (datetime, etc.) to strings so the snapshot
is deterministic and human-readable.
"""

import dataclasses
import datetime


def vehicle_to_dict(vehicle) -> dict:
    """Return a sorted dict of all public Vehicle fields suitable for snapshotting.

    Private backing fields (``_foo``) that have a corresponding public property
    are excluded — the property value is captured instead via ``getattr``.
    """
    result = {}

    # Collect all field names defined on the dataclass
    field_names = [f.name for f in dataclasses.fields(vehicle)]

    # Build set of public property names from the class
    property_names = {
        name
        for name in dir(type(vehicle))
        if isinstance(getattr(type(vehicle), name, None), property)
    }

    # Track which private fields are backing a property so we skip them
    backed_private = set()
    for prop in property_names:
        backing = f"_{prop}"
        if backing in field_names:
            backed_private.add(backing)

    for name in field_names:
        # Skip the raw data blob — it's the fixture input, not parsed output
        if name == "data":
            continue
        # Skip private backing fields that have a property
        if name in backed_private:
            continue
        # Also skip internal value/unit split fields (e.g. _odometer_value)
        if name.startswith("_"):
            continue

        result[name] = _serialize_value(getattr(vehicle, name))

    # Add property values
    for prop in sorted(property_names):
        result[prop] = _serialize_value(getattr(vehicle, prop))

    return dict(sorted(result.items()))


def _serialize_value(value):
    """Convert a value to a JSON-friendly representation."""
    if value is None:
        return None
    if isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, datetime.datetime):
        # Normalize to UTC for consistent snapshots across timezones
        if value.tzinfo is not None:
            value = value.astimezone(datetime.timezone.utc)
        else:
            # Assume naive datetimes are in UTC
            value = value.replace(tzinfo=datetime.timezone.utc)
        return value.isoformat()
    if isinstance(value, datetime.time):
        return value.isoformat()
    if isinstance(value, datetime.timezone):
        return str(value)
    if isinstance(value, (list, tuple)):
        return [_serialize_value(v) for v in value]
    if isinstance(value, dict):
        return {k: _serialize_value(v) for k, v in sorted(value.items())}
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            f.name: _serialize_value(getattr(value, f.name))
            for f in dataclasses.fields(value)
        }
    return str(value)
