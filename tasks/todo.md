# TODO — EU DeviceIDError & API Fixes

## Device ID / DeviceIDError

- [ ] **Run stress test v2 with check_action_status polling** — extended stress test now polls action status after each lock command; should trigger DeviceIDError mid-polling (community reports: 2-4 polls in)
- [ ] **Fix check_action_status device_id mismatch** — current code regenerates device_id after commands, but new device_id doesn't match the one that sent the command → empty resMsg / "No action found". Need: either skip polling (sleep+refresh), or refresh device_id mid-poll, or keep original device_id for the polling session
- [ ] **Add timeout to all requests calls in ApiImplType1.py** — ~20 requests.get/post calls have no `timeout` parameter; causes CLOSE_WAIT hangs (discovered during stress test v1)
- [ ] **Thread safety for Token.device_id** — concurrent HA calls read/write device_id without lock (plan Phase 4, deferred)

## check_action_status / action polling

- [ ] **Replace check_action_status polling with sleep+refresh pattern** — EU check_action_status is fundamentally broken due to device_id mismatch. Alternative: after command, sleep 5s, then force_refresh_vehicle_state. Avoids the broken polling endpoint entirely. Proposed in API#1143.
- [ ] **Handle UNKNOWN status as PENDING in check_action_status** — EU returns empty/unknown status that gets treated as failure, but it often just means "not yet completed". Map to pending with timeout.

## Community-reported issues to address

- [ ] **kia_uvo coordinator: auto-recover from DeviceIDError** — tamcore PR#1552 adds retry with UpdateFailed. We need equivalent in our coordinator: catch DeviceIDError, set token=None, trigger re-login on next update cycle
- [ ] **"service problem, because of anything problem" error** — newer variant of DeviceIDError (resCode 4002 with different message). Same root cause, same fix path, but need to confirm our DeviceIDError exception catches this variant
- [ ] **Overnight/charging-completion crashes** (kia_uvo#990) — device_id invalidated when server sends push about charging completion. Not triggered by user action. Need: proactive re-login on DeviceIDError in coordinator's async_update_data

## CCI path (longer term)

- [ ] **Unblock CCI auth flow** — Keycloak WAF blocks /protocol/openid-connect/auth. Need mitmproxy/Frida approach to capture CCI tokens. CCI path uses stable client_device_id, eliminates DeviceIDError entirely. See [[xfingerprint-blocker]]
