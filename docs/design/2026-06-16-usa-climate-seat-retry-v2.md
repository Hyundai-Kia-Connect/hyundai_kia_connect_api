# USA Climate Seat Retry — Error Handling Redesign

**Date:** 2026-06-16
**Status:** Draft
**Affects:** `HyundaiBlueLinkApiUSA.start_climate()`, `KiaUvoApiUSA.start_climate()`
**Related PRs:** feat/usa-climate-seat-retry, feat/ca-climate-retry

---

## 1. Problem

### 1.1 Original implementation (current branch)

The retry logic in `HyundaiBlueLinkApiUSA.start_climate()` catches `APIError` to
trigger a retry without seat settings:

```python
try:
    _check_response_for_errors(response_json)
except APIError:
    if "seatHeaterVentInfo" in data:
        del data["seatHeaterVentInfo"]
        response = self.sessions.post(url, json=data, headers=headers)
        response_json = _safe_parse_json(response, "start_climate")
        if response_json is not None:
            _check_response_for_errors(response_json)
    else:
        raise
```

This assumes that **parameter errors raise `APIError`** and **auth errors raise
`AuthenticationError`**, so `except APIError` selectively retries only parameter
failures.

### 1.2 The errorCode 502 overload

`_check_response_for_errors` maps errorCode `"502"` → `AuthenticationError` and
**everything else** → `APIError`. This is the only mapped code.

But errorCode `"502"` is overloaded in the Hyundai USA API:

- **Auth failure** — documented in docstring as "Incorrect username or password"
- **Rate limiting** — `_get_vehicle_location` (line 296-299) checks for
  `errorCode == 502` + `errorSubCode == "HT_534"` as rate limiting, not auth.
  This branch is **unreachable** because `_check_response_for_errors` raises
  `AuthenticationError` first. This is a latent bug.
- **Possibly parameter errors** — we have no evidence either way. The Hyundai
  USA API is not documented. If the server returns 502 for an invalid
  `seatHeaterVentInfo` payload, our `except APIError` will never catch it.

### 1.3 Consequence

If the server returns errorCode `"502"` for seat parameter errors, the retry
**never triggers**. The user gets an `AuthenticationError` for what is actually
a parameter validation failure — confusing and unhelpful.

Even if the server doesn't return 502 for seat errors today, there's no
guarantee it won't tomorrow. The 502 overload makes the exception-based
approach fragile.

---

## 2. Reference implementations

### 2.1 egmp-bluelink-scriptable (USA Hyundai + Kia)

egmp does **not analyze error codes at all**. It retries on any failure:

```typescript
// usa.ts (Hyundai)
if (this.requestResponseValid(resp.resp, resp.json).valid) {
    // success
} else {
    // Kia/Hyundai US seems pretty particular with seat / duration settings,
    // hence if fail retry without them,
    if (!retryWithNoSeat) return this.climateOn(_id, config, true);
}
```

`requestResponseValid` checks only `resp.statusCode === 200` (HTTP status).
Any non-200 response triggers retry. No errorCode analysis.

**Notable:** egmp also removes `igniOnDuration` in the retry, because duration
settings also cause parameter errors on some US vehicles.

**Why egmp can do this:** It's a Scriptable iOS widget. The author had no API
documentation, so "if it fails, try without seats" was the simplest working
approach. No exception hierarchy to respect.

### 2.2 Our CA climate retry (feat/ca-climate-retry)

CA checks the response code **before** calling `_check_response_for_errors`:

```python
response_json = response.json()
if response_json.get("responseHeader", {}).get("responseCode") != 0:
    _LOGGER.warning("Climate start with hvacInfo failed, retrying with remoteControl")
    payload = self._build_ev_climate_payload(token, options, hex_set_temp, use_remote_control=True)
    response = self.sessions.post(url, headers=headers, data=json.dumps(payload))
    response_json = response.json()

self._check_response_for_errors(response_json)
```

This is a **response-level check** — it inspects the raw response before
throwing exceptions. The retry happens based on what the server said, not on
which exception type was raised.

---

## 3. Comparison of approaches

| Approach                        | How it decides to retry | Catches 502? | Catches other codes? | Risk of retrying auth errors                       |
| ------------------------------- | ----------------------- | ------------ | -------------------- | -------------------------------------------------- |
| `except APIError` (current)     | Exception type          | No           | Yes                  | None                                               |
| `except Exception` (egmp-style) | Any failure             | Yes          | Yes                  | High — retries on auth, network, etc.              |
| Response-level check (CA-style) | Raw response code       | Yes          | Yes                  | Low — only retries if errorCode exists in response |

---

## 4. Proposed design: Response-level check

### 4.1 Principle

Check the response JSON **before** calling `_check_response_for_errors`. If the
response contains an `errorCode`, attempt retry. After retry (or if no retry
needed), call `_check_response_for_errors` as normal.

