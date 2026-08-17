"""Token.py"""

# pylint:disable=invalid-name

import datetime as dt
from dataclasses import asdict, dataclass


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
    pin: str | None = None
    # Control token (EU/AU/IN PIN verification) — cached with expiry:
    control_token: str | None = None
    control_token_expiry: float = 0
    # CCI login flow (EU Hyundai/Kia) — the access_token (above) is a CCS token
    # obtained by exchanging the CCI access token. These fields are persisted so
    # refresh_access_token can call cci-api-eu/domain/api/v2/auth/token-refresh
    # without a full password login. Each is a distinct value sent in the refresh
    # request body; none duplicates access_token/refresh_token.
    cci_access_token: str | None = None
    exchangeable_token: str | None = None
    exchangeable_refresh_token: str | None = None
    non_ccs_token: str | None = None
    non_ccs_refresh_token: str | None = None
    id_token: str | None = None
    # CCS user ID (uid claim from ccs_token JWT) — used for GSPA X-Stamp.
    ccs_user_id: str | None = None

    def to_dict(self) -> dict:
        """Convert Token to a JSON‑serializable dict."""
        data = asdict(self)

        # Convert datetimes to ISO strings
        data["valid_until"] = self.valid_until.isoformat()

        return data

    @classmethod
    def from_dict(cls, data: dict) -> "Token":
        """Create a Token instance from a dict."""
        # Parse datetimes from ISO strings
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
            control_token=data.get("control_token"),
            control_token_expiry=data.get("control_token_expiry", 0),
            cci_access_token=data.get("cci_access_token"),
            exchangeable_token=data.get("exchangeable_token"),
            exchangeable_refresh_token=data.get("exchangeable_refresh_token"),
            non_ccs_token=data.get("non_ccs_token"),
            non_ccs_refresh_token=data.get("non_ccs_refresh_token"),
            id_token=data.get("id_token"),
            ccs_user_id=data.get("ccs_user_id"),
        )
