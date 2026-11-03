"""GSPA X-Stamp cipher — pure-Python AES-128 implementation with per-parameter affine layers."""

import base64
import datetime as dt
import json
import os
import struct
import time
from typing import Any

SR_POSITIONS = [0, 5, 10, 15, 4, 9, 14, 3, 8, 13, 2, 7, 12, 1, 6, 11]
TSID_EPOCH_MS = int(dt.datetime(2020, 1, 1, tzinfo=dt.UTC).timestamp() * 1000)

# Region IVs (public 16-byte ASCII strings).
IVS = {
    1: b"iv.ccsp.stamp.eu",  # EU
    2: b"iv.ccsp.stamp.sa",  # SA
    4: b"iv.ccsp.stamp.us",  # US
    5: b"iv.ccsp.stamp.ca",  # CA
    9: b"iv.ccsp.stamp.jp",  # JP
}

_GSPA_DIR = os.path.dirname(os.path.abspath(__file__))


def _load_json(filename: str) -> dict[str, Any]:
    path = os.path.join(_GSPA_DIR, filename)
    with open(path) as f:
        data: dict[str, Any] = json.load(f)
    return data


# Per-brand cipher configuration.  Each brand specifies its parameter
# table file, optional mask LUT file, round-phase plan (sbox_group,
# linear_group, round_count), and the final-round group name.
BRAND_CIPHER_SPECS: dict[str, dict[str, Any]] = {
    "hyundai": {
        "params_file": "hyundai_cipher_params.json",
        "masks": None,
        "phases": [("X4", "X27", 8), ("GAP", "POST_X27", 5)],
        "final_group": "X8",
    },
    "kia": {
        "params_file": "kia_cipher_params.json",
        "masks": "kia_mask_luts.json",
        "phases": [("SBOX", "LINEAR", 13)],
        "final_group": "BYTE",
    },
    # Genesis EU: same 13-round topology as Kia with no mask layer — the
    # replay reproduces the native cipher output byte-for-byte.
    "genesis": {
        "params_file": "genesis_cipher_params.json",
        "masks": None,
        "phases": [("SBOX", "LINEAR", 13)],
        "final_group": "BYTE",
    },
}


def _mat(cols: list[int], x: int) -> int:
    y = 0
    i = 0
    while x:
        if x & 1:
            y ^= cols[i]
        x >>= 1
        i += 1
    return y


def _lapply(cols: list[int], c: int) -> int:
    v = 0
    i = 0
    while c:
        if c & 1:
            v ^= cols[i]
        c >>= 1
        i += 1
    return v


