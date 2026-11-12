# EU Error Code Table (myHyundai / Kia Connect EU, app 1.5.1)

Reference table of error codes carried in EU API responses, with the code
names as shipped in the app's error resources (app 1.5.1) and the current
library handling. Wire behavior is checked live where noted. This is a
reference document only: it adds no code paths and changes no
existing raise.

Two dispatchers see these codes:

- GSPA endpoints (`_raise_gspa_error` in `GspaApiEU`): exact and 3-char
  prefix entries in `GSPA_RES_CODE_MAPPING`, plus `ServiceTemporaryUnavailable`
  for anything starting with `5`, else a passthrough `APIError` carrying
  `rc=<resCode>` and the server message.
- SPA app-server responses (`ApiImplType1`): `error_code_mapping` exact
  entries, else a passthrough `APIError` with the raw `resCode`.

## A. GSPA resCodes

| resCode    | code name                                          | exception raised today          |
| ---------- | -------------------------------------------------- | ------------------------------- |
| `400-000`  | GspaBadRequest                                     | APIError passthrough            |
| `400-001`  | GspaInvalidRequestForRemoteControl                 | APIError passthrough            |
| `400-002`  | GspaInvalidRequestBody                             | APIError passthrough            |
| `400-003`  | GspaInvalidPin                                     | APIError passthrough            |
| `400-004`  | GspaDuplicateRequest                               | DuplicateRequestError           |
| `400-005`  | GspaUnsupportedControlRequest                      | APIError passthrough            |
| `400-010`  | GspaMissingMandatoryHeader                         | APIError passthrough            |
| `400-013`  | GspaNotification.AlreadyRegisteredRemind           | APIError passthrough            |
| `401-001`  | GspaInvalidAccessToken                             | AuthenticationTokenExpiredError |
| `402-002`  | GspaServicePlanUnsupported                         | APIError passthrough            |
| `403-000`  | GspaDeniedAccessToService                          | AuthenticationError             |
| `403-001`  | GspaDeactivatedUser                                | AuthenticationError             |
| `403-002`  | GspaForbiddenControlToken                          | AuthenticationError             |
| `403-003`  | GspaRegisteredVehicle                              | AuthenticationError             |
| `403-004`  | GspaStolenVehicle                                  | AuthenticationError             |
| `403-007`  | GspaInvalidRequestStamp                            | AuthenticationError             |
| `403-012`  | GspaUserLink.DeactivatedUser                       | AuthenticationError             |
| `403-017`  | GspaUserLink.UndefinedService                      | AuthenticationError             |
| `403-018`  | GspaUserLink.UndefinedProvider                     | AuthenticationError             |
| `404-003`  | GspaNoDataFound                                    | NoDataFound                     |
| `404-004`  | GspaDataNotFound                                   | NoDataFound                     |
| `404-007`  | GspaNoUpdateInfoFound                              | UnsupportedControlError         |
| `406-001`  | GspaFailedUploadingVideo                           | APIError passthrough            |
| `406-002`  | GspaFailedDeletingVideoDate                        | APIError passthrough            |
| `408-000`  | GspaRequestTimeout                                 | RequestTimeoutError             |
| `409-000`  | GspaCcuOtaUpdateDuplicateRequest                   | DuplicateRequestError           |
| `409-001`  | GspaCcuOtaUpdateReservationInProgress              | APIError passthrough            |
| `410-001`  | GspaInvalidVehicleWithEmptyCenterData              | APIError passthrough            |
| `412-001`  | GspaUserLink.NoContext                             | APIError passthrough            |
| `412-003`  | GspaNeverBeenActivatedVehicle                      | APIError passthrough            |
| `412-004`  | GspaTmuUsimPausedAndNoProvisioningMo               | APIError passthrough            |
| `412-005`  | GspaTmuUsimActivatedAndNoProvisioningMo            | APIError passthrough            |
| `412-006`  | GspaOfflineMode                                    | APIError passthrough            |
| `412-007`  | GspaTmuUsimNotActivated                            | APIError passthrough            |
| `412-012`  | GspaUserLink.LobBatteryError                       | APIError passthrough            |
| `412-013`  | GspaChargingCableInNotConnected                    | APIError passthrough            |
| `412-014`  | GspaUserLink.ConditionInsufficientForRemoteControl | APIError passthrough            |
| `412-015`  | GspaElectronicVehicleError                         | APIError passthrough            |
| `412-032`  | GspaNotEnoughBattery                               | APIError passthrough            |
| `412-033`  | GspaUnstableVehicleVoltage                         | APIError passthrough            |
| `422-020`  | GspaSvrInProgress                                  | APIError passthrough            |
| `422-021`  | GspaSvrInProgressStolenVehicle                     | APIError passthrough            |
| `422-022`  | GspaUploadingVideo                                 | APIError passthrough            |
| `422-023`  | GspaSvmError                                       | APIError passthrough            |
| `422-024`  | GspaSvmNoResponse                                  | APIError passthrough            |
| `422-025`  | GspaSvmEtcStatus                                   | APIError passthrough            |
| `422-026`  | GspaLowBatteryForSurroundViewMonitor               | APIError passthrough            |
| `422-030`  | GspaTmuTimeoutForRemoteControl                     | APIError passthrough            |
| `422-031`  | GspaLowBatteryForRemoteControl                     | APIError passthrough            |
| `422-032`  | GspaCcuOtaUpdateForRemoteControl                   | APIError passthrough            |
| `422-033`  | GspaHuOtaUpdateForRemoteControl                    | APIError passthrough            |
| `422-034`  | GspaUploadFailedVideo                              | APIError passthrough            |
| `428-001`  | GspaProvisionDisabledByGpiGlobal                   | APIError passthrough            |
| `428-001E` | GspaEPrivacyOffByCustomer                          | APIError passthrough            |
| `428-002`  | GspaProvisionDisabledBySetting                     | APIError passthrough            |
| `428-999`  | GspaForbiddenByPolicy                              | APIError passthrough            |
| `429-000`  | GspaTooManyRequest                                 | APIError passthrough            |
| `429-001`  | GspaRateLimit                                      | APIError passthrough            |
| `429-002`  | GspaMaximumRequestsReached                         | APIError passthrough            |
| `429-003`  | GspaHourlyRequestLimitExceeded                     | RateLimitingError               |
| `429-004`  | GspaDailyRequestLimitExceeded                      | RateLimitingError               |
| `451-009`  | GspaDataSafetyRegulationsOnCn                      | APIError passthrough            |
| `491-000`  | GspaNotFoundDIM                                    | APIError passthrough            |
| `500-000`  | GspaUserLink.InternalServerError                   | ServiceTemporaryUnavailable     |
| `500-999`  | GspaUndefinedError                                 | ServiceTemporaryUnavailable     |
| `501-000`  | GspaNotImplemented                                 | ServiceTemporaryUnavailable     |
| `502-000`  | GspaInternalServerError                            | ServiceTemporaryUnavailable     |
| `502-001`  | GspaCarInfoParseError                              | ServiceTemporaryUnavailable     |
| `502-051`  | GspaGpsLocationCheckFailed                         | ServiceTemporaryUnavailable     |
| `502-053`  | GspaInvalidValetAuth                               | ServiceTemporaryUnavailable     |
| `502-071`  | GspaUserLink.UnavailableAwsRequest                 | ServiceTemporaryUnavailable     |
| `502-072`  | GspaUserLink.UnavailableCpRequest                  | ServiceTemporaryUnavailable     |
| `503-000`  | GspaServiceTemporaryUnavailable                    | ServiceTemporaryUnavailable     |
| `503-001`  | GspaFailLfpBatteryTargetSoc                        | ServiceTemporaryUnavailable     |
| `504-000`  | GspaGatewayTimeout                                 | ServiceTemporaryUnavailable     |

