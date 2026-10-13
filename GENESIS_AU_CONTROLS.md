# Genesis Australia (GCS) remote controls – API findings

Findings from the live Genesis Connected Services (GCS) Australia backend and
the decompiled Android app `com.genesis.aus` v1.0.15 (shared `hmns.global.*`
codebase). Captured 2026-10-13 against a 2025 Genesis GV70 3.5T (ICE, CCS2
head unit, `ccuCCS2ProtocolSupport: 1`, RHD). Identifiers are redacted.

## Live capture – what the GV70 actually returns

| Endpoint | Result |
| --- | --- |
| `GET /api/v1/spa/vehicles` | one vehicle, `type: "GN"` (ICE), `protocolType: 1`, `ccuCCS2ProtocolSupport: 1` |
| `GET /api/v1/spa/vehicles/{id}/ccs2/carstatus/latest` | full CCS2 `state.Vehicle` tree (Body/Cabin/Chassis/Drivetrain/Electronics/Location) |
| `GET /api/v1/spa/vehicles/{id}/ccs2/carstatus` (force refresh) | **async ack only** `{retCode:S, resCode:0000, msgId}` – no `resMsg`; the car then publishes a fresh `/latest` (observed `lastUpdateTime` advancing on the next read) |
| `GET /api/v1/spa/vehicles/{id}/location/park` | `coord{lat,lon,alt}`, `head`, `speed`, `time` |
| `GET /api/v1/spa/vehicles/{id}/profile` | `vinInfo[0].{basic, device, option, serviceOption, batteryType, detailInfo, dtcCategory}` – **this is where the `opt_*` / `serviceOpt_*` capability flags come from** |
| `GET /api/v1/spa/vehicles/{id}/status/latest` | legacy flat status also works on this CCS2 car |
| `GET /api/v1/spa/vehicles/{id}/status/valet` | `valetMode: 2` |
| `GET /api/v1/spa/vehicles/{id}/status/huversion` | head unit `ccIC27`, map/software versions |
| `GET /api/v1/spa/vehicles/{id}/setting/alert` | idle/alert settings |
| `GET /api/v1/spa/notifications/{id}/records` | `[]` (action result log used by `check_action_status`) |
| `GET /api/v1/spa/vehicles/{id}/setting/service` | 500 |
| `GET /api/v1/spa/vehicles/{id}/immobilization/status` | 500 |
| `GET …/setting/service_v2`, `…/subscriptions` | read timeout (30 s) |

### Capability flags reported for this GV70 (`profile.vinInfo[0]`)

`option`: `airControlType 1, drvSeatLoc R, remoteControl 1, heating1 1,
heatingFrontWindow 0, strgWhlHeatOption 2, heatingSteeringWheel 1,
heatingSideMirror 1, heatingRearWindow 1, hvacTempType 1,
remoteControlWaitingTime 336, windowSafetyOption2 3, svmOptionInfo 1,
remoteAirCtrlControlOption 1, seatHeaterVent {drv 6, ast 6, rl 2, rr 2},
cmsOption 0, evAlarmOptionInfo 0, sunRoofOption 1, digitalKey2 0,
remoteHeatControl 2, airPurifierOption 0, dvrsOption 0, ignCtrlOption 1,
fineDustOption 1`

`serviceOption`: `batteryWarningService 1, scheduleLinkService 1,
centerUserProfileOption 4, finalDestinationNoti 1, valetServiceOption 1,
notificationSupport 1, remoteValetActOption 1, engineIdleTimeNotification 1,
dvrsOption 0, speedEventSupport 0, calendarOption 2`

Absent (so unsupported on this car): `frunkOption`, `rearWindowCurtainOption`,
`petCareService`, `remoteLightControlOption`, `windowControlOption`,
`batteryPreconditioningOption`, `V2LOption`, `chargePortDoorOption`,
`hornLightAvailable`, `lightOnlyAvailable`, third-row / armrest seat heat.

The app copies these into its Room `Vehicle` table as `opt_<name>` /
`serviceOpt_<name>` columns; the seat values use `J.e(v)`: `v not in (0, 7)`
means the seat has remote heat/vent control.

### CCS2 status fields of note (not previously parsed)