class GspaCipher:
    """Unified X-Stamp cipher driven by a per-brand phase plan.

    The engine is a pure-Python AES-128 implementation with per-parameter
    affine layers.  Brand differences are fully data-driven: parameter
    tables, optional mask LUTs, and the round-phase plan.
    """

    def __init__(
        self,
        params: dict[str, Any] | None = None,
        masks: dict[str, Any] | None = None,
        spec: dict[str, Any] | None = None,
    ):
        if params is None:
            # Default constructor: load the parameter set for the given
            # spec (or Hyundai, so that existing call sites —
            # ``GspaCipher()`` — keep working unchanged).
            spec = spec or BRAND_CIPHER_SPECS["hyundai"]
            params = _load_json(spec["params_file"])
            masks = (
                masks
                if masks is not None
                else (_load_json(spec["masks"]) if spec["masks"] else None)
            )
        self._spec = spec or BRAND_CIPHER_SPECS["hyundai"]
        self.p = params
        self.masks = masks
        self.S = [int(x, 16) for x in self.p["sbox"]]
        self.G: dict[str, dict[int, dict[str, Any]]] = {}
        self._parse_groups()

    def _parse_groups(self) -> None:
        # Sbox-style groups: {M1, a, M2, t2, Lcols, c, is_byte}
        # Linear-style groups: {lcols, lconst}
        # Both brands use both schemas; group names differ by brand.
        sbox_groups = {
            g
            for g in self.p
            if isinstance(self.p[g], dict)
            and "M1" in (next(iter(self.p[g].values())) if self.p[g] else {})
        }
        lin_groups = {
            g
            for g in self.p
            if isinstance(self.p[g], dict)
            and "lcols" in (next(iter(self.p[g].values())) if self.p[g] else {})
        }
        for g in sbox_groups:
            self.G[g] = {}
            for sub, p in self.p[g].items():
                self.G[g][int(sub)] = {
                    "M1": [int(c, 16) for c in p["M1"]],
                    "a": p["a"],
                    "M2": [int(c, 16) for c in p["M2"]],
                    "t2": p["t2"],
                    "Lcols": [int(c, 16) for c in p["Lcols"]],
                    "c": p["c"],
                    "is_byte": p["is_byte"],
                }
        for g in lin_groups:
            self.G[g] = {}
            for sub, p in self.p[g].items():
                self.G[g][int(sub)] = {
                    "lcols": [int(c, 16) for c in p["lcols"]],
                    "lconst": p["lconst"],
                }
        # Parse mask LUTs into int form if present.
        self._masks_parsed: dict[tuple[int, int], list[list[int]]] | None = None
        if self.masks is not None:
            self._masks_parsed = {}
            for key, luts in self.masks.items():
                # key format: "(r,c)"
                r_str, c_str = key.strip("()").split(",")
                r, c = int(r_str), int(c_str)
                self._masks_parsed[(r, c)] = [[int(v) for v in lut] for lut in luts]

    def _sbox_lookup(self, g: str, sub: int, x: int) -> int:
        q = self.G[g][sub]
        a1 = _mat(q["M1"], x ^ int(q["a"]))
        s = self.S[a1]
        a2 = _mat(q["M2"], s) ^ int(q["t2"])
        if q["is_byte"]:
            return (int(q["c"]) ^ a2) & 0xFF
        return int(q["c"]) ^ _lapply(q["Lcols"], a2)

    def _lin_lookup(self, g: str, sub: int, b: int) -> int:
        q = self.G[g][sub]
        return int(q["lconst"]) ^ _lapply(q["lcols"], b)

    def _mask_byte(self, global_round: int, col: int, src_bytes: list[int]) -> int:
        """Compute the 32-bit mask word for a round column.

        mask = g0[b0] ^ g1[b1] ^ g2[b2] ^ g3[b3], where b0..b3 are the
        column's state bytes before the sbox layer.  The word is XORed
        once into the round's linear output word.  Returns 0 when no
        masks are loaded.  Mask integration is validated by the live
        gate (current parameter set carries all-zero masks; bit-exact
        vectors cannot distinguish the integration point).
        """
        if self._masks_parsed is None:
            return 0
        luts = self._masks_parsed.get((global_round, col))
        if luts is None:
            return 0
        return (
            luts[0][src_bytes[0]]
            ^ luts[1][src_bytes[1]]
            ^ luts[2][src_bytes[2]]
            ^ luts[3][src_bytes[3]]
        )

    def _round(
        self,
        state: bytearray,
        sbox_group: str,
        lin_group: str,
        rnd: int,
        global_round: int,
    ) -> bytearray:
        state = bytearray(state[SR_POSITIONS[i]] for i in range(16))
        rbase = rnd * 16
        for col in range(4):
            base = rbase + col * 4
            sbase = col * 4
            sbox_out = [
                self._sbox_lookup(sbox_group, base + j, state[sbase + j])
                for j in range(4)
            ]
            inter = sbox_out[0] ^ sbox_out[1] ^ sbox_out[2] ^ sbox_out[3]
            lin_out = [
                self._lin_lookup(lin_group, base + j, (inter >> (8 * (3 - j))) & 0xFF)
                for j in range(4)
            ]
            # Mask layer (brand-specific, no-op when masks absent or zero):
            # the 32-bit mask word derived from the column's pre-sbox state
            # bytes is XORed once into the linear output word.
            mb = self._mask_byte(global_round, col, list(state[sbase : sbase + 4]))
            out = lin_out[0] ^ lin_out[1] ^ lin_out[2] ^ lin_out[3] ^ mb
            for j in range(4):
                state[sbase + j] = (out >> (8 * (3 - j))) & 0xFF
        return state

    def encrypt_block(self, plaintext_16: bytes) -> bytes:
        if len(plaintext_16) != 16:
            raise ValueError("Block must be exactly 16 bytes")
        spec = self._spec
        state = bytearray(plaintext_16)
        global_round = 0
        for sbox_group, lin_group, rounds in spec["phases"]:
            for rnd in range(rounds):
                state = self._round(state, sbox_group, lin_group, rnd, global_round)
                global_round += 1
        fg = spec["final_group"]
        return bytes(
            self._sbox_lookup(fg, k, state[SR_POSITIONS[k]]) & 0xFF for k in range(16)
        )

    def encrypt_cfb(self, iv: bytes, plaintext: bytes) -> bytes:
        if len(iv) != 16:
            raise ValueError(f"IV must be 16 bytes, got {len(iv)}")
        ciphertext = bytearray()
        feedback = bytearray(iv)
        for i in range(0, len(plaintext), 16):
            enc = self.encrypt_block(bytes(feedback))
            block = plaintext[i : i + 16]
            cb = bytes(a ^ b for a, b in zip(enc, block))
            ciphertext.extend(cb)
            feedback = bytearray(cb)
        return bytes(ciphertext[: len(plaintext)])

    def compute_x_stamp(
        self,
        region: int = 1,
        tsid: str | None = None,
        epoch_seconds: int | None = None,
        user_id: str = "",
    ) -> str:
        if region in (4, 5):
            raise NotImplementedError("US/CA not supported")
        if region not in IVS:
            raise ValueError(f"Unknown region {region!r}")
        if tsid is None:
            tsid = create_tsid()
        if epoch_seconds is None:
            epoch_seconds = int(time.time())
        payload = f"{tsid}:{epoch_seconds}:{user_id}".encode()
        return base64.b64encode(self.encrypt_cfb(IVS[region], payload)).decode("utf-8")


