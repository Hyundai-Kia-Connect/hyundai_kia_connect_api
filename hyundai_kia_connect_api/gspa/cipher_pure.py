"""GSPA AES-128 ECB block cipher — pure-Python, stdlib-only.

Implements the GSPA block cipher used for X-Stamp computation:
  - 13 rounds of T-table lookups (ShiftRows + SubBytes + MixColumns +
    AddRoundKey combined), each round using 16 X4-like sub-tables and 16
    X27-like sub-tables;
  - a final x8 round (SubBytes + ShiftRows + AddRoundKey, no MixColumns).

Rounds 0-7 use the X4/X27 tables (sub-tables 0-127).  Rounds 8-12 use the
GAP/POST_X27 tables (sub-tables 0-79).  The x8 final round uses the X8
byte-tables.

All table data lives in ``ttables.py``; this module is stdlib-only.

CFB-128 mode (``encrypt_cfb``), ``create_tsid`` and ``compute_x_stamp``
provide the full X-Stamp computation surface for EU-cipher regions
{1 (EU), 2 (KR), 3 (EU-alt), 9 (JP), -1 (SA)}.  US (4) and CA (5) use
separate cipher functions whose T-tables are not available; calling
``compute_x_stamp`` for those regions raises ``NotImplementedError`` rather
than silently falling back to the EU tables (which would produce wrong
X-Stamps).
"""

import base64
import datetime as dt
import os
import struct
import time

from .ttables import GAP, IVS, POST_X27, X4, X8, X27

# ShiftRows gather order — applied in-place at the start of every round
# (including the virtual ShiftRows of the x8 final round).
#   state_after[i] = state_before[SR_POSITIONS[i]]
SR_POSITIONS = [0, 5, 10, 15, 4, 9, 14, 3, 8, 13, 2, 7, 12, 1, 6, 11]

# Round structure: (x4_tables, x27_tables, num_rounds)
# Rounds 0-7: X4 + X27 (128 sub-tables each, 16 per round)
# Rounds 8-12: GAP + POST_X27 (80 sub-tables each, 16 per round)
_ROUND_GROUPS = (
    (X4, X27, 8),  # 8 rounds, sub-table base = round * 16
    (GAP, POST_X27, 5),  # 5 rounds, sub-table base = (round - 8) * 16
)

# TSID epoch: 2020-01-01T00:00:00Z (matches the app's TSID format)
TSID_EPOCH_MS = int(dt.datetime(2020, 1, 1, tzinfo=dt.UTC).timestamp() * 1000)

# Region IV mapping for EU-cipher regions.  Regions 1 (EU), 2 (KR),
# 3 (EU-alt), 9 (JP) and -1 (SA) — they differ only in the IV.
# Regions 4 (US) and 5 (CA) use separate cipher functions whose T-tables
# are not available — they are not in this map and are rejected by
# compute_x_stamp with NotImplementedError.
_EU_CIPHER_REGION_IVS = {
    1: IVS[1],  # EU
    2: IVS[2],  # KR SA IV
    3: IVS[1],  # EU-alt EU IV
    9: IVS[1],  # JP EU IV
    -1: IVS[2],  # SA/ETC SA IV
}

# Regions whose cipher functions are not yet available.
_UNSUPPORTED_REGIONS = {4, 5}


def create_tsid(device_id_hex: str = "", counter: int = 0) -> str:
    """Create a 14-byte Time-Sorted ID matching the app's TSID format.

    Format:
    - Bytes 0-4: timestamp (ms since 2020-01-01, big-endian, 40-bit mask)
    - Bytes 5-12: node (8 bytes from device ID hex)
    - Byte 13: (counter_nibble_swap | platform_nibble), platform=6 for Android

    Encoded as base64 without padding (matching Android Base64.NO_WRAP | NO_PADDING).
    """
    now_ms = int(dt.datetime.now(dt.UTC).timestamp() * 1000)
    ts_offset = now_ms - TSID_EPOCH_MS
    ts_bytes = struct.pack(">Q", ts_offset)[3:]  # last 5 bytes of 8-byte BE

    if device_id_hex:
        clean = device_id_hex.replace("-", "")
        node_bytes = bytes.fromhex(clean[:16])
    else:
        node_bytes = os.urandom(8)

    counter_swapped = ((counter & 0x0F) << 4) | ((counter >> 4) & 0x0F)
    last_byte = counter_swapped | 6  # platform=6 (Android)
    tsid_bytes = ts_bytes + node_bytes + bytes([last_byte])
    return base64.b64encode(tsid_bytes).decode("ascii").rstrip("=")