`Body.Lights.TailLamp.Alert`, `Body.Lights.Hazard.Alert` (lamp-left-on alerts
that gate the app's remote "lamp off" screen), `Body.Sunroof.Glass.Open`,
`Cabin.HVAC.Vent.AirCleaning.Indicator`, `Cabin.HVAC.Vent.FineDust.*`,
`Cabin.Seat.*.ArmRest.Heat`, `Service.ConnectedCar.RemoteControl.{Available,
WaitingTime}`.

## Control endpoints (Retrofit `ControlUseTokenApi`, all need the control token)

All CCS2 (`*Mqtt`) variants are `POST /api/v2/spa/vehicles/{id}/ccs2/...` with
`Authorization: <control token>` (PIN-derived, `PUT /api/v1/user/pin?token=`).
Bodies were taken from `RemoteViewModel` (`viewmodels/D.java`), the map
helpers in `repositories/J.java` and the `RemoteRepository$Command` enum
(re-decompiled in simple mode to recover the hidden `command` values).

| Feature | CCS2 path | Body | Legacy path | Body | Gate |
| --- | --- | --- | --- | --- | --- |
| Frunk open | `ccs2/control/frunk` | `{"command": 1}` | – | – | `option.frunkOption == 1` |
| Steady lamp off | `ccs2/control/lamp` | `{"tailLamp": 0/1, "hazardLamp": 0/1}` (omit a key to leave it) | `control/lamp` | `{"tailLamp": 0/1, "hazard": 0/1}` | `serviceOption.remoteLightControlOption`, status `Body.Lights.{TailLamp,Hazard}.Alert` |
| Windows (all) | `ccs2/control/window` | `{"command": "open"\|"close"}` | `control/window` | `{"deviceId", "action": "open"\|"close"}` | `windowSafetyOption2`/`serviceOption.windowControlOption` |
| Windows + rear curtain | `ccs2/control/windowcurtain` | `{"drvSeatWindow", "psgSeatWindow", "rlSeatWindow", "rrSeatWindow", "rlSeatWindowCurtain", "rrSeatWindowCurtain", "drvSeatLoc"}` (rear keys omitted when `windowSafetyOption2 == 4`) | `control/windowcurtain` | `{"deviceId", "frontLeft", "frontRight", "backLeft", "backRight", "rearCurtainLeft", "rearCurtainRight"}` | `option.rearWindowCurtainOption` for the curtain keys |
| Pet care / cabin mode | `ccs2/control/petcare` | `{"hvacTemp": "22.0", "tempUnit": "C"\|"F"}` (temperature as a string) | – | – | `serviceOption.petCareService`, `option.cmsOption` |
| Battery conditioning | `ccs2/control/batteryconditioning` | `{"command": 1}` start / `{"command": 0}` stop | – | – | `option.batteryPreconditioningOption`, EV only |
| Hazard + horn | `ccs2/control/hornlight` | `{"command": "on"}` (`"lowbeam"` when `remoteLightControlOption == 0`) | `control/horn` | `{"deviceId", ...}` | – |
| Hazard only | `ccs2/control/light` | `{"command": "on"}` | `control/light` | – | – |
| Door | `ccs2/control/door` | `{"command": "open"\|"close"}` | `control/door` | `{"deviceId", "action"}` | – |
| Climate start | `ccs2/control/temperature` | see below | `control/temperature` | `{"action","hvacType","tempCode","unit","options":{…}}` | – |
| Climate stop | `ccs2/control/temperature` | `{"command": "stop"}` | | | |
| Scheduled charge + climate | `ccs2/reservation/chargehvac` | `{"reservFlag", "reservChargeInfo1", "reservChargeInfo2", "offPeakPowerInfo"}`; `reservFatcSet` is stripped unless `vehicleType == "EV"` | `reservation/charge` | same with `reservChargeInfo` | `serviceOption.scheduleLinkService`; charging-only for non-EV |
| EV5 flat schedules | `ccs2/reservation/charge`, `ccs2/reservation/hvac` | already implemented upstream | | | |
| Charge alarm / current / V2L limit | `ccs2/charge/alarm`, `ccs2/charge/chargingcurrent`, `ccs2/charge/dischargelimit` | already implemented upstream | | | |

### CCS2 climate-start body (`repositories/H.java`, case 2)

```
command: "start"
hvacTemp, hvacTempType, tempUnit            (omitted when airControlType == "0")
heating1  OR  sideRearMirrorHeating + strgWhlHeating (when a steering-wheel step option exists)
airPurifierControl: 0/1                      (only when option.airPurifierOption == 1 and remoteControl == 1)
windshieldFrontDefogState: bool              (forced False when airPurifierControl == 1)
windshieldFrontHeatState: 0/1                (only with heatingFrontWindow)
drvSeatLoc: "L"/"R"
seatClimateInfo: { drvSeatClimateState, psgSeatClimateState, rlSeatClimateState,
                   rrSeatClimateState, thrlSeatClimateState, thrrSeatClimateState }
ignitionDuration                             (only when option.clmtCtrlOption == 1)
```

Armrest heating (`frArmrestHeatOption` / `rrArmrestHeatOption`) only appears
in status (`Cabin.Seat.*.ArmRest.Heat`); the app sends no command for it.
Sunroof (`sunRoofOption`) is status only (`Body.Sunroof.Glass.Open`); there is
no sunroof command in the app. The air purifier is a climate-start option, not
a stand-alone toggle.

## What this branch implements

* `KiaUvoApiAU._force_refresh_vehicle_state_ccs2`: read `/latest` for a
  baseline `lastUpdateTime`, wake, then poll `/latest` every 3 s until the
  timestamp advances (60 s cap, then the cached snapshot is applied). The
  wake leaves no `/notifications/{id}/records` entry, so the snapshot
  timestamp is the only completion signal; the GV70 published ~2 s after the
  wake. The old code raised `KeyError: 'resMsg'`.
* `KiaUvoApiAU.get_vehicles`: fetches `/profile` per vehicle and fills
  `Vehicle.year`, `vehicle_options`, `service_options` and the derived
  `supports_frunk`, `supports_lamp_control`, `supports_rear_window_curtain`,
  `supports_pet_care`, `supports_battery_conditioning`, `supports_air_purifier`,
  `supports_sunroof`, `supports_window_control` flags. Unknown flags stay
  `None` so other regions are unaffected.
* `ApiImplType1`: `open_frunk`, `set_lamp_state`, `start_pet_care`,
  `set_battery_conditioning`, CCS2 climate extras (`air_purifier`,
  `third_row_left_seat`, `third_row_right_seat`), rear-curtain keys on the
  legacy window-curtain body, and `tail_lamp_alert_is_on` /
  `hazard_lamp_alert_is_on` from CCS2 status.
* `KiaUvoApiAU.set_windows_state`: CCS2 vehicles use
  `ccs2/control/windowcurtain` with the seat-based body (RHD-aware).
* EV-only controls (battery conditioning, pet care) are gated on engine type
  and the profile flags and could not be exercised on the ICE GV70.