## B. SPA app-server resCodes (flows the library calls)

| resCode  | code name                              | exception raised today      |
| -------- | -------------------------------------- | --------------------------- |
| `1111`   | EmptyRequestDataForService             | APIError passthrough        |
| `2002`   | UnregisteredUser                       | APIError passthrough        |
| `4002`   | ControlRequestUnauthorized             | DeviceIDError               |
| `4003`   | InvalidPin                             | APIError passthrough        |
| `4004`   | ControlRequestDuplicatedEOversea       | DuplicateRequestError       |
| `4006`   | BatteryProtectionState2                | APIError passthrough        |
| `4007`   | LowBatteryForRemoteControlGlobal       | APIError passthrough        |
| `4008`   | CcuOta                                 | APIError passthrough        |
| `4009`   | VehicleInfoUpdateInProgressGlobal      | APIError passthrough        |
| `4032`   | ControlTokenInvalid                    | APIError passthrough        |
| `4081`   | VehicleNotResponded                    | RequestTimeoutError         |
| `4101`   | NoneCcsUser                            | APIError passthrough        |
| `4121`   | PinReset                               | APIError passthrough        |
| `4126`   | OfflineMode                            | APIError passthrough        |
| `4230`   | ReleaseRequestedBeforeCcsSyncCompleted | APIError passthrough        |
| `4291`   | AuthAlreadyCompleted                   | APIError passthrough        |
| `4292`   | MaximumRequestsReached                 | APIError passthrough        |
| `5031`   | ControlRequestFailed                   | ServiceTemporaryUnavailable |
| `5091`   | ControlRequestExceeded                 | RateLimitingError           |
| `5131`   | ChargingCablesUnpluggedGlobal          | APIError passthrough        |
| `5152`   | EmptyLatitudeLongitudeFromClientGlobal | APIError passthrough        |
| `6666`   | MtsSessionExpired                      | APIError passthrough        |
| `7775`   | SvmVideoEmpty                          | APIError passthrough        |
| `7776`   | EmptyLatitudeLongitudeFromClient       | APIError passthrough        |
| `7777`   | EmptyLatitudeLongitude                 | APIError passthrough        |
| `7778`   | VehicleDistanceOutranged               | APIError passthrough        |
| `7779`   | SurroundViewFailed                     | APIError passthrough        |
| `7780`   | SurroundViewCameraFailed               | APIError passthrough        |
| `7781`   | SurroundViewEmergencyLightError        | APIError passthrough        |
| `7782`   | GlobalLowBatteryForSurroundViewMonitor | APIError passthrough        |
| `8888`   | EmptyDeviceInfo                        | APIError passthrough        |
| `BK01`   | BlockedByCenter                        | APIError passthrough        |
| `BK02`   | BlockedByRateLimit                     | APIError passthrough        |
| `BK03`   | WarnedByRateLimit                      | APIError passthrough        |
| `CC01`   | NoneCcsUser2                           | APIError passthrough        |
| `CC02`   | MtsLoggedInByCcspUser                  | APIError passthrough        |
| `CC03`   | ProfileUpdateInProgress                | APIError passthrough        |
| `CC04`   | ProfileDisabled                        | APIError passthrough        |
| `CC05`   | BatteryProtectionState                 | APIError passthrough        |
| `CC06`   | LowBatteryForRemoteControl             | APIError passthrough        |
| `CC07`   | VehicleSystemUpdateInProgress          | APIError passthrough        |
| `CC08`   | VehicleInfoUpdateInProgress            | APIError passthrough        |
| `CC09`   | VehicleInRepairMode                    | APIError passthrough        |
| `CS01`   | FailedToRefresh                        | APIError passthrough        |
| `DP01`   | DuplicatedRequest                      | APIError passthrough        |
| `E100`   | ControlRequestDuplicatedEDomestic      | APIError passthrough        |
| `E101`   | NonExistentVehicle                     | APIError passthrough        |
| `E102`   | ControlResultEmpty                     | APIError passthrough        |
| `E103`   | ControlRequestDuplicatedInSecond       | APIError passthrough        |
| `E110`   | PortalServerError                      | APIError passthrough        |
| `E200`   | ControlRequestExceededPerDay           | APIError passthrough        |
| `E300`   | InvalidArsNumber                       | APIError passthrough        |
| `E301`   | InvalidAccessToken                     | APIError passthrough        |
| `E400`   | MtsNumberEmpty                         | APIError passthrough        |
| `E500`   | MiddlewareServerError                  | APIError passthrough        |
| `E555`   | SchedulingInfoNotSet                   | APIError passthrough        |
| `E666`   | SchedulingInfoDuplicated               | APIError passthrough        |
| `E700`   | ControlRequestFailed                   | APIError passthrough        |
| `E760`   | ChargingCablesUnplugged                | APIError passthrough        |
| `E800`   | EV41ServerBaseReservationEmpty         | APIError passthrough        |
| `EEEE`   | RequestErrorE                          | APIError passthrough        |
| `EX01`   | ServerError                            | APIError passthrough        |
| `EX02`   | RequestError                           | APIError passthrough        |
| `G100`   | BlockedByCenterG                       | APIError passthrough        |
| `L462`   | LoggedInfoMismatched                   | APIError passthrough        |
| `PE01`   | AccessWithoutControlTokenError         | APIError passthrough        |
| `PR01`   | OpenCommNetworkFailed                  | APIError passthrough        |
| `R090`   | AuthNumberExpired                      | APIError passthrough        |
| `S100`   | LicenseMismatched                      | APIError passthrough        |
| `S301`   | IssuingAuthNumberFailed                | APIError passthrough        |
| `S401`   | RequestAuthNumberDuplicated            | APIError passthrough        |
| `S403`   | AuthKeyMismatched                      | APIError passthrough        |
| `S404`   | MobileMismatched                       | APIError passthrough        |
| `S422`   | IllegalPhoneNumberError                | APIError passthrough        |
| `S504`   | BlockedByCenterS                       | APIError passthrough        |
| `S510`   | InvalidSid                             | APIError passthrough        |
| `S520`   | UbiServiceUnauthorized                 | APIError passthrough        |
| `S703`   | CreditCardUnverified                   | APIError passthrough        |
| `S888`   | ChargingInfoNotReceived                | APIError passthrough        |
| `SV02`   | SessionExpired                         | APIError passthrough        |
| `SV03`   | ControlRequestDuplicated               | APIError passthrough        |
| `SVM2`   | SvmControllerNotResponded              | APIError passthrough        |
| `SVM7`   | SvmControlHazardFailed                 | APIError passthrough        |
| `SVM8`   | LowBatteryForSurroundViewMonitor       | APIError passthrough        |
| `VL01`   | FullTextFormatError                    | APIError passthrough        |
| `VL02`   | InfoMismatched                         | APIError passthrough        |
| `X000`   | UndefinedResponseError                 | APIError passthrough        |
| `161530` | VehicleInfoUpdateInProgressOnPnc       | APIError passthrough        |

