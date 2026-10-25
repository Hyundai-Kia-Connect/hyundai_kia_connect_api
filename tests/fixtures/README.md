# Test Fixtures

This directory contains JSON files representing real API response shapes from the Hyundai/Kia Connect API.

The top-level files are status payloads, stored without the outer response envelope. `tests/test_vehicle_update_snapshots.py` serves each one through a cached-state update scenario in `updates/` (see "Cached-state updates" below) and compares the requests sent and the full parsed `Vehicle` to a syrupy snapshot in `tests/__snapshots__/`. A status fixture that no scenario references fails the test. Don't write per-field parsing tests for a fixture: the snapshot already covers every field.

## Status fixtures by region

| Region       | Implementation (update path) | Fixture Prefix          | Response Structure                                 |
| ------------ | ---------------------------- | ----------------------- | -------------------------------------------------- |
| US (Kia)     | `KiaUvoApiUSA`               | `us_kia_`               | `lastVehicleInfo.vehicleStatusRpt.vehicleStatus.*` |
| US (Hyundai) | `HyundaiBlueLinkApiUSA`      | `us_hyundai_`           | `vehicleStatus.*`                                  |
| EU           | `KiaUvoApiEU`                | `eu_kia_` (no `ccs2`)   | `vehicleStatus.*`                                  |
| EU (CCS2)    | `KiaUvoApiEU`                | `eu_kia_` (with `ccs2`) | `Green.*`, `Cabin.*`, `Body.*`                     |
| EU (GSPA)    | `KiaCciApiEU`                | `eu_kia_gspa_`          | CCS2-style stored status                           |
| BR           | `HyundaiBlueLinkApiBR`       | `br_`                   | `resMsg.state.Vehicle` (CCS2)                      |
| CA           | `KiaUvoApiCA`                | `ca_`                   | `status.*`                                         |
| AU           | `KiaUvoApiAU`                | `au_`                   | `status.*`                                         |
| CN           | `KiaUvoApiCN`                | `cn_`                   | `status.*`                                         |

## Vehicle lists

`vehicle_lists/` holds one real vehicle-list response per implementation, named after its key in `IMPLEMENTATIONS` in `tests/test_vehicle_list_snapshots.py` (`eu_hyundai.json`, `usa_kia.json`, ...). That test runs `get_vehicles` and `refresh_vehicles` on each one and snapshots the requests sent and the Vehicle fields set.

Use captured responses only, such as the `Get Vehicles Response` debug log line, which Type1 (EU/AU/CN/IN), CA, Kia USA and Hyundai USA print. BR and the EU CCI (GSPA) classes don't log this response. Several captures may be merged into one file's vehicle list. Each file has a `_fixture_meta` block with `region`, `brand`, `endpoint`, `vehicles`, `source` (where each capture came from) and `description`. Replace VINs, vehicle ids, keys and registration ids with placeholders such as `TESTVIN0000000001` and `VEHICLE-1`, and drop account, driver and address blocks. Then run `pytest tests/test_vehicle_list_snapshots.py --snapshot-update`.

## Cached-state updates

`updates/` holds one scenario per update, run by `tests/test_vehicle_update_snapshots.py` through `VehicleManager.update_vehicle_with_cached_state`. A fake HTTP transport answers each request by method and URL path, and the requests sent plus the full parsed Vehicle are snapshotted. A request with no scenario entry, or a scenario response that is never requested, fails the test.

```json
{
    "_fixture_meta": {
        "status": "...",
        "follow_up_sources": "...",
        "not_captured": [],
        "description": "..."
    },
    "implementation": { "region": "Europe", "brand": "Kia" },
    "vehicle": {
        "id": "VEHICLE-1",
        "engine_type": "EV",
        "ccu_ccs2_protocol_support": 0
    },
    "responses": {
        "GET /vehicles/VEHICLE-1/status/latest": {
            "body": {
                "retCode": "S",
                "resMsg": {
                    "vehicleStatusInfo": "$fixture:eu_kia_ev6_2023_with_soc.json"
                }
            }
        },
        "POST /vehicles/VEHICLE-1/drvhistory": [
            { "body": "$fixture:updates/responses/eu_drvhistory_alltime.json" },
            { "body": "..." }
        ]
    }
}
```

- A response is `{"status": 200, "body": ...}`. A list is answered in request order.
- `"$fixture:<path under tests/fixtures>[#key]"` inlines a fixture without its `_fixture_meta`. `{"$fixture": "<ref>", "field": ...}` inlines it and overrides fields.
- Status payloads stay in the top-level status fixtures. Follow-up reads (location, driving info, service, charge limits, trips) are real captured responses in `updates/responses/`, each with a `_fixture_meta.source`.
- Only when no real capture exists for a read, answer it with `{"status": 503, "body": null}` and list it in `_fixture_meta.not_captured`.

