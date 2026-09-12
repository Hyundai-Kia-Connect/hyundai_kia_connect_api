"""KiaCciApiEU.py — Kia EU CCI/GSPA API.

Kia-specific EU implementation inheriting the OneApp (CCI) login flow,
vehicle listing, and the GSPA secure-request layer from ``GspaApiEU``.
This module keeps Kia brand constants. Remote actions are not
implemented: force refresh raises NotImplementedError (inherited from
ApiImpl) and prewakeup is overridden here with the same — Kia EU CCI
remote control awaits live verification (D6). Cached-state parsing
requires a live response fixture (captured during integration
validation) and raises NotImplementedError until then. Extended reads
(driving info, history, breakdowns, DTC) are Hyundai-specific parsers
and are intentionally not present on Kia; they are added once live
fixtures from a real Kia vehicle confirm their payload shapes.
"""

# pylint:disable=missing-class-docstring,invalid-name

from typing import Any

from .GspaApiEU import GspaApiEU
from .Token import Token
from .Vehicle import Vehicle


class KiaCciApiEU(GspaApiEU):
    """Kia EU CCI/GSPA API.

    Uses the CCI login flow (OneApp client_id 01b36c86) confirmed on
    production endpoints. Login, token lifecycle, and the GSPA
    secure-request layer are inherited from ``GspaApiEU``. GSPA remote
    control is inherited too but stays gated (GSPA_REMOTE_CONTROL_VERIFIED
    stays False): every control command raises NotImplementedError until
    live verification on a real Kia vehicle (D6).
    """

    # Brand constants (Kia OneApp EU, confirmed on production endpoints).
    ONEAPP_CLIENT_ID = "01b36c86-79e8-486c-8009-15f2ad88d670"
    ONEAPP_REDIRECT_URI = "https://oneapp.kia.com/redirect"
    CCI_API_URL = "https://cci-api-eu.kia.com"
    CCI_PACKAGE_ID = "com.kia.oneapp.eu"
    GSPA_BASE_URL = "https://gspa-ccs-eu.kia.com/"
    LOGIN_FORM_HOST = "https://idpconnect-eu.kia.com"
    CIPHER_BRAND = "kia"
    REQUEST_ID_HEADER = "DD-REQUEST-ID"
    DEVICE_ID_HEADER = "X-Userdevice-Id"

    def prewakeup(self, token: Token, vehicle: Vehicle) -> dict[str, Any] | None:
        """Kia EU CCI remote actions await live verification (D6)."""
        raise NotImplementedError("Kia EU CCI prewakeup awaits live verification")

    def update_vehicle_with_cached_state(self, token: Token, vehicle: Vehicle) -> None:
        """Raise until the Kia stored-status parser is live-verified.

        The Kia cached-state parser awaits a live response fixture,
        captured during integration validation on a real vehicle (D5).
        """
        raise NotImplementedError(
            "Kia cached-state parser requires a live response fixture "
            "(captured during integration validation)."
        )

    # ------------------------------------------------------------------
    # Door control (Kia per-action endpoints) — GATED
    #
    # Confirmed endpoints (protocol tables):
    #   POST /gspa/v1/remote/vehicles/{carId}/door-lock          {"command": "lock"}
    #   POST /gspa/v1/remote/vehicles/{carId}/door-unlock        {"command": "unlock"}
    #   POST /gspa/v1/remote/vehicles/{carId}/door-lock-safety   {"command": "lock"}
    #   POST /gspa/v1/remote/vehicles/{carId}/door-unlock-safety {"command": "unlock"}
    # Gated until a live-verified Kia fixture exists; no POST is sent.
    # ------------------------------------------------------------------

    def lock_door(self, token: Token, vehicle: Vehicle) -> str:
        raise NotImplementedError("Kia EU CCI door control awaits live verification")

    def unlock_door(self, token: Token, vehicle: Vehicle) -> str:
        raise NotImplementedError("Kia EU CCI door control awaits live verification")

    def lock_door_safety(self, token: Token, vehicle: Vehicle) -> str:
        raise NotImplementedError("Kia EU CCI door control awaits live verification")

    def unlock_door_safety(self, token: Token, vehicle: Vehicle) -> str:
        raise NotImplementedError("Kia EU CCI door control awaits live verification")
