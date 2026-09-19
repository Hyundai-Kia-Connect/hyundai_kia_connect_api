"""KiaCciApiEU.py — Kia EU CCI/GSPA API.

Kia-specific EU implementation inheriting the OneApp (CCI) login flow,
vehicle listing, and the GSPA secure-request layer from ``GspaApiEU``.
This module keeps Kia brand constants. Remote actions are not
implemented: force refresh raises NotImplementedError (inherited from
ApiImpl) and prewakeup is overridden here with the same — Kia EU CCI
remote control awaits live verification (D6). Cached-state parsing
uses the shared CCS2 property parser (moved to ``GspaApiEU``): the Kia
stored-status envelope and vehicle state tree match the Hyundai shape
(confirmed live on a real EV6). Extended reads (driving info, history,
breakdowns, DTC) are Hyundai-specific parsers and are intentionally
not present on Kia; they are added once live fixtures from a real Kia
vehicle confirm their payload shapes.
"""

# pylint:disable=missing-class-docstring,invalid-name

from typing import Any

from .const import VEHICLE_LOCK_ACTION
from .exceptions import APIError
from .GspaApiEU import GspaApiEU
from .Token import Token
from .Vehicle import Vehicle


class KiaCciApiEU(GspaApiEU):
    """Kia EU CCI/GSPA API.

    Uses the CCI login flow (OneApp client_id 01b36c86) confirmed on
    production endpoints. Login, token lifecycle, and the GSPA
    secure-request layer are inherited from ``GspaApiEU``. GSPA remote
    control is inherited but partially gated: only live-verified
    endpoints pass (GSPA_VERIFIED_ENDPOINTS). Door lock was live-proven
    on a Kia PV5 (2026-09-19); everything else still raises
    NotImplementedError until live verification (D6).
    """

    # Door lock via the shared "door" endpoint + {"command": "close"}
    # live-proven on a Kia PV5 (2026-09-19): 202 S 202-000, hazard flash,
    # fresh stored-status 7 s later. Unlock ("open") is NOT yet proven.
    GSPA_VERIFIED_ENDPOINTS = frozenset({"door"})

    # Brand constants (Kia OneApp EU, confirmed on production endpoints).
    ONEAPP_CLIENT_ID = "01b36c86-79e8-486c-8009-15f2ad88d670"
    ONEAPP_REDIRECT_URI = "https://oneapp.kia.com/redirect"
    CCI_API_URL = "https://cci-api-eu.kia.com"
    CCI_PACKAGE_ID = "com.kia.oneapp.eu"
    GSPA_BASE_URL = "https://gspa-ccs-eu.kia.com/"
    LOGIN_FORM_HOST = "https://idpconnect-eu.kia.com"
    CIPHER_BRAND = "kia"
    REQUEST_ID_HEADER = "X-Request-Id"
    DEVICE_ID_HEADER = "X-Userdevice-Id"

    def prewakeup(self, token: Token, vehicle: Vehicle) -> dict[str, Any] | None:
        """Kia EU CCI remote actions await live verification (D6).

        App-confirmed request shape when implemented: the app sends
        {"action": "prewakeup"} (PreWakeupApiRequest) to the same
        brand-global prewakeup path — mirror the Hyundai implementation
        then.
        """
        raise NotImplementedError("Kia EU CCI prewakeup awaits live verification")

    def update_vehicle_with_cached_state(self, token: Token, vehicle: Vehicle) -> None:
        """Fetch GSPA stored-status and update vehicle properties.

        Same response shape as Hyundai EU CCI (``state.Vehicle`` in CCS2
        nested format), so the shared CCS2 property parser applies.
        Unlike the Hyundai implementation, driving info and history are
        not fetched: on Kia these endpoints currently fail server-side
        (confirmed live on a real EV6), so a cached-state update stays
        limited to stored-status.
        """
        if not (token.access_token or token.exchangeable_token):
            raise APIError("No CCS token — cannot fetch GSPA stored-status")
        data = self.get_stored_status(token, vehicle)
        if not data:
            raise APIError("GSPA stored-status returned no data")
        state = data.get("state", {})
        if isinstance(state, dict) and "Vehicle" in state:
            state = state["Vehicle"]
        self._update_vehicle_properties_ccs2(vehicle, state)

    # ------------------------------------------------------------------
    # Door control (Kia per-action endpoints) — GATED
    #
    # Confirmed endpoints (Kia OneApp request dispatch + live):
    #   POST /gspa/v1/remote/vehicles/{carId}/door-lock          {"command": "set"}
    #   POST /gspa/v1/remote/vehicles/{carId}/door-unlock        {"command": "set"}
    #   POST /gspa/v1/remote/vehicles/{carId}/door-lock-safety   {"command": "set"}
    #   POST /gspa/v1/remote/vehicles/{carId}/door-unlock-safety {"command": "set"}
    # The action lives in the endpoint; bodies default to "set" and are
    # case-sensitive ("lock"/"LOCK"/"CLOSE" are rejected with 400-002,
    # live-verified on a PV5). door-lock + {"command": "set"} returned
    # 202 S 202-000 on a PV5 (2026-09-19). Gated until the remaining
    # commands are live-verified; no POST is sent.
    # ------------------------------------------------------------------

    def lock_door(self, token: Token, vehicle: Vehicle) -> str:
        raise NotImplementedError("Kia EU CCI door control awaits live verification")

    def unlock_door(self, token: Token, vehicle: Vehicle) -> str:
        raise NotImplementedError("Kia EU CCI door control awaits live verification")

    def lock_door_safety(self, token: Token, vehicle: Vehicle) -> str:
        raise NotImplementedError("Kia EU CCI door control awaits live verification")

    def unlock_door_safety(self, token: Token, vehicle: Vehicle) -> str:
        raise NotImplementedError("Kia EU CCI door control awaits live verification")

    def lock_action(
        self, token: Token, vehicle: Vehicle, action: VEHICLE_LOCK_ACTION
    ) -> str:
        """Lock via the shared "door" endpoint (live-proven on a PV5).

        Unlock is intentionally still gated: the "open" direction was not
        exercised live on a Kia vehicle (2026-09-19 PV5 session).
        """
        if action != VEHICLE_LOCK_ACTION.LOCK:
            raise NotImplementedError("Kia EU CCI unlock awaits live verification")
        return super().lock_action(token, vehicle, action)
