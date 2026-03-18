"""Token.py"""

# pylint:disable=invalid-name

import datetime as dt
from dataclasses import dataclass, asdict


@dataclass
class Token:
    """Token"""

    username: str = None
    password: str = None
    access_token: str = None
    refresh_token: str = None
    device_id: str = None
    # Access Token expiry:
    valid_until: dt.datetime = dt.datetime.min
    stamp: str = None
    pin: str = None

    def to_dict(self) -> dict:
        """Convert Token to a JSON‑serializable dict."""
        data = asdict(self)

        # Convert datetime to ISO string
        data["valid_until"] = self.valid_until.isoformat()

        return data

    @classmethod
    def from_dict(cls, data: dict) -> "Token":
        """Create a Token instance from a dict."""
        # Parse datetime from ISO string
        valid_until = data.get("valid_until")
        if isinstance(valid_until, str):
            valid_until = dt.datetime.fromisoformat(valid_until)

        return cls(
            username=data.get("username"),
            password=data.get("password"),
            access_token=data.get("access_token"),
            refresh_token=data.get("refresh_token"),
            device_id=data.get("device_id"),
            valid_until=valid_until,
            stamp=data.get("stamp"),
            pin=data.get("pin"),
        )