This matches the CA climate retry pattern and avoids the errorCode 502 problem
entirely — because we don't rely on the exception type to decide whether to
retry.

### 4.2 HyundaiBlueLinkApiUSA.start_climate() — EV path

**Current (exception-based):**

```python
response = self.sessions.post(url, json=data, headers=headers)
response_json = _safe_parse_json(response, "start_climate")
if response_json is not None:
    try:
        _check_response_for_errors(response_json)
    except APIError:
        if "seatHeaterVentInfo" in data:
            _LOGGER.warning(
                f"{DOMAIN} - Climate start with seat settings failed, retrying without seat settings"
            )
            del data["seatHeaterVentInfo"]
            response = self.sessions.post(url, json=data, headers=headers)
            response_json = _safe_parse_json(response, "start_climate")
            if response_json is not None:
                _check_response_for_errors(response_json)
        else:
            raise
```

**Proposed (response-level):**

```python
response = self.sessions.post(url, json=data, headers=headers)
response_json = _safe_parse_json(response, "start_climate")

# If first attempt failed and seat settings were included, retry without them
if response_json is not None and "errorCode" in response_json:
    if "seatHeaterVentInfo" in data:
        _LOGGER.warning(
            f"{DOMAIN} - Climate start with seat settings failed "
            f"(errorCode={response_json.get('errorCode')}), "
            f"retrying without seat settings"
        )
        del data["seatHeaterVentInfo"]
        response = self.sessions.post(url, json=data, headers=headers)
        response_json = _safe_parse_json(response, "start_climate")

# Final error check — raises appropriate exception for the final response
if response_json is not None:
    _check_response_for_errors(response_json)
```

**Key differences:**

1. **No try/except around `_check_response_for_errors`** — the retry decision is
   made by checking the response JSON directly, not by catching exceptions.
2. **`"errorCode" in response_json`** — this is the signal that the server
   rejected the request. Any errorCode (502, 400, 9999) triggers retry if
   seat settings are present.
3. **Single `_check_response_for_errors` call at the end** — the final
   response is validated normally, so auth errors from the retry attempt
   still raise `AuthenticationError` correctly.
4. **Logging includes the actual errorCode** — helps debugging which error
   triggered the retry.

### 4.3 KiaUvoApiUSA.start_climate() — EV path

**Current (exception-based):**

```python
try:
    response = self.post_request_with_logging_and_active_session(
        token=token, url=url, json_body=body, vehicle=vehicle
    )
except RequestException:
    if "heatVentSeat" in body.get("remoteClimate", {}):
        _LOGGER.warning(
            f"{DOMAIN} - Climate start with seat settings failed, retrying without seat settings"
        )
        del body["remoteClimate"]["heatVentSeat"]
        response = self.post_request_with_logging_and_active_session(
            token=token, url=url, json_body=body, vehicle=vehicle
        )
    else:
        raise
return response.headers["Xid"]
```

**Problem:** `request_with_logging` decorator raises `RequestException` for
**all** non-zero statusCode responses. This means we're catching the
decorator's error signal, not the server's. And `RequestException` is a very
broad class from `requests` that also includes network errors.

**Proposed (bypass decorator for first call):**

```python
# Make the first call without the error-checking decorator
headers = self.authed_api_headers(token, vehicle)
response = self.session.post(url, json=body, headers=headers)
response_json = response.json()

# If first attempt failed and seat settings were included, retry without them
if response_json.get("status", {}).get("statusCode", 0) != 0:
    if "heatVentSeat" in body.get("remoteClimate", {}):
        _LOGGER.warning(
            f"{DOMAIN} - Climate start with seat settings failed "
            f"(statusCode={response_json.get('status', {}).get('statusCode')}), "
            f"retrying without seat settings"
        )
        del body["remoteClimate"]["heatVentSeat"]
        response = self.session.post(url, json=body, headers=headers)
        response_json = response.json()

# Validate final response
if response_json.get("status", {}).get("statusCode", 0) != 0:
    error_type = response_json.get("status", {}).get("errorType", "")
    if error_type == 1 and response_json.get("status", {}).get("errorCode") in [1003, 1005]:
        raise AuthenticationError("Session invalid")
    raise APIError(
        f"Climate start failed: statusCode={response_json.get('status', {}).get('statusCode')}"
    )

return response.headers["Xid"]
```

**Wait — this duplicates decorator logic.** Let me reconsider.

The `request_with_logging` decorator does two things:

1. Logs the request/response
2. Checks `statusCode` and raises `AuthenticationError` or `RequestException`

The `request_with_active_session` decorator catches `AuthenticationError` and
retries with a fresh login.

If we bypass the decorator, we lose:

- Request/response logging
- Auto-login on session expiry

**Alternative for Kia USA — keep decorator, but use response-level check before it throws:**

The decorator raises the exception **after** it has already logged and parsed
the response. We can't intercept it before it raises.