def create_tsid(device_id_hex: str = "", counter: int = 0) -> str:
    now_ms = int(dt.datetime.now(dt.UTC).timestamp() * 1000)
    ts_offset = now_ms - TSID_EPOCH_MS
    ts_bytes = struct.pack(">Q", ts_offset)[3:]
    node_bytes = (
        bytes.fromhex(device_id_hex.replace("-", "")[:16])
        if device_id_hex
        else os.urandom(8)
    )
    last_byte = (((counter & 0x0F) << 4) | ((counter >> 4) & 0x0F)) | 6
    return (
        base64.b64encode(ts_bytes + node_bytes + bytes([last_byte]))
        .decode()
        .rstrip("=")
    )


# --- Brand factories ---

_hyundai_cipher: GspaCipher | None = None
_kia_cipher: GspaCipher | None = None


def hyundai_cipher() -> GspaCipher:
    global _hyundai_cipher
    if _hyundai_cipher is None:
        spec = BRAND_CIPHER_SPECS["hyundai"]
        params = _load_json(spec["params_file"])
        masks = _load_json(spec["masks"]) if spec["masks"] else None
        _hyundai_cipher = GspaCipher(params, masks, spec=spec)
    return _hyundai_cipher


def kia_cipher() -> GspaCipher:
    global _kia_cipher
    if _kia_cipher is None:
        spec = BRAND_CIPHER_SPECS["kia"]
        params = _load_json(spec["params_file"])
        masks = _load_json(spec["masks"]) if spec["masks"] else None
        _kia_cipher = GspaCipher(params, masks, spec=spec)
    return _kia_cipher


_genesis_cipher: GspaCipher | None = None


def genesis_cipher() -> GspaCipher:
    global _genesis_cipher
    if _genesis_cipher is None:
        spec = BRAND_CIPHER_SPECS["genesis"]
        params = _load_json(spec["params_file"])
        masks = _load_json(spec["masks"]) if spec["masks"] else None
        _genesis_cipher = GspaCipher(params, masks, spec=spec)
    return _genesis_cipher


# --- Module-level convenience functions (Hyundai delegates, zero API breakage) ---


def _get_cipher() -> GspaCipher:
    return hyundai_cipher()


def encrypt_block(plaintext_16: bytes) -> bytes:
    """ECB block encrypt (module-level convenience, Hyundai brand)."""
    return _get_cipher().encrypt_block(plaintext_16)


def encrypt_cfb(iv: bytes, plaintext: bytes, region: int = 1) -> bytes:
    """CFB-128 encrypt for X-Stamp computation (Hyundai brand).

    The region argument guards stamp usage only (US/CA unsupported);
    the IV is caller-supplied.
    """
    if region in (4, 5):
        raise NotImplementedError("US/CA not supported — EU-cipher regions only")
    if region not in IVS:
        raise ValueError(f"Unknown region {region!r}")
    return _get_cipher().encrypt_cfb(iv, plaintext)


def compute_x_stamp(
    region: int = 1,
    tsid: str | None = None,
    epoch_seconds: int | None = None,
    user_id: str = "",
) -> str:
    return _get_cipher().compute_x_stamp(region, tsid, epoch_seconds, user_id)
