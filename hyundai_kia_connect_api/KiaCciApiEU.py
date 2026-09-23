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
    endpoints pass (GSPA_VERIFIED_ENDPOINTS). Door lock and unlock, and
    climate start/stop, are live-proven on a Kia PV5 (2026-09-19); lamp
    is live-proven on a Kia EV6 (2026-09-23). The remaining commands
    still raise NotImplementedError until live verification (D6).
    """

    # Door lock and unlock via the shared "door" endpoint +
    # {"command": "close"/"open"} live-proven on a Kia PV5 (2026-09-19):
    # 202 S 202-000, fresh stored-status 7 s (lock) / 4 s (unlock)
    # after sending. The vehicle re-locks itself after ~30 s if no door
    # is opened. Climate start/stop via the "temperature" endpoint also
    # live-proven on the same PV5 (2026-09-19): 202 S 202-000 both
    # directions, reaction ~5 s awake / ~21 s from sleep. tempUnit must
    # be a string ("C"/"F"); the int form is rejected with 400-002, and
    # without tempUnit/hvacTempType the backend applies the car's
    # stored set point instead of the requested one. Lamp all-off via
    # the "lamp" endpoint live-proven on a Kia EV6 (2026-09-23): 202
    # APPLIED with the stored-status lastUpdateTime advancing, and
    # prewakeup accepted with HTTP 202 on the same vehicle.
    GSPA_VERIFIED_ENDPOINTS = frozenset({"door", "temperature", "lamp"})

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
    # Door control (Kia per-action endpoints)
    #
    # Confirmed endpoints (Kia OneApp request dispatch + live):
    #   POST /gspa/v1/remote/vehicles/{carId}/door              {"command": "close" | "open"}
    #   POST /gspa/v1/remote/vehicles/{carId}/door-lock          {"command": "set"}
    #   POST /gspa/v1/remote/vehicles/{carId}/door-unlock        {"command": "set"}
    #   POST /gspa/v1/remote/vehicles/{carId}/door-lock-safety   {"command": "set"} — untested
    #   POST /gspa/v1/remote/vehicles/{carId}/door-unlock-safety {"command": "set"} — untested
    # The action lives in the endpoint; bodies are case-sensitive
    # ("lock"/"LOCK"/"CLOSE" are rejected with 400-002, live-verified on
    # a PV5). Both lock and unlock are live-proven on a PV5
    # (2026-09-19): door + close/open and door-lock/door-unlock + set
    # all returned 202 S 202-000 with a fresh stored-status 4-7 s
    # after. The SID shape is endpoint-independent (short msgId or
    # UUID, alternating between runs). lock_action covers both
    # directions via the shared "door" endpoint; the safety variants
    # stay gated until live-verified (D6).
    # ------------------------------------------------------------------