class GspaCipher:
    """GSPA AES-128 (ECB block + CFB-128) for X-Stamp computation."""

    def encrypt_block(self, plaintext_16: bytes) -> bytes:
        if len(plaintext_16) != 16:
            raise ValueError("Block must be exactly 16 bytes")
        state = bytearray(plaintext_16)
        for tbl4, tbl27, n_rounds in _ROUND_GROUPS:
            for rnd in range(n_rounds):
                state = self._round(state, tbl4, tbl27, rnd)
        # x8 final round: virtual ShiftRows then byte-level SubBytes+AddRoundKey
        return bytes(X8[k][state[SR_POSITIONS[k]]] for k in range(16))

    def encrypt_cfb(self, iv: bytes, plaintext: bytes) -> bytes:
        """AES-128-CFB encryption using the GSPA AES as the block cipher.

        CFB-128: feedback = previous ciphertext block.  Used for X-Stamp
        computation.

        Args:
            iv: 16-byte IV (region-specific, e.g. b"iv.ccsp.stamp.eu")
            plaintext: arbitrary-length bytes to encrypt

        Returns:
            ciphertext bytes (same length as plaintext)
        """
        if len(iv) != 16:
            raise ValueError(f"IV must be 16 bytes, got {len(iv)}")

        ciphertext = bytearray()
        feedback = bytearray(iv)

        for i in range(0, len(plaintext), 16):
            encrypted_feedback = self.encrypt_block(bytes(feedback))
            block = plaintext[i : i + 16]
            cipher_block = bytes(a ^ b for a, b in zip(encrypted_feedback, block))
            ciphertext.extend(cipher_block)
            feedback = bytearray(cipher_block)

        return bytes(ciphertext[: len(plaintext)])

    def compute_x_stamp(
        self,
        region: int = 1,
        is_production: bool = True,
        tsid: str | None = None,
        epoch_seconds: int | None = None,
        user_id: str = "",
    ) -> str:
        """Compute the X-Stamp header value.

        X-Stamp = base64(encrypt_cfb(iv_for_region, payload))
        payload = f"{tsid}:{epoch_seconds}:{user_id}"

        Only EU-cipher regions are supported (1=EU, 2=KR, 3=EU-alt, 9=JP,
        -1=SA) — they all use the EU tables with different IVs.
        US (4) and CA (5) use separate cipher functions whose T-tables are
        not available; calling for those regions raises ``NotImplementedError``.

        Args:
            region: Region code (1=EU, 2=KR, 3=EU-alt, 9=JP, -1=SA).
                4 (US) and 5 (CA) raise NotImplementedError.
            is_production: accepted for API parity; the cipher only has the
                EU production tables, so ``is_production=False`` (staging)
                raises ``NotImplementedError`` rather than silently producing
                a wrong X-Stamp with production tables.
            tsid: X-Request-Id (TSID base64 string). Auto-generated if None.
            epoch_seconds: Unix timestamp (seconds). Auto-generated if None.
            user_id: uid claim from ccs_token JWT.

        Returns:
            str: base64-encoded X-Stamp value
        """
        if region in _UNSUPPORTED_REGIONS:
            raise NotImplementedError(
                "US/CA tables not available — EU-cipher regions only"
            )
        if region not in _EU_CIPHER_REGION_IVS:
            raise ValueError(
                f"Unknown region {region!r}; expected one of "
                f"{sorted(_EU_CIPHER_REGION_IVS)} (EU-cipher regions)"
            )
        if not is_production:
            raise NotImplementedError(
                "staging cipher tables not available — production only"
            )

        if tsid is None:
            tsid = create_tsid()
        if epoch_seconds is None:
            epoch_seconds = int(time.time())

        sep = ":"
        payload = f"{tsid}{sep}{epoch_seconds}{sep}{user_id}".encode()

        iv = _EU_CIPHER_REGION_IVS[region]
        ciphertext = self.encrypt_cfb(iv, payload)
        return base64.b64encode(ciphertext).decode("utf-8")

    @staticmethod
    def _round(state: bytearray, tbl4, tbl27, rnd: int) -> bytearray:
        """One T-table round: ShiftRows + 4 columns of (tbl4 XOR, tbl27 XOR)."""
        # In-place ShiftRows
        state = bytearray(state[SR_POSITIONS[i]] for i in range(16))
        rbase = rnd * 16
        for col in range(4):
            base = rbase + col * 4
            sbase = col * 4
            # X4-like: tbl4[base+j][state[sbase+j]] -> XOR -> intermediate word
            x4w = [
                int.from_bytes(
                    tbl4[base + j][state[sbase + j] * 4 : state[sbase + j] * 4 + 4],
                    "little",
                )
                for j in range(4)
            ]
            inter = x4w[0] ^ x4w[1] ^ x4w[2] ^ x4w[3]
            # X27-like: tbl27[base+j][byte_j(inter)] -> XOR -> output word
            x27w = [
                int.from_bytes(
                    tbl27[base + j][
                        ((inter >> (8 * (3 - j))) & 0xFF) * 4 : (
                            (inter >> (8 * (3 - j))) & 0xFF
                        )
                        * 4
                        + 4
                    ],
                    "little",
                )
                for j in range(4)
            ]
            out = x27w[0] ^ x27w[1] ^ x27w[2] ^ x27w[3]
            for j in range(4):
                state[sbase + j] = (out >> (8 * (3 - j))) & 0xFF
        return state


