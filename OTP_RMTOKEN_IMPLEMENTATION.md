# OTP and rmtoken Implementation for Kia USA API

## Summary

Implemented persistent authentication for Kia USA API to reduce OTP prompts in Home Assistant integration.

## Problem

The Kia USA API now requires OTP (One-Time Password) authentication. Previously, every time the session expired, users had to enter a new OTP code, making it impractical for automated systems like Home Assistant.

## Solution

Store and reuse the `rmtoken` (remember token) returned during OTP verification to avoid repeated OTP prompts.

## Changes Made

### 1. KiaUvoApiUSA.py

#### Modified `login()` method signature:
```python
def login(self, username: str, password: str, token: Token = None) -> Token:
```

#### Key features:
- Accepts optional `token` parameter with stored `rmtoken`
- If `token.refresh_token` (rmtoken) exists, includes it in the auth request headers
- Stores `rmtoken` in `Token.refresh_token` field after OTP verification
- Detects `rmTokenExpired` in API response and falls back to OTP flow
- Maintains backward compatibility (token parameter is optional)

#### Modified `request_with_active_session` decorator:
- Passes existing token when calling `login()` for session repair
- Updates `token.refresh_token` along with `access_token` and `valid_until`

### 2. VehicleManager.py

#### Added import:
```python
import inspect as insp
```

#### Modified `initialize()` and `check_and_refresh_token()`:
- Uses `inspect.signature()` to check if API's `login()` method accepts `token` parameter
- Passes token only if supported (USA API), otherwise uses old signature
- **This approach ensures compatibility with all other regions WITHOUT modifying them**
- Other region APIs (CA, EU, AU, CN, IN, BR, Hyundai USA) remain unchanged

### 3. tests/us_login_test.py

#### Updated test to verify rmtoken persistence:
- First login prompts for OTP and stores rmtoken
- Forces token expiration by setting `valid_until` to past time
- Second login should use stored rmtoken without OTP prompt
- Verifies rmtoken is preserved across logins

## Authentication Flow

### First Login (OTP Required):
1. POST `/prof/authUser` → Returns `otpKey` in payload
2. User chooses email or phone for OTP delivery
3. POST `/cmm/sendOTP` → Sends OTP code
4. User enters OTP code
5. POST `/cmm/verifyOTP` → Returns `sid` and `rmtoken` in headers
6. POST `/prof/authUser` with `sid` and `rmtoken` → Returns final `sid`
7. Store both `sid` (as `access_token`) and `rmtoken` (as `refresh_token`)

### Subsequent Logins (rmtoken Reuse):
1. POST `/prof/authUser` with `rmtoken` in headers → Returns `sid` directly
2. No OTP prompt needed!

### When rmtoken Expires:
1. POST `/prof/authUser` with expired `rmtoken` → Returns `otpKey` and `"rmTokenExpired": true`
2. Falls back to full OTP flow
3. Gets new `rmtoken`

## Benefits for Home Assistant

1. **Reduced OTP prompts**: Users only need to enter OTP when rmtoken expires
2. **Better UX**: Integration works more seamlessly without constant user intervention
3. **Backward compatible**: Works with accounts that don't have OTP enabled
4. **No changes to other regions**: EU, CA, AU, etc. continue to work as before

## Testing

Run the test:
```bash
source .venv/bin/activate
pytest tests/us_login_test.py -v -s
```

Expected behavior:
1. First login prompts for OTP (email or phone choice, then OTP code entry)
2. Test forces token expiration
3. Second login uses stored rmtoken without OTP prompt
4. Test passes if both logins succeed

## Notes

- The `rmtoken` lifetime is not documented but appears to be longer than the session token
- When `rmtoken` expires, the API returns `"rmTokenExpired": true` in the payload
- The implementation gracefully falls back to OTP flow when rmtoken is invalid/expired
- Home Assistant integration should persist the `Token.refresh_token` field to storage

