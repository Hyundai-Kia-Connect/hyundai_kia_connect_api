# pylint:disable=missing-function-docstring,missing-class-docstring
"""driving_insight_eu.py — Driving Insight (DI) client for the EU CCI brands.

The EU CCI backend exposes a Driving Insight API family next to GSPA:
token-exchange ?serviceType=DI, a per-brand gateway
apigw-eu.hmg-corp.io/di-{brand}-eu/v0100, and reads headered with the DI
bearer token plus Cci-Access-Token. Reads are feature-gated per vehicle:
/vehicle/{vin} carries supportedFeatures, and a read whose feature is
absent answers 400 "Not supported Feature -> <code>". Only calls a
vehicle's supportedFeatures list names are attempted.

Verified live 2026-11-23 on a Hyundai Santa Fe HEV (token-exchange DI and
/vehicle/{vin}); Kia shapes per the issue #1319/#1320 reports
(live-verified on a PV5 by the reporter).
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import TYPE_CHECKING, Any

import requests

from .const import DOMAIN

if TYPE_CHECKING:
    from .Token import Token
    from .Vehicle import Vehicle

_LOGGER = logging.getLogger(DOMAIN)


class DrivingInsightMixin:
    """Driving Insight reads for the EU CCI brand classes.

    Host-class contract: CCI_API_URL and LANGUAGE for the exchange
    header builders (CciAuthMixin), plus DI_BASE_URL and
    DRIVING_INSIGHT_VERIFIED — set both on a brand class only after live
    verification on that brand.
    """

    DI_BASE_URL: str | None = None
    DRIVING_INSIGHT_VERIFIED: bool = False

    if TYPE_CHECKING:
        # Host-provided state (CciAuthMixin contracts + GspaApiEU.__init__)
        # that this mixin relies on. data_timezone stays Any so the
        # declaration composes with the value-typed class attr on
        # GspaApiEU (dt.UTC) without an MRO mismatch.
        LANGUAGE: str
        data_timezone: Any

        def _exchange_ccs_token(
            self,
            device_id: str,
            cci_access_token: str,
            non_ccs_token: str,
            exchangeable_token: str,
            service_type: str = "CCS",
        ) -> tuple[str, dt.datetime]: ...

    def _ensure_di_token(self, token: Token) -> bool:
        """Exchange for a DI token when missing or expired; True when ready."""
        valid_until = token.di_token_valid_until
        if isinstance(valid_until, dt.datetime) and valid_until.tzinfo is None:
            valid_until = valid_until.replace(tzinfo=dt.UTC)
        if (
            token.di_access_token
            and valid_until is not None
            and valid_until - dt.timedelta(seconds=60) > dt.datetime.now(dt.UTC)
        ):
            return True
        try:
            di_token, di_valid_until = self._exchange_ccs_token(
                token.device_id or "",
                token.cci_access_token or "",
                token.non_ccs_token or "",
                token.exchangeable_token or "",
                service_type="DI",
            )
        except Exception:
            _LOGGER.debug(f"{DOMAIN} - DI token exchange failed")
            return False
        token.di_access_token = di_token
        token.di_token_valid_until = di_valid_until
        return bool(di_token)

    def _di_request(
        self, token: Token, vehicle: Vehicle, path: str
    ) -> dict[str, Any] | None:
        """GET a DI path; returns data on the success envelope, None otherwise.

        Feature-gate misses and non-2xx answer status "fail" envelopes;
        both are expected states (the vehicle does not support that read),
        logged at debug and returned as None.
        """
        if not (self.DI_BASE_URL and self.DRIVING_INSIGHT_VERIFIED):
            return None
        if not self._ensure_di_token(token):
            return None
        headers = {
            "Authorization": f"Bearer {token.di_access_token}",
            "Cci-Access-Token": token.access_token or "",
            "Client-Timezone": str(self.data_timezone),
            "Accept-Language": self.LANGUAGE or "en",
        }
        url = f"{self.DI_BASE_URL}{path}"
        try:
            resp = requests.get(url, headers=headers, timeout=(5, 30))
        except requests.RequestException:
            _LOGGER.debug(f"{DOMAIN} - DI request failed: {path}")
            return None
        try:
            body = resp.json()
        except ValueError:
            _LOGGER.debug(f"{DOMAIN} - DI non-JSON response: {path}")
            return None
        if resp.status_code != 200 or body.get("status") != "success":
            _LOGGER.debug(
                f"{DOMAIN} - DI read unavailable: {path} HTTP "
                f"{resp.status_code} {str(body.get('resultMessage'))[:120]}"
            )
            return None
        data = body.get("data")
        return data if isinstance(data, dict) else None

    def _update_di_data(self, token: Token, vehicle: Vehicle) -> None:
        """DI reads that follow a cached update; gated by the brand flags.

        The feature list is read once per cycle and cached on the
        vehicle (None = never read); the feature reads append below.
        """
        if not (self.DI_BASE_URL and self.DRIVING_INSIGHT_VERIFIED):
            return
        try:
            if vehicle.driving_insight_supported_features is None:
                self.get_di_vehicle_info(token, vehicle)
        except Exception:
            _LOGGER.debug(f"{DOMAIN} - DI vehicle-info read failed")

    def get_di_vehicle_info(
        self, token: Token, vehicle: Vehicle
    ) -> dict[str, Any] | None:
        """GET /vehicle/{vin}; caches supportedFeatures on the vehicle."""
        data = self._di_request(token, vehicle, f"/vehicle/{vehicle.VIN}")
        if data is None:
            return None
        features = data.get("supportedFeatures")
        vehicle.driving_insight_supported_features = (
            [f for f in features if isinstance(f, str)]
            if isinstance(features, list)
            else []
        )
        return data