# --- Module-level convenience functions ---

_cipher: GspaCipher | None = None


def _get_cipher() -> GspaCipher:
    global _cipher
    if _cipher is None:
        _cipher = GspaCipher()
    return _cipher


def encrypt_block(plaintext_16: bytes) -> bytes:
    """Encrypt a 16-byte plaintext block (ECB mode) using the GSPA AES.

    Module-level convenience mirroring ``GspaCipher.encrypt_block``.
    Re-exported via ``hyundai_kia_connect_api.gspa`` so callers can use it
    without instantiating ``GspaCipher``.
    """
    return _get_cipher().encrypt_block(plaintext_16)


def encrypt_cfb(
    iv: bytes, plaintext: bytes, region: int = 1, is_production: bool = True
) -> bytes:
    """Encrypt data in CFB mode (X-Stamp compatible).

    ``region`` and ``is_production`` are accepted for API parity; only the
    EU-cipher regions are supported. ``region`` is validated here so that a
    caller passing a US/CA IV with ``region=4/5`` cannot silently get
    EU-encrypted output (which would be a wrong X-Stamp):
    ``NotImplementedError`` is raised for unsupported regions, and
    ``is_production=False`` raises ``NotImplementedError`` (staging tables
    not available). The IV must still be supplied by the caller — use
    ``compute_x_stamp`` for the region-to-IV mapping.
    """
    if region in _UNSUPPORTED_REGIONS:
        raise NotImplementedError("US/CA tables not available — EU-cipher regions only")
    if region not in _EU_CIPHER_REGION_IVS:
        raise ValueError(
            f"Unknown region {region!r}; expected one of "
            f"{sorted(_EU_CIPHER_REGION_IVS)} (EU-cipher regions)"
        )
    if not is_production:
        raise NotImplementedError(
            "staging cipher tables not available — production only"
        )
    return _get_cipher().encrypt_cfb(iv, plaintext)


def compute_x_stamp(
    region: int = 1,
    is_production: bool = True,
    tsid: str | None = None,
    epoch_seconds: int | None = None,
    user_id: str = "",
) -> str:
    """Compute the X-Stamp header value. See GspaCipher.compute_x_stamp for details."""
    return _get_cipher().compute_x_stamp(
        region, is_production, tsid, epoch_seconds, user_id
    )
