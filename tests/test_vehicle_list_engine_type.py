"""Vehicle-list ``type`` codes map to ENGINE_TYPES for Type1 regions and BR."""

from unittest.mock import MagicMock, patch

import pytest

from hyundai_kia_connect_api.const import ENGINE_TYPES
from hyundai_kia_connect_api.HyundaiBlueLinkApiBR import HyundaiBlueLinkApiBR
from hyundai_kia_connect_api.KiaUvoApiEU import KiaUvoApiEU
from hyundai_kia_connect_api.Token import Token

CASES = {
    "GN": ENGINE_TYPES.ICE,
    "EV": ENGINE_TYPES.EV,
    "PHEV": ENGINE_TYPES.PHEV,
    "PE": ENGINE_TYPES.PHEV,
    "HV": ENGINE_TYPES.HEV,
    "XX": None,
}


def _vehicles_response() -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = {
        "retCode": "S",
        "resCode": "0000",
        "resMsg": {
            "vehicles": [
                {
                    "vehicleId": f"veh-{code}",
                    "nickname": "car",
                    "vehicleName": "model",
                    "regDate": "2024-01-01",
                    "vin": "TESTVIN",
                    "type": code,
                }
                for code in CASES
            ]
        },
    }
    return resp


@pytest.mark.parametrize(
    "api",
    [
        KiaUvoApiEU(region=1, brand=1, language="en"),
        HyundaiBlueLinkApiBR(region=8, brand=2),
    ],
    ids=["eu", "br"],
)
def test_get_vehicles_maps_engine_type(api):
    with (
        patch.object(api, "_get_authenticated_headers", return_value={}),
        patch.object(api.session, "get", return_value=_vehicles_response()),
    ):
        vehicles = api.get_vehicles(Token(access_token="a", device_id="d"))

    assert {v.id: v.engine_type for v in vehicles} == {
        f"veh-{code}": expected for code, expected in CASES.items()
    }
