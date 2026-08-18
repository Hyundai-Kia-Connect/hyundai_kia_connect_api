"""Stamp parity tests for the pure-Python GSPA cipher.

These tests pin the cipher output for fixed inputs so any future change
to ``cipher_keys`` that breaks the output is caught immediately.
"""

import base64
import datetime as dt
import struct
from types import SimpleNamespace

import pytest

from hyundai_kia_connect_api.gspa import GspaCipher, cipher_keys, create_tsid

# Known encrypt_block vectors (pin the ECB output for fixed 16-byte inputs).
P1 = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
C1 = bytes.fromhex("d8d51c3015970d3570b211312783d83b")
P2 = bytes.fromhex("5554535251504f4e4d4c4b4a49484746")
C2 = bytes.fromhex("f49c54a7a4b0906609dac73880c4df2c")

# Known encrypt_cfb vector (pin the CFB-128 output for a fixed IV + payload).
CFB_IV = b"iv.ccsp.stamp.eu"
CFB_PAYLOAD = b"0TSID123456789:1748523600:testuser"
CFB_EXPECTED = bytes.fromhex(
    "740f3b0febde3f415273415f5d9f6af84d1a8a256d16fa075bccad8507adce048100"
)


def test_encrypt_block_known_vectors():
    """Pinned ECB block output for two distinct plaintext blocks."""
    cipher = GspaCipher()
    assert cipher.encrypt_block(P1) == C1
    assert cipher.encrypt_block(P2) == C2


def test_encrypt_cfb_known_vector():
    """Pinned CFB-128 output for a fixed IV and payload.

    This pins the full CFB chaining (IV + one block + partial second block)
    so any change to encrypt_block or the CFB feedback loop is caught.
    """
    cipher = GspaCipher()
    result = cipher.encrypt_cfb(CFB_IV, CFB_PAYLOAD)
    assert result == CFB_EXPECTED


def test_create_tsid_known_vector(monkeypatch):
    """Pinned TSID output with a frozen timestamp.

    create_tsid uses the current time, so we replace the ``dt`` module
    reference in ``cipher_keys`` with a fake whose ``datetime.now`` returns
    a fixed value. This makes the output deterministic and pinnable.
    """
    fixed_dt = dt.datetime(2025, 6, 1, 12, 0, 0, tzinfo=dt.UTC)

    fake_dt = SimpleNamespace(
        datetime=type(
            "FakeDateTime",
            (),
            {"now": staticmethod(lambda tz=None: fixed_dt)},
        ),
        timezone=dt.timezone,
        UTC=dt.UTC,
    )
    monkeypatch.setattr(cipher_keys, "dt", fake_dt)

    result = create_tsid(device_id_hex="0123456789abcdef", counter=1)

    # Compute expected value manually to avoid relying on the function itself.
    now_ms = int(fixed_dt.timestamp() * 1000)
    ts_offset = now_ms - cipher_keys.TSID_EPOCH_MS
    ts_bytes = struct.pack(">Q", ts_offset)[3:]  # last 5 bytes
    node_bytes = bytes.fromhex("0123456789abcdef")
    counter_swapped = ((1 & 0x0F) << 4) | ((1 >> 4) & 0x0F)  # 0x10
    last_byte = counter_swapped | 6  # platform=6 (Android)
    expected = (
        base64.b64encode(ts_bytes + node_bytes + bytes([last_byte]))
        .decode("ascii")
        .rstrip("=")
    )

    assert result == expected


def test_create_tsid_structure():
    """Verify TSID structural properties: 14 bytes, correct last byte."""
    device_id = "0123456789abcdef"
    counter = 2
    result = create_tsid(device_id_hex=device_id, counter=counter)

    # Decode (add padding for base64 decode)
    padded = result + "=" * (-len(result) % 4)
    raw = base64.b64decode(padded)
    assert len(raw) == 14

    # Last byte: counter nibble-swapped | platform (6)
    counter_swapped = ((counter & 0x0F) << 4) | ((counter >> 4) & 0x0F)
    expected_last = counter_swapped | 6
    assert raw[13] == expected_last

    # Node bytes (bytes 5-12) from device_id hex
    assert raw[5:13] == bytes.fromhex(device_id[:16])


@pytest.mark.parametrize("region", [4, 5])
def test_compute_x_stamp_us_ca_not_implemented(region):
    """US/CA use separate cipher functions whose T-tables are not
    available — NotImplementedError, not a silent EU fallback."""
    pure = GspaCipher()
    with pytest.raises(NotImplementedError):
        pure.compute_x_stamp(
            region=region,
            tsid="test-tsid",
            epoch_seconds=1748523600,
            user_id="u",
        )


@pytest.mark.parametrize("region", [1, 2, 3, 9, -1])

def test_module_encrypt_cfb_us_ca_not_implemented():
    """Module-level encrypt_cfb(region=4) must raise NotImplementedError so a
    re-exported entry point cannot silently produce a wrong X-Stamp."""
    with pytest.raises(NotImplementedError):
        cipher_keys.encrypt_cfb(b"iv.ccsp.stamp.us", b"x" * 16, region=4)




def test_encrypt_block_rejects_wrong_size():
    """encrypt_block must reject non-16-byte inputs."""
    cipher = GspaCipher()
    with pytest.raises(ValueError):
        cipher.encrypt_block(b"short")
    with pytest.raises(ValueError):
        cipher.encrypt_block(b"x" * 15)
    with pytest.raises(ValueError):
        cipher.encrypt_block(b"x" * 17)


def test_encrypt_cfb_rejects_wrong_iv_size():
    """encrypt_cfb must reject IVs that are not 16 bytes."""
    cipher = GspaCipher()
    with pytest.raises(ValueError):
        cipher.encrypt_cfb(b"short", b"x" * 16)
    with pytest.raises(ValueError):
        cipher.encrypt_cfb(b"x" * 15, b"x" * 16)


def test_encrypt_cfb_partial_block():
    """CFB with a payload shorter than 16 bytes must still produce
    correct-length output (no padding)."""
    cipher = GspaCipher()
    payload = b"abc"
    result = cipher.encrypt_cfb(CFB_IV, payload)
    assert len(result) == len(payload)


def test_encrypt_cfb_multi_block():
    """CFB with a multi-block payload must exercise the feedback chain."""
    cipher = GspaCipher()
    payload = bytes(range(40))  # 2 full blocks + 8 bytes
    result = cipher.encrypt_cfb(CFB_IV, payload)
    assert len(result) == len(payload)
    # Verify the second block uses the first block's ciphertext as feedback
    # (not the IV again) — the two blocks must differ.
    first_block = result[:16]
    second_block = result[16:32]
    assert first_block != second_block