**Simplest correct approach for Kia USA:**

Keep the `try/except RequestException` but also catch `AuthenticationError` and
re-raise it immediately (don't retry on auth):

```python
try:
    response = self.post_request_with_logging_and_active_session(
        token=token, url=url, json_body=body, vehicle=vehicle
    )
except AuthenticationError:
    # Auth errors should not trigger seat-setting retry
    raise
except RequestException:
    if "heatVentSeat" in body.get("remoteClimate", {}):
        _LOGGER.warning(
            f"{DOMAIN} - Climate start with seat settings failed, retrying without seat settings"
        )
        del body["remoteClimate"]["heatVentSeat"]
        response = self.post_request_with_logging_and_active_session(
            token=token, url=url, json_body=body, vehicle=vehicle
        )
    else:
        raise
return response.headers["Xid"]
```

**Why this works for Kia USA:**

- `AuthenticationError` is raised for `statusCode == 1, errorType == 1,
errorCode in [1003, 1005]`. We catch and re-raise immediately — no retry.
- `RequestException` is raised for all other non-zero statusCodes. We retry
  if seat settings are present.
- The decorator's `request_with_active_session` wrapper has already handled
  the first `AuthenticationError` by re-logging in and retrying once. If we
  still get `AuthenticationError`, the session is truly invalid.

### 4.4 Summary of proposed changes

| File                       | Current approach               | Proposed approach                                 |
| -------------------------- | ------------------------------ | ------------------------------------------------- |
| `HyundaiBlueLinkApiUSA.py` | `except APIError` (misses 502) | Response-level: `"errorCode" in response_json`    |
| `KiaUvoApiUSA.py`          | `except RequestException`      | Add `except AuthenticationError: raise` before it |

---

## 5. Test changes

### 5.1 Hyundai USA tests

The response-level check means we no longer care which exception type the
first attempt would raise. The retry is triggered by the presence of
`errorCode` in the response, not by the exception type.

**Test `test_retry_without_seats_on_failure`:**

- First call returns JSON with `errorCode` (any value — 502, 400, 9999)
- Second call returns empty body (success)
- Verify 2 calls made

**Test `test_no_retry_on_success`:**

- First call returns empty body (no errorCode)
- Verify 1 call made

**Test `test_raises_error_when_both_fail`:**

- Both calls return JSON with `errorCode`
- Verify `APIError` or `AuthenticationError` raised (depending on errorCode)
- Verify 2 calls made

**Test `test_no_retry_on_auth_error_after_retry`:**

- First call returns `errorCode: 502`
- Second call (after seat removal) also returns `errorCode: 502`
- Verify `AuthenticationError` raised
- Verify 2 calls made (retry DID happen, because 502 is still an errorCode)

This last test is the key difference: with the response-level approach,
**errorCode 502 DOES trigger retry** if seat settings are present. The retry
won't help if 502 means auth failure, but it won't hurt either — the second
call will also get 502 and raise `AuthenticationError`. The cost is one extra
API call, the benefit is that we don't miss a potential parameter-error-502.

### 5.2 Kia USA tests

No functional change — the `except AuthenticationError: raise` pattern just
makes the auth-error non-retry behavior explicit. Tests remain the same.

---

## 6. Latent bug: \_get_vehicle_location 502/HT_534

The current code in `_get_vehicle_location` (lines 296-299) checks for rate
limiting via `errorCode == 502` + `errorSubCode == "HT_534"`, but this branch
is unreachable because `_check_response_for_errors` at line 291 already
raises `AuthenticationError` for errorCode `"502"`.

This is **out of scope** for this PR but should be tracked. The fix would be
to check `errorSubCode` inside `_check_response_for_errors` before mapping
502 to `AuthenticationError`:

```python
error_code_mapping = {
    "502": AuthenticationError,
}
# ...
if response["errorCode"] in error_code_mapping:
    # Check errorSubCode for non-auth 502 variants
    if response["errorCode"] == "502" and response.get("errorSubCode"):
        raise APIError(f"API Error {response['errorCode']}: {response['errorMessage']}")
    raise error_code_mapping[response["errorCode"]](response["errorMessage"])
```

Or more specifically, only map 502 to AuthenticationError when `errorSubCode`
is absent or indicates auth failure.

---

## 7. Open questions

1. **Should we also remove `igniOnDuration` in the retry?** egmp does this for
   USA vehicles. Some 2025+ vehicles reject both seat settings AND duration.
   This would require a larger payload reconstruction.

2. **Should the response-level check apply to ICE (non-EV) climate too?**
   Currently the retry only applies to the EV path. ICE vehicles don't have
   `seatHeaterVentInfo` in their payload structure.

3. **What other errorSubCode values exist for errorCode 502?** Without API
   documentation, we can't build a complete mapping. The response-level
   approach sidesteps this question entirely.
