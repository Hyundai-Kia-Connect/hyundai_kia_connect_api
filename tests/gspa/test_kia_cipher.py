"""Kia X-Stamp cipher — bit-exact vector tests."""

from hyundai_kia_connect_api.gspa.cipher_keys import IVS, kia_cipher


def test_kia_encrypt_block_vector_p1():
    """P1: incremental input → known ciphertext."""
    cipher = kia_cipher()
    pt = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
    ct = cipher.encrypt_block(pt)
    assert ct.hex() == "b4bba595c23c313c9c47e0e272048546"


def test_kia_encrypt_block_vector_p2():
    """P2: descending input → known ciphertext."""
    cipher = kia_cipher()
    pt = bytes.fromhex("5554535251504f4e4d4c4b4a49484746")
    ct = cipher.encrypt_block(pt)
    assert ct.hex() == "036ac2d873a6e3da9d6001567a446d5a"


def test_kia_encrypt_block_zeros():
    """All-zeros block → known ciphertext."""
    cipher = kia_cipher()
    ct = cipher.encrypt_block(b"\x00" * 16)
    assert ct.hex() == "1434c739f62ea7b9a8c8dbaaaa7772b9"


def test_kia_encrypt_cfb_with_stamp_iv():
    """CFB-128 with EU stamp IV produces deterministic output."""
    cipher = kia_cipher()
    iv = IVS[1]  # b"iv.ccsp.stamp.eu"
    plaintext = b"test-payload-16"
    ct = cipher.encrypt_cfb(iv, plaintext)
    assert cipher.encrypt_cfb(iv, plaintext) == ct
    pt_back = cipher.encrypt_cfb(iv, ct)
    assert pt_back[: len(plaintext)] == plaintext


def test_kia_x_stamp_format():
    """X-Stamp matches the pinned bit-exact vector for a fixed payload."""
    import base64

    cipher = kia_cipher()
    stamp = cipher.compute_x_stamp(
        region=1,
        tsid="AAAAAAAAAAAAAAAA",
        epoch_seconds=1700000000,
        user_id="user123",
    )
    assert stamp == "gfWUhknc2T5DPddAGGiGTqldYz6r2l7P1p/k6izoXLd6ggo="
    decoded = base64.b64decode(stamp)
    assert len(decoded) == 35  # streaming CFB: ct length == pt length


def test_kia_cipher_single_phase_13_rounds():
    """Kia has one phase of 13 rounds (global_round 0..12)."""
    cipher = kia_cipher()
    spec = cipher._spec
    assert len(spec["phases"]) == 1
    assert spec["phases"][0] == ("SBOX", "LINEAR", 13)
    assert spec["final_group"] == "BYTE"


def test_kia_masks_loaded():
    """Kia cipher loads mask LUTs (52 entries: 13 rounds × 4 cols)."""
    cipher = kia_cipher()
    assert cipher._masks_parsed is not None
    assert len(cipher._masks_parsed) == 52
    for r, c in cipher._masks_parsed:
        assert 0 <= r <= 12
        assert 0 <= c <= 3


def test_kia_params_accessible_via_importlib():
    """Parameter files are accessible via importlib.resources (wheel-safe)."""
    from importlib.resources import files

    import hyundai_kia_connect_api.gspa as gspa_pkg

    kia_params = files(gspa_pkg).joinpath("kia_cipher_params.json")
    hyundai_params = files(gspa_pkg).joinpath("hyundai_cipher_params.json")
    kia_masks = files(gspa_pkg).joinpath("kia_mask_luts.json")
    assert kia_params.is_file()
    assert hyundai_params.is_file()
    assert kia_masks.is_file()
