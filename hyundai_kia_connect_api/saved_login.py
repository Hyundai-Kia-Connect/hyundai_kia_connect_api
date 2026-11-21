"""Remember the dashboard login between runs.

Region, brand and username go in a file only the user can read. The password
and PIN go in the operating system's password store through the optional
``keyring`` package, never in that file.
"""

import contextlib
import json
import os
from pathlib import Path
from types import ModuleType
from typing import Any

SERVICE = "roadbook"


def path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "roadbook" / "login.json"


def _keyring() -> ModuleType | None:
    try:
        import keyring
    except ImportError:
        return None
    return keyring


def _account(info: dict[str, Any]) -> str:
    return f"{info['region']}/{info['brand']}/{info['username']}"


def load() -> dict[str, Any]:
    """Saved region, brand, username and, when keyring has them, password and PIN."""
    try:
        info: dict[str, Any] = json.loads(path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    kr = _keyring()
    if kr and info.get("username"):
        try:
            secret = kr.get_password(SERVICE, _account(info))
        except Exception:  # a locked or missing backend: just ask again
            secret = None
        if secret:
            info.update(json.loads(secret))
    return info


def save(region: str, brand: str, username: str, password: str, pin: str) -> bool:
    """Save the login. Returns False when only the non-secret part could be saved."""
    info = {"region": region, "brand": brand, "username": username}
    file = path()
    file.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(info, f)
    os.chmod(file, 0o600)  # also when the file already existed with wider permissions
    kr = _keyring()
    if not kr:
        return False
    try:
        kr.set_password(
            SERVICE, _account(info), json.dumps({"password": password, "pin": pin})
        )
    except Exception:
        return False
    return True


def forget() -> bool:
    """Delete the saved login. Returns True if there was one."""
    info = load()
    if not info:
        return False
    kr = _keyring()
    if kr:
        with contextlib.suppress(Exception):  # nothing stored there
            kr.delete_password(SERVICE, _account(info))
    path().unlink(missing_ok=True)
    return True
