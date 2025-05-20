from datetime import date, datetime


def parse_date_from_string(date_string: str) -> date:
    try:
        return date.fromisoformat(date_string)
    except ValueError:
        return datetime.strptime(date_string, "%Y%m").date()
