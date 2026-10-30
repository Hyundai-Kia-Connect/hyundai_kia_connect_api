"""Tests for GenesisCciApiEU: dispatch, brand constants, and the Genesis
X-Stamp cipher validated against the native libgspa-cipher.so (arm64,
Genesis EU 1.2.1) emulated under Unicorn.

Cipher golden vectors come from the emulated native library, not from this
reimplementation — see re_to_the_bottom_genesis1.4.0/scripts/g1_oracle.py.
"""

import base64
import datetime as dt

from hyundai_kia_connect_api.const import REGION_EUROPE_CCI
from hyundai_kia_connect_api.GenesisCciApiEU import GenesisCciApiEU
from hyundai_kia_connect_api.gspa.cipher_keys import genesis_cipher
from hyundai_kia_connect_api.GspaApiEU import GspaApiEU
from hyundai_kia_connect_api.Token import Token
from hyundai_kia_connect_api.VehicleManager import VehicleManager


def test_dispatch_creates_genesis_cci():
    # The dispatcher indexes REGIONS/BRANDS by raw int (region 9, brand 3).
    api = VehicleManager.get_implementation_by_region_brand(9, 3, "en")
    assert isinstance(api, GenesisCciApiEU)
    assert isinstance(api, GspaApiEU)


def test_genesis_brand_constants():
    api = GenesisCciApiEU(REGION_EUROPE_CCI, 3, "en")
    assert api.ONEAPP_CLIENT_ID == "50e3b8b0-ced5-43b7-8a42-f86ac92fe50e"
    assert api.CCI_API_URL == "https://cci-api-eu.genesis.com"
    assert api.CCI_PACKAGE_ID == "com.genesis.oneapp.eu"
    assert api.GSPA_BASE_URL == "https://gspa-ccs-eu.genesis.com/"
    assert api.LOGIN_FORM_HOST == "https://idpconnect-eu.genesis.com"
    assert api.CIPHER_BRAND == "genesis"
    assert api.DEVICE_ID_HEADER == "X-Device-Id"
    # Region codes for genesis EU dispatch: 0 (native) vs lib STAMP_REGION 1;
    # the stamp payload and IV ("iv.ccsp.stamp.eu") are what the server
    # validates, and the lib keeps 1 (same as Hyundai/Kia).
    assert api.STAMP_REGION == 1


def test_genesis_cipher_ecb_golden_vector():
    cipher = genesis_cipher()
    block = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
    assert cipher.encrypt_block(block).hex() == ("30dd8e05a7c68d18b2e3d02e7a674346")


def test_genesis_cipher_cfb_golden_vector():
    cipher = genesis_cipher()
    iv = b"iv.ccsp.stamp.eu"
    data = b"HELLOWORLD" * 4
    assert cipher.encrypt_cfb(iv, data).hex() == (
        "4474efadd27095e02b5ddfebdec1c98f2535514870e8dd42ed967c0f201e5bfc32c"
        "b5bc26b3df257"
    )


def test_genesis_compute_x_stamp_shape():
    cipher = genesis_cipher()
    stamp = cipher.compute_x_stamp(
        tsid="AAAAAAAAAAAAAAAAAAAAAA",
        epoch_seconds=1700000000,
        user_id="user-1",
    )
    decoded = base64.b64decode(stamp)
    # payload "AAAA...:1700000000:user-1" is 34 bytes -> one CFB block + tail
    assert len(decoded) == len(b"AAAAAAAAAAAAAAAAAAAAAA:1700000000:user-1")


def test_genesis_credential_stamping_fingerprint_keys():
    """The fingerprint constant set for Genesis is registered (#1481)."""
    from hyundai_kia_connect_api.cci_fingerprint import (
        _FINGERPRINT_KEYS,
        BRAND_GENESIS,
    )

    assert _FINGERPRINT_KEYS[BRAND_GENESIS] == (
        "SGcyufLERcZ",
        "g8d7THY41KO",
        "8l1fHRfZZb",
    )


def test_get_stamp_requires_token_fields():
    api = GenesisCciApiEU(REGION_EUROPE_CCI, 3, "en")
    token = Token(
        username="u",
        password="p",
        access_token="Bearer x",
        refresh_token="r",
        device_id="d" * 16,
        valid_until=dt.datetime.now(dt.UTC) + dt.timedelta(hours=1),
        cci_access_token="Bearer cci",
        non_ccs_token="n",
        exchangeable_token="e",
        user_id="user-1",
    )
    stamp, tsid = api._get_stamp(token)
    assert stamp and tsid
    decode = base64.b64decode(tsid + "===")
    assert decode[-1] & 0x07 == 0x06  # version nibble from create_tsid
    assert decode[5:13] == b"\xdd" * 8  # node from the token device_id