## Naming Convention

```
{region}_{brand}_{model}_{year}_{scenario}.json
```

| Component  | Description                         | Examples                                      |
| ---------- | ----------------------------------- | --------------------------------------------- |
| `region`   | Two-letter region code              | `us`, `eu`, `ca`, `au`, `cn`                  |
| `brand`    | Vehicle brand                       | `kia`, `hyundai`                              |
| `model`    | Model name (underscores for spaces) | `niro_ev`, `ioniq_5`, `ev6`                   |
| `year`     | Model year                          | `2020`, `2024`                                |
| `scenario` | What the response represents        | `cached`, `force_refresh`, `with_soc`, `ccs2` |

**Current fixtures:**

- `us_kia_niro_ev_2020_cached.json` — US Kia cached state, no targetSOC
- `us_kia_niro_ev_2020_force_refresh.json` — US Kia after force refresh, with targetSOC
- `us_hyundai_ioniq_5_2024_cached.json` — US Hyundai BlueLinkAPI, with targetSOC
- `eu_kia_ev6_2023_with_soc.json` — EU standard protocol, with targetSOC
- `eu_kia_ev9_2024_ccs2.json` — EU CCS2 protocol (newer vehicles), with TargetSoC
- `ca_kia_niro_ev_2022_cached.json` — CA cached status (charge limits via separate call)
- `au_hyundai_ioniq_5_2023_with_soc.json` — AU with targetSOC
- `cn_kia_ev6_2024_with_soc.json` — CN with targetSOC

## File Structure

Each fixture file is a JSON object matching the structure returned by the API endpoint (after extracting from the outer response envelope). It must also include a `_fixture_meta` block at the top level:

```json
{
    "_fixture_meta": {
        "vehicle": "Kia Niro EV",
        "year": 2020,
        "region": "US",
        "brand": "Kia",
        "endpoint": "cmm/gvi",
        "description": "Brief description of what this fixture represents.",
        "has_target_soc": false
    },
    "vehicleConfig": { "...": "..." },
    "lastVehicleInfo": { "...": "..." }
}
```

### `_fixture_meta` Fields

| Field            | Required | Description                                   |
| ---------------- | -------- | --------------------------------------------- |
| `vehicle`        | Yes      | Human-readable vehicle name                   |
| `year`           | Yes      | Model year                                    |
| `region`         | Yes      | Region code (US, EU, CA, AU, CN)              |
| `brand`          | Yes      | Kia or Hyundai                                |
| `endpoint`       | Yes      | API endpoint the response comes from          |
| `description`    | Yes      | What makes this fixture interesting/unique    |
| `has_target_soc` | Yes      | Whether targetSOC data is present in evStatus |

## How Tests Use Fixtures

Tests use helpers from `tests/fixture_helpers.py`:

- `discover_fixtures("us_kia_")` — finds all fixture files starting with `us_kia_`
- `load_fixture(filename)` — loads and parses a JSON fixture

To cover a parsing edge case (a missing block, an unusual value), add a small status fixture such as `eu_kia_ev6_unset_timers.json` and an update scenario for it instead of writing a new test.

## Contributing a Fixture

1. Capture the real API response for your vehicle, for example from the `get_cached_vehicle_status response` debug log line. Never invent a response body.
2. Strip any sensitive data (VINs, vehicle ids, keys, coordinates including altitude, account info).
3. Name the file following the convention above and add the `_fixture_meta` block.
4. Add an update scenario in `updates/` that references it, with real captures for the follow-up reads in `updates/responses/`.
5. Run `pytest tests/test_vehicle_update_snapshots.py --snapshot-update` to create its snapshot, then review the generated snapshot to check every parsed value is correct.

### Region-Specific Notes

- **US Kia** (`us_kia_*`): Uses deeply nested `lastVehicleInfo.vehicleStatusRpt.vehicleStatus.*` paths. targetSOC is at `evStatus.targetSOC`.
- **US Hyundai** (`us_hyundai_*`): Uses `vehicleStatus.*` directly. targetSOC is at `evStatus.reservChargeInfos.targetSOClist`.
- **EU/AU/CN** (`eu_*`, `au_*`, `cn_*`): Use `vehicleStatus.*` (EU) or `status.*` (AU/CN). Temperature is hex-encoded (e.g., `"10H"`). targetSOC is at `evStatus.reservChargeInfos.targetSOClist`.
- **CA** (`ca_*`): Uses `status.*` paths. Charge limits come from a separate `evc/selsoc` call, not the main status response.
- **CCS2** (`eu_kia_*ccs2*`, `br_*`): Completely different structure using `Green.*`, `Cabin.*`, `Body.*`, `Chassis.*`. TargetSoC is direct scalars (`Green.ChargingInformation.TargetSoC.Standard/Quick`), not arrays. Door lock logic is inverted.
