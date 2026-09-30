# Test Fixtures

This directory contains JSON files representing real API response shapes from the Hyundai/Kia Connect API. Tests load these files and verify that the parsing logic in each region's API implementation correctly populates `Vehicle` objects.

## Supported Parsers

Each fixture names its parser in `_fixture_meta.parser`. The parsers are
registered in `PARSERS` in [`tests/fixture_helpers.py`](../fixture_helpers.py):

| `parser`      | API Class               | Parse method                      | Response Structure                                 |
| ------------- | ----------------------- | --------------------------------- | -------------------------------------------------- |
| `usa_kia`     | `KiaUvoApiUSA`          | `_update_vehicle_properties`      | `lastVehicleInfo.vehicleStatusRpt.vehicleStatus.*` |
| `usa_hyundai` | `HyundaiBlueLinkApiUSA` | `_update_vehicle_properties`      | `vehicleStatus.*`                                  |
| `eu`          | `KiaUvoApiEU`           | `_update_vehicle_properties`      | `vehicleStatus.*`                                  |
| `ccs2`        | `ApiImplType1`          | `_update_vehicle_properties_ccs2` | `Green.*`, `Cabin.*`, `Body.*`                     |
| `eu_kia_cci`  | `KiaCciApiEU`           | `_update_vehicle_properties_ccs2` | CCS2 tree from GSPA stored-status                  |
| `br`          | `HyundaiBlueLinkApiBR`  | `_update_vehicle_properties_ccs2` | `resMsg.state.Vehicle` (CCS2)                      |
| `ca`          | `KiaUvoApiCA`           | `_update_vehicle_properties_base` | `status.*`                                         |
| `au`          | `KiaUvoApiAU`           | `_update_vehicle_properties`      | `status.*`                                         |
| `cn`          | `KiaUvoApiCN`           | `_update_vehicle_properties`      | `status.*`                                         |

A new API needs one `PARSERS` entry; a new fixture for an existing API needs
no code at all.

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

The filename is for humans only; tests route on `_fixture_meta.parser`.

## File Structure

Each fixture file is a JSON object matching the structure returned by the API endpoint (after extracting from the outer response envelope). It must also include a `_fixture_meta` block at the top level:

```json
{
    "_fixture_meta": {
        "parser": "usa_kia",
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
            "odometer": 23456,
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

| Field            | Required | Description                                                                     |
| ---------------- | -------- | ------------------------------------------------------------------------------- |
| `parser`         | Yes      | Key into `PARSERS` (see table above)                                            |
| `description`    | Yes      | What makes this fixture interesting/unique                                      |
| `vehicle`        | No       | Human-readable vehicle name                                                     |
| `year`           | No       | Model year                                                                      |
| `region`         | No       | Region code (US, EU, CA, AU, CN, BR)                                            |
| `brand`          | No       | Kia, Hyundai or Genesis                                                         |
| `endpoint`       | No       | API endpoint the response comes from                                            |
| `has_target_soc` | No       | Whether targetSOC data is present in evStatus                                   |
| `expected`       | No       | Public `Vehicle` attribute names mapped to their expected (JSON-encoded) values |

`expected` values are compared after the same conversion the snapshots use:
times and datetimes become ISO strings (`"22:30:00"`), enums become `str()`.

## How Tests Use Fixtures

[`tests/test_fixture_parsing.py`](../test_fixture_parsing.py) runs against
every fixture in this directory:

- `test_fixture_meta` — `_fixture_meta` has the required keys and a known parser.
- `test_expected_fields` — every `expected` value matches the parsed `Vehicle`.
- `test_raw_payload_retained` — the parser keeps the raw payload on `vehicle.data`.
- `test_vehicle_snapshot` — the full parsed `Vehicle` matches the syrupy snapshot
  in `tests/__snapshots__/test_fixture_parsing.ambr`.

Other tests can reuse a fixture with `parse_fixture(filename)` (returns
`(vehicle, payload)`), `load_fixture(filename)`, or build an API instance with
`PARSERS["ccs2"].api()`.

## Contributing a Fixture

1. Capture the API response for your vehicle (from `cmm/gvi`, `lststatus`, or equivalent endpoint).
2. Strip any sensitive data (VIN, real GPS coordinates, account info).
3. Name the file following the convention above.
4. Add the `_fixture_meta` block with `parser`, `description`, and the
   `expected` values you have checked against the real app/vehicle.
5. Run `pytest tests/test_fixture_parsing.py --snapshot-update` to record the
   snapshot, review the generated `.ambr` diff, then run `pytest`.

### Region-Specific Notes

- **US Kia** (`us_kia_*`): Uses deeply nested `lastVehicleInfo.vehicleStatusRpt.vehicleStatus.*` paths. targetSOC is at `evStatus.targetSOC`.
- **US Hyundai** (`us_hyundai_*`): Uses `vehicleStatus.*` directly. targetSOC is at `evStatus.reservChargeInfos.targetSOClist`.
- **EU/AU/CN** (`eu_*`, `au_*`, `cn_*`): Use `vehicleStatus.*` (EU) or `status.*` (AU/CN). Temperature is hex-encoded (e.g., `"10H"`). targetSOC is at `evStatus.reservChargeInfos.targetSOClist`.
- **CA** (`ca_*`): Uses `status.*` paths. Charge limits come from a separate `evc/selsoc` call, not the main status response.
- **CCS2** (`eu_kia_ev9_*`): Completely different structure using `Green.*`, `Cabin.*`, `Body.*`, `Chassis.*`. TargetSoC is direct scalars (`Green.ChargingInformation.TargetSoC.Standard/Quick`), not arrays. Door lock logic is inverted.
