"""Token serialization."""

import datetime as dt

from hyundai_kia_connect_api.Token import Token


def test_from_dict_without_valid_until_is_expired() -> None:
    """A stored token without valid_until falls back to the field default
    (expired, forcing a re-login) instead of None."""
    token = Token.from_dict({"username": "user", "password": "pass"})
    assert token.valid_until == dt.datetime.min


def test_round_trip_keeps_valid_until() -> None:
    valid_until = dt.datetime(2026, 11, 1, 12, 0, tzinfo=dt.UTC)
    token = Token(username="user", password="pass", valid_until=valid_until)
    assert Token.from_dict(token.to_dict()).valid_until == valid_until
