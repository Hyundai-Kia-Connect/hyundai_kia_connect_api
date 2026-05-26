# TODO — EU DeviceIDError & API Fixes

## Device ID / DeviceIDError

- [x] **Run stress test v2 with check_action_status polling** — DONE. 40 min total testing, 0 DeviceIDErrors. Server may have changed behavior.
- [ ] **Fix check_action_status device_id mismatch** — current code regenerates device_id after commands, but new device_id doesn't match the one that sent the command → empty resMsg / "No action found". Need: either skip polling (sleep+refresh), or refresh device_id mid-poll, or keep original device_id for the polling session
- [ ] **Thread safety for Token.device_id** — concurrent HA calls read/write device_id without lock (plan Phase 4, deferred)

## HTTP timeout — Issue #1151

- [ ] **Add timeout constants to ApiImpl base class** — `HTTP_CONNECT_TIMEOUT=30`, `HTTP_READ_TIMEOUT=120`, region-overridable
- [ ] **Add timeout to all 139 requests calls** — across 10 files (see issue #1151 for per-file checklist)
- [ ] **Remove `pylint:disable=missing-timeout`** from 8 files after fix
- [ ] **Handle `requests.exceptions.Timeout`** → `RequestTimeoutError` in VehicleManager
- [ ] **Nominatim geocoding in ApiImpl** — shorter timeout (10s/30s), third-party service
- [ ] **Unit tests** — mock requests, assert timeout kwarg
- [ ] **Integration tests** — mock server with delayed response, confirm Timeout raised
- [ ] **Verify CA RetrySession + USA/BR SSLAdapter** compatibility with timeout

## check_action_status / action polling

- [ ] **Replace check_action_status polling with sleep+refresh pattern** — EU check_action_status is fundamentally broken due to device_id mismatch. Alternative: after command, sleep 5s, then force_refresh_vehicle_state. Avoids the broken polling endpoint entirely. Proposed in API#1143.
- [ ] **Handle UNKNOWN status as PENDING in check_action_status** — EU returns empty/unknown status that gets treated as failure, but it often just means "not yet completed". Map to pending with timeout.

## Community-reported issues to address

- [ ] **kia_uvo coordinator: auto-recover from DeviceIDError** — tamcore PR#1552 adds retry with UpdateFailed. We need equivalent in our coordinator: catch DeviceIDError, set token=None, trigger re-login on next update cycle
- [ ] **"service problem, because of anything problem" error** — newer variant of DeviceIDError (resCode 4002 with different message). Same root cause, same fix path, but need to confirm our DeviceIDError exception catches this variant
- [ ] **Overnight/charging-completion crashes** (kia_uvo#990) — device_id invalidated when server sends push about charging completion. Not triggered by user action. Need: proactive re-login on DeviceIDError in coordinator's async_update_data

## CCI path (longer term)

- [ ] **Unblock CCI auth flow** — Keycloak WAF blocks /protocol/openid-connect/auth. Need mitmproxy/Frida approach to capture CCI tokens. CCI path uses stable client_device_id, eliminates DeviceIDError entirely. See [[xfingerprint-blocker]]
