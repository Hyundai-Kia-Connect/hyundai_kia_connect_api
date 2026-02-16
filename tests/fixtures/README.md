# Test Fixtures

This directory contains JSON files representing real API response shapes from the Hyundai/Kia Connect API. Tests load these files and verify that the parsing logic in each region's API implementation correctly populates `Vehicle` objects.

## Supported Regions & API Classes

| Region       | API Class               | Test File                                 | Fixture Prefix           | Response Structure                                 |
| ------------ | ----------------------- | ----------------------------------------- | ------------------------ | -------------------------------------------------- |
| US (Kia)     | `KiaUvoApiUSA`          | `test_usa_vehicle_properties.py`          | `us_kia_`                | `lastVehicleInfo.vehicleStatusRpt.vehicleStatus.*` |
| US (Hyundai) | `HyundaiBlueLinkApiUSA` | `test_bluelink_usa_vehicle_properties.py` | `us_hyundai_`            | `vehicleStatus.*`                                  |
| EU           | `KiaUvoApiEU`           | `test_eu_vehicle_properties.py`           | `eu_kia_ev6_` (non-CCS2) | `vehicleStatus.*`                                  |
| EU (CCS2)    | `ApiImplType1`          | `test_ccs2_vehicle_properties.py`         | `eu_kia_ev9_`            | `Green.*`, `Cabin.*`, `Body.*`                     |
| CA           | `KiaUvoApiCA`           | `test_ca_vehicle_properties.py`           | `ca_`                    | `status.*`                                         |
| AU           | `KiaUvoApiAU`           | `test_au_vehicle_properties.py`           | `au_`                    | `status.*`                                         |
| CN           | `KiaUvoApiCN`           | `test_cn_vehicle_properties.py`           | `cn_`                    | `status.*`                                         |

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
        "has_target_soc": false,
        "expected": {
            "ev_battery_percentage": 68,
            "ev_charge_limits_dc": null,
            "ev_charge_limits_ac": null,
            "car_battery_percentage": 87,
            "engine_is_running": false,
            "is_locked": true,
            "ev_battery_is_charging": false,
            "ev_battery_is_plugged_in": 0
        }
    },
    "vehicleConfig": { "...": "..." },
    "lastVehicleInfo": { "...": "..." }
}
```

### `_fixture_meta` Fields

| Field            | Required | Description                                          |
| ---------------- | -------- | ---------------------------------------------------- |
| `vehicle`        | Yes      | Human-readable vehicle name                          |
| `year`           | Yes      | Model year                                           |
| `region`         | Yes      | Region code (US, EU, CA, AU, CN)                     |
| `brand`          | Yes      | Kia or Hyundai                                       |
| `endpoint`       | Yes      | API endpoint the response comes from                 |
| `description`    | Yes      | What makes this fixture interesting/unique           |
| `has_target_soc` | Yes      | Whether targetSOC data is present in evStatus        |
| `expected`       | Yes      | Dict of Vehicle field names to their expected values |

## How Tests Use Fixtures

Tests in the parent `tests/` directory use helpers from `fixture_helpers.py`:

- `discover_fixtures("us_kia_")` — finds all fixture files starting with `us_kia_`
- `load_fixture(filename)` — loads and parses a JSON fixture
- `get_fixture_expected(data)` — extracts the `_fixture_meta.expected` block
- `get_fixture_meta(data)` — extracts the full `_fixture_meta` block

Tests are parameterized over all matching fixtures, so adding a new JSON file automatically creates new test cases.

## Contributing a Fixture

1. Capture the API response for your vehicle (from `cmm/gvi`, `lststatus`, or equivalent endpoint).
2. Strip any sensitive data (VIN, real GPS coordinates, account info).
3. Name the file following the convention above — the prefix must match what the corresponding test file discovers.
4. Add the `_fixture_meta` block with expected values.
5. Run `pytest tests/` to verify your fixture is picked up and tests pass.

### Region-Specific Notes

- **US Kia** (`us_kia_*`): Uses deeply nested `lastVehicleInfo.vehicleStatusRpt.vehicleStatus.*` paths. targetSOC is at `evStatus.targetSOC`.
- **US Hyundai** (`us_hyundai_*`): Uses `vehicleStatus.*` directly. targetSOC is at `evStatus.reservChargeInfos.targetSOClist`.
- **EU/AU/CN** (`eu_*`, `au_*`, `cn_*`): Use `vehicleStatus.*` (EU) or `status.*` (AU/CN). Temperature is hex-encoded (e.g., `"10H"`). targetSOC is at `evStatus.reservChargeInfos.targetSOClist`.
- **CA** (`ca_*`): Uses `status.*` paths. Charge limits come from a separate `evc/selsoc` call, not the main status response.
- **CCS2** (`eu_kia_ev9_*`): Completely different structure using `Green.*`, `Cabin.*`, `Body.*`, `Chassis.*`. TargetSoC is direct scalars (`Green.ChargingInformation.TargetSoC.Standard/Quick`), not arrays. Door lock logic is inverted.
