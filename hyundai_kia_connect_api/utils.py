# pylint:disable=bare-except,missing-function-docstring,invalid-name
"""utils.py"""

import datetime
import re


def get_child_value(data, key):
    value = data
    for x in key.split("."):
        try:
            value = value[x]
        except Exception:
            try:
                value = value[int(x)]
            except Exception:
                value = None
    return value


def get_float(value):
    if value is None:
        return None
    if isinstance(value, float):
        return value
    if isinstance(value, int):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return value  # original fallback
    return value  # original fallback


def get_hex_temp_into_index(value):
    if value is not None:
        value = value.replace("H", "")
        value = int(value, 16)
        return value
    else:
        return None


def get_index_into_hex_temp(value):
    if value is not None:
        value = hex(value).split("x")
        value = value[1] + "H"
        value = value.zfill(3).upper()
        return value
    else:
        return None


def parse_datetime(value, timezone) -> datetime.datetime:
    if value is None:
        return datetime.datetime(2000, 1, 1, tzinfo=timezone)

    value = value.replace("-", "").replace("T", "").replace(":", "").replace("Z", "")
    m = re.match(r"(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})", value)
    return datetime.datetime(
        year=int(m.group(1)),
        month=int(m.group(2)),
        day=int(m.group(3)),
        hour=int(m.group(4)),
        minute=int(m.group(5)),
        second=int(m.group(6)),
        tzinfo=timezone,
    )