## Error shapes that are not resCodes

Two EU auth surfaces answer without a `resCode`, so they stay out of the
tables above:

- **Password login** (`POST signin`): the server answers with redirects
  instead of error codes. A wrong username, a wrong password, or a
  password over the ~20 character limit all return the same
  redirect-to-login page. The library maps the redirect variants to
  `AuthenticationSigninError`, `AuthenticationInvalidCredentialsError`,
  `AuthenticationConsentRequired` / `ConsentRequiredError`, and
  `AuthenticationRedirectError`; config flows show a length hint for
  long passwords. There is no additional wire code to map.
- **PIN verification** (`POST auth/v1/pin`): a wrong PIN returns HTTP 200
  with `isMatched: false` and `attemptInfo` (remaining attempts, lockout
  window), not an error envelope. The library raises `APIError` with the
  remaining attempt count and the server lockout window.

## Excluded on purpose

Families the library never calls and that show no error surface on any
library flow are left out: device provisioning (`Provision.*`), the
CarProfileWeb portal family (4xx legacy `450-487`), digital-key flows
(`DigitalKey1/2`, 1xxx/2xxx/4xx-DK), the digital-frame widget (`DF*`),
built-in dashcam events (`E104-E107`), user-link/share screens
(`UserLink`, 200/2xxx), the store, repair and charge-B2B / V2X flows, and
the test-server demo codes (`fe1`, `me1`, ...). Their app names are kept
in the source table this document was generated from; add them back only
if a library flow grows.
