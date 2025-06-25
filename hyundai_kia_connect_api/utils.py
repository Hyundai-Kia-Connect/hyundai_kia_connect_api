# pylint:disable=bare-except,missing-function-docstring,invalid-name,broad-exception-caught
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

    # Try parsing the new format: Tue, 24 Jun 2025 16:18:10 GMT
    try:
        dt_object = datetime.datetime.strptime(value, "%a, %d %b %Y %H:%M:%S GMT")

        if timezone:
            # First, make it aware of UTC since 'GMT' implies UTC
            utc_dt = dt_object.replace(tzinfo=datetime.timezone.utc)
            # Then convert to the target timezone
            return utc_dt.astimezone(timezone)
        else:
            return dt_object
    except ValueError:
        # If the new format parsing fails, try the old format
        value = (
            value.replace("-", "").replace("T", "").replace(":", "").replace("Z", "")
        )
        m = re.match(r"(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})", value)
        if m:
            return datetime.datetime(
                year=int(m.group(1)),
                month=int(m.group(2)),
                day=int(m.group(3)),
                hour=int(m.group(4)),
                minute=int(m.group(5)),
                second=int(m.group(6)),
                tzinfo=timezone,
            )
        else:
            raise ValueError(f"Unable to parse datetime value: {value}")


def get_safe_local_datetime(date: datetime) -> datetime:
    """get safe local datetime"""
    if date is not None and hasattr(date, "tzinfo") and date.tzinfo is not None:
        date = date.astimezone()
    return date
