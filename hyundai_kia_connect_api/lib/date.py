from datetime import datetime, date


def date_string_to_datetime(date_string: str) -> datetime:
    """
    Tries to convert a date string to a datetime object.

    It goes in the following order:
        - ISO format
        - YYYYMM
        - YYYYMMDDHHMMSS
    """
    try:
        return datetime.fromisoformat(date_string)
    except ValueError:
        try:
            return datetime.strptime(date_string, "%Y%m")
        except ValueError:
            return datetime.strptime(date_string, "%Y%m%d%H%M%S")


def date_to_year_month(d: date):
    """Format a date to YYYYMM format."""
    return d.strftime("%Y%m")


def date_to_year_month_day(d: date):
    """Format a date to YYYYMMDD format."""
    return d.strftime("%Y%m%d")
