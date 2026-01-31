# Test Fixtures

This directory contains JSON files representing real API response shapes from the Hyundai/Kia Connect API. Tests load these files and verify that the parsing logic in each region's API implementation correctly populates `Vehicle` objects.

## Naming Convention

```
{region}_{brand}_{model}_{year}_{scenario}.json
```

| Component  | Description                         | Examples                       |
| ---------- | ----------------------------------- | ------------------------------ |
| `region`   | Two-letter region code              | `us`, `eu`, `ca`, `au`         |
| `brand`    | Vehicle brand                       | `kia`, `hyundai`               |
| `model`    | Model name (underscores for spaces) | `niro_ev`, `ioniq_5`, `tucson` |
| `year`     | Model year                          | `2020`, `2024`                 |
| `scenario` | What the response represents        | `cached`, `force_refresh`      |

**Examples:**

- `us_kia_niro_ev_2020_cached.json` — Cached state from `cmm/gvi`, no targetSOC
- `us_kia_niro_ev_2020_force_refresh.json` — State after force refresh, includes targetSOC
- `us_hyundai_ioniq_5_2024_cached.json` — (future) Ioniq 5 cached state

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
            "odometer_value": 23456,
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

Tests in the parent `tests/` directory use helpers from `conftest.py`:

- `discover_fixtures("us_")` — finds all fixture files starting with `us_`
- `load_fixture(filename)` — loads and parses a JSON fixture
- `get_fixture_expected(data)` — extracts the `_fixture_meta.expected` block
- `get_fixture_meta(data)` — extracts the full `_fixture_meta` block

Tests are parameterized over all matching fixtures, so adding a new JSON file automatically creates new test cases.

## Contributing a Fixture

1. Capture the API response for your vehicle (from `cmm/gvi` or equivalent).
2. Strip any sensitive data (VIN, location, account info).
3. Name the file following the convention above.
4. Add the `_fixture_meta` block with expected values.
5. Run `pytest tests/` to verify your fixture is picked up and tests pass.
