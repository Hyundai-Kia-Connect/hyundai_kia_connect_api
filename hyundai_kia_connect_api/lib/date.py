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
    return d.strftime("%Y%m")
