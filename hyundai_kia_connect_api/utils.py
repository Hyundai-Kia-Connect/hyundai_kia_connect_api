from .const import (BRAND_HYUNDAI, BRAND_KIA, BRANDS, REGION_CANADA,
                    REGION_EUROPE, REGION_USA, REGIONS)
from .HyundaiBlueLinkAPIUSA import HyundaiBlueLinkAPIUSA
from .KiaUvoApiCA import KiaUvoApiCA
from .KiaUvoApiEU import KiaUvoApiEU
from .KiaUvoApiImpl import KiaUvoApiImpl
from .KiaUvoAPIUSA import KiaUvoAPIUSA


def get_implementation_by_region_brand(
    region: int,
    brand: int,
    username: str,
    password: str,
    pin: str = "",
) -> KiaUvoApiImpl:  # pylint: disable=too-many-arguments
    if REGIONS[region] == REGION_CANADA:
        return KiaUvoApiCA(
            username, password, region, brand, pin
        )
    elif REGIONS[region] == REGION_EUROPE:
        return KiaUvoApiEU(
            username, password, region, brand, pin
        )
    elif REGIONS[region] == REGION_USA and BRANDS[brand] == BRAND_HYUNDAI:
        return HyundaiBlueLinkAPIUSA(
            username, password, region, brand, pin
        )
    elif REGIONS[region] == REGION_USA and BRANDS[brand] == BRAND_KIA:
        return KiaUvoAPIUSA(
            username, password, region, brand, pin
        )


def get_child_value(data, key):
    value = data
    for x in key.split("."):
        try:
            value = value[x]
        except:
            try:
                value = value[int(x)]
            except:
                value = None
    return value
