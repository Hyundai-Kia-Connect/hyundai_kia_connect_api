"""KiaUvoApiAU stamps climate_duration_setting on its vehicles.

The AU start_climate override sends ``ignitionDuration`` (server default 5
when the caller passes none — kia_uvo#2063 reporter log), so the integration
can offer the duration choice on AU vehicles too. EU coverage comes from the
update-snapshot scenarios, where the profile read sets the same flag from
``clmtCtrlOption``.
"""

from hyundai_kia_connect_api.KiaUvoApiAU import KiaUvoApiAU


def test_au_vehicle_supports_climate_duration() -> None:
    impl = KiaUvoApiAU.__new__(KiaUvoApiAU)
    vehicle = impl._vehicle_from_spa_entry(
        {
            "vehicleId": "vehicle-1",
            "nickname": "test",
            "vehicleName": "Ioniq 5",
            "regDate": "20230101",
            "vin": "TESTVIN0000000001",
            "type": "EV",
            "ccuCCS2ProtocolSupport": 1,
        }
    )

    assert vehicle.supports.climate_duration_setting is True
