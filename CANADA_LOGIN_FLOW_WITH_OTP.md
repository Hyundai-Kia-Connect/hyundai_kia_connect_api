# Kia Canada Authentication Flow with OTP

This document describes the complete authentication flow for the Kia Canada API, including the One-Time Password (OTP) verification process required for new devices.

## Table of Contents

- [Initial Login with OTP](#initial-login-with-otp)
- [Subsequent Login (Device Remembered)](#subsequent-login-device-remembered)
- [Important Headers](#important-headers)

---

## Initial Login with OTP

When logging in from a new device, the Kia Canada API requires OTP verification via email. This is a 5-step process:

### Step 1: Initial Login Attempt

**Endpoint:** `POST https://kiaconnect.ca/tods/api/v2/login`

**Request Body:**

```json
{
    "loginId": "user@example.com",
    "password": "password123"
}
```

**Response:** `200 OK` (with error in payload)

```json
{
    "error": {
        "errorCode": "7110"
    },
    "responseHeader": {
        "responseCode": 1,
        "responseDesc": "Failure"
    }
}
```

**Note:** Error code `7110` indicates that OTP verification is required for this device.

---

### Step 2: Select Verification Method

**Endpoint:** `POST https://kiaconnect.ca/tods/api/mfa/selverifmeth`

**Request Body:**

```json
{
    "mfaApiCode": "0107",
    "userAccount": "user@example.com"
}
```

**Parameters:**

- `mfaApiCode`: Always `"0107"` for Canada
- `userAccount`: User's email address

**Response:** `200 OK`

```json
{
    "responseHeader": {
        "responseCode": 0,
        "responseDesc": "Success"
    },
    "result": {
        "userInfoUuid": "ff36138e-4aa8-4030-ba5d-25090485fece",
        "otpKey": "",
        "emailList": ["user@example.com"]
    }
}
```

**Response Fields:**

- `userInfoUuid`: Unique identifier for this OTP session (required for subsequent steps)
- `otpKey`: Empty at this stage
- `emailList`: Available email addresses for OTP delivery

---

### Step 3: Send OTP Code

**Endpoint:** `POST https://kiaconnect.ca/tods/api/mfa/sendotp`

**Request Body:**

```json
{
    "otpMethod": "E",
    "mfaApiCode": "0107",
    "userAccount": "user@example.com",
    "userPhone": "",
    "userInfoUuid": "ff36138e-4aa8-4030-ba5d-25090485fece"
}
```

**Parameters:**

- `otpMethod`: `"E"` for email (Canada only supports email OTP)
- `mfaApiCode`: Always `"0107"` for Canada
- `userAccount`: User's email address
- `userPhone`: Empty string (not used in Canada)
- `userInfoUuid`: The UUID received from Step 2

**Response:** `200 OK`

```json
{
    "responseHeader": {
        "responseCode": 0,
        "responseDesc": "Success"
    },
    "result": {
        "otpKey": "NzY0NmFhNzEtNTc3My00ZGM3LTg4ODItM2Y3MTJjNjU"
    }
}
```

**Response Fields:**

- `otpKey`: Key required for OTP validation (Step 4)

**Note:** This triggers the server to send a 6-digit OTP code to the user's email address.

---

### Step 4: Validate OTP Code

**Endpoint:** `POST https://kiaconnect.ca/tods/api/mfa/validateotp`

**Request Body:**

```json
{
    "otpNo": "123456",
    "userAccount": "user@example.com",
    "otpKey": "NzY0NmFhNzEtNTc3My00ZGM3LTg4ODItM2Y3MTJjNjU",
    "mfaApiCode": "0107"
}
```

**Parameters:**

- `otpNo`: The 6-digit OTP code received via email
- `userAccount`: User's email address
- `otpKey`: The key received from Step 3
- `mfaApiCode`: Always `"0107"` for Canada

**Response:** `200 OK`

```json
{
    "responseHeader": {
        "responseCode": 0,
        "responseDesc": "Success"
    },
    "result": {
        "otpValidationKey": "2NIsvrwDDtigxczzGJvRZj4ikePAsjZbLsmiXfDI6uEOAfkI8HX59ZuYDuVnWIgyoUjdypXtYGyaUqJc0ly5wpG_Smm1kSbddalSrruEis2bdgb9gL7aE9FpiITQL2nm4LM9mTKFVsDX_ehioi4xISCRyHQj_anS07nq2jc6viKsgNzinGsY0FEisWu5J_QtKzsZRII35zYZtjMBv_8WJ2r_3WSVSPElTW-SXVJRpkpnMjpQXeqa4mrxad_HmFKfqDPCEO0JRfpPatfhv8MPJNHzYa05VpaLLvIWDWe5S2ZNBiAmsuMwmTOwrv1H76lo",
        "verifiedOtp": true
    }
}
```

**Response Fields:**

- `otpValidationKey`: Validation key required for token generation (Step 5)
- `verifiedOtp`: Boolean indicating if OTP was successfully verified

**Note:** This is where the user interface typically offers the option to remember the device for 90 days.

---

### Step 5: Generate MFA Token

**Endpoint:** `POST https://kiaconnect.ca/tods/api/mfa/genmfatkn`

**Request Body:**

```json
{
    "userAccount": "user@example.com",
    "otpEmail": "user@example.com",
    "mfaApiCode": "0107",
    "otpValidationKey": "2NIsvrwDDtigxczzGJvRZj4ikePAsjZbLsmiXfDI6uEOAfkI8HX59ZuYDuVnWIgyoUjdypXtYGyaUqJc0ly5wpG_Smm1kSbddalSrruEis2bdgb9gL7aE9FpiITQL2nm4LM9mTKFVsDX_ehioi4xISCRyHQj_anS07nq2jc6viKsgNzinGsY0FEisWu5J_QtKzsZRII35zYZtjMBv_8WJ2r_3WSVSPElTW-SXVJRpkpnMjpQXeqa4mrxad_HmFKfqDPCEO0JRfpPatfhv8MPJNHzYa05VpaLLvIWDWe5S2ZNBiAmsuMwmTOwrv1H76lo",
    "mfaYn": "N"
}
```

**Parameters:**

- `userAccount`: User's email address
- `otpEmail`: Email address where OTP was sent
- `mfaApiCode`: Always `"0107"` for Canada
- `otpValidationKey`: The validation key received from Step 4
- `mfaYn`: Device memory preference
    - `"N"` = Do not remember device (OTP required on next login)
    - `"Y"` = Remember device for 90 days

**Response:** `200 OK`

```json
{
    "responseHeader": {
        "responseCode": 0,
        "responseDesc": "Success"
    },
    "result": {
        "verifiedTnC": true,
        "token": {
            "signature": "",
            "scope": ["profile"],
            "expireIn": 86400,
            "accessToken": "K_8qiUZRI7LYiBaovXB...",
            "tokenType": "bearer",
            "refreshToken": "F-0pxNKNN8waqrmyKhdvhjOLMvgC4YJ3..."
        }
    }
}
```

**Response Fields:**

- `verifiedTnC`: Terms and conditions verification status
- `token.accessToken`: Bearer token for API authentication
- `token.refreshToken`: Token for refreshing the access token
- `token.expireIn`: Token expiration time in seconds (typically 86400 = 24 hours)
- `token.scope`: Token permissions
- `token.tokenType`: Always `"bearer"`

**Note:** The access token and refresh token have been truncated for readability.

---

## Subsequent Login (Device Remembered)

When the device has been remembered (by setting `mfaYn: "Y"` in Step 5), subsequent logins within 90 days are simplified to a single step.

### Single Step: Direct Login

**Endpoint:** `POST https://kiaconnect.ca/tods/api/v2/login`

**Request Body:**

```json
{
    "loginId": "user@example.com",
    "password": "password123"
}
```

**Important:** The same `Deviceid` header must be sent that was used during the initial OTP flow.

**Response:** `200 OK`

```json
{
    "responseHeader": {
        "responseCode": 0,
        "responseDesc": "Success"
    },
    "result": {
        "accountInformation": {
            "firstName": "John",
            "lastName": "Doe",
            "notificationEmail": "user@example.com",
            "phones": {
                "primary": "514-555-1234",
                "secondary": null
            },
            "addresses": {
                "primary": {
                    "street": "275 Rue Notre Dame E",
                    "city": "Montréal",
                    "province": "QC",
                    "postalCode": "H2Y 4B7"
                },
                "secondary": null
            },
            "preference": {
                "odometerUnit": 1,
                "climateUnit": "C",
                "languageId": 1,
                "maintenanceAlert": false,
                "preferredDealer": null,
                "promotionMessage": null
            }
        },
        "token": {
            "accessToken": "K_8qiUZRI7LYiBaovXB...",
            "scope": ["profile"],
            "tokenType": "bearer",
            "expireIn": 86400,
            "refreshToken": "F-0pxNKNN8waqrmyKhdvhjOLMvgC4YJ3...",
            "signature": ""
        },
        "verifiedTnC": true
    }
}
```

**Response Fields:**

- `accountInformation`: User's account details and preferences
- `token`: Authentication tokens (same structure as Step 5)
- `verifiedTnC`: Terms and conditions verification status

---

## Important Headers

All API requests must include these headers:

| Header         | Value                            | Description                                                                                       |
| -------------- | -------------------------------- | ------------------------------------------------------------------------------------------------- |
| `Content-Type` | `application/json;charset=UTF-8` | Request content type                                                                              |
| `Deviceid`     | Base64-encoded device identifier | **Critical:** Must be consistent across all requests in the OTP flow and stored for future logins |
| `from`         | `CWP`                            | Client identifier (Customer Web Portal)                                                           |
| `language`     | `0`                              | Language preference (0 = English)                                                                 |
| `offset`       | `-5` (or user's timezone)        | Timezone offset from UTC                                                                          |
| `Origin`       | `https://kiaconnect.ca`          | Request origin                                                                                    |
| `Referer`      | `https://kiaconnect.ca/login`    | Referrer URL                                                                                      |
