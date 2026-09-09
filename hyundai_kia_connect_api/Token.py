"""Token.py"""

# pylint:disable=invalid-name

import datetime as dt
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path


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
    # User ID for GSPA X-Stamp (uid claim from CCS JWT).
    user_id: str | None = None
    # Connected-car customer ID used by Hyundai Korea's domestic API.
    cc_id: str | None = None

    def to_dict(self) -> dict:
        """Convert Token to a JSON‑serializable dict."""
        data = asdict(self)

        # Convert datetimes to ISO strings
        data["valid_until"] = self.valid_until.isoformat()

        return data

    def to_persistent_dict(self) -> dict:
        """Return refreshable session data without account or control secrets."""
        data = self.to_dict()
        data["password"] = None
        data["pin"] = None
        data["control_token"] = None
        data["control_token_expiry"] = 0
        return data

    def save(self, path: str | os.PathLike) -> None:
        """Atomically save a refreshable session in a user-only JSON file."""
        destination = Path(path).expanduser()
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        file_descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=destination.parent,
        )
        try:
            with os.fdopen(file_descriptor, "w", encoding="utf-8") as file:
                json.dump(self.to_persistent_dict(), file)
                file.write("\n")
            os.chmod(temporary_name, 0o600)
            os.replace(temporary_name, destination)
            os.chmod(destination, 0o600)
        finally:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass

    @classmethod
    def load(cls, path: str | os.PathLike) -> "Token":
        """Load session data created by :meth:`save`."""
        source = Path(path).expanduser()
        with source.open(encoding="utf-8") as file:
            return cls.from_dict(json.load(file))

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
            user_id=data.get("user_id"),
            cc_id=data.get("cc_id"),
        )
