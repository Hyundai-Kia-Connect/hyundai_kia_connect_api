"""Every region's login() takes (username, password, pin) in base-class order.

VehicleManager.check_and_refresh_token re-logs in with
``self.api.login(self.username, self.password, self.pin)``, passing pin
positionally. AU, BR, CN and IN used to declare an unused ``otp_handler``
parameter before ``pin``, so the pin landed in ``otp_handler`` and the new
token was created with ``pin=None``.
"""

import inspect

import pytest

from hyundai_kia_connect_api.ApiImpl import ApiImpl
from hyundai_kia_connect_api.GspaApiEU import GspaApiEU
from hyundai_kia_connect_api.HyundaiBlueLinkApiBR import HyundaiBlueLinkApiBR
from hyundai_kia_connect_api.HyundaiBlueLinkApiUSA import HyundaiBlueLinkApiUSA
from hyundai_kia_connect_api.KiaUvoApiAU import KiaUvoApiAU
from hyundai_kia_connect_api.KiaUvoApiCA import KiaUvoApiCA
from hyundai_kia_connect_api.KiaUvoApiCN import KiaUvoApiCN
from hyundai_kia_connect_api.KiaUvoApiEU import KiaUvoApiEU
from hyundai_kia_connect_api.KiaUvoApiIN import KiaUvoApiIN


def _positional_params(cls) -> list[str]:
    return [
        name
        for name, p in inspect.signature(cls.login).parameters.items()
        if p.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    ]


@pytest.mark.parametrize(
    "cls",
    [
        GspaApiEU,
        HyundaiBlueLinkApiBR,
        HyundaiBlueLinkApiUSA,
        KiaUvoApiAU,
        KiaUvoApiCA,
        KiaUvoApiCN,
        KiaUvoApiEU,
        KiaUvoApiIN,
    ],
)
def test_login_params_match_base(cls):
    assert _positional_params(cls)[:4] == _positional_params(ApiImpl)
