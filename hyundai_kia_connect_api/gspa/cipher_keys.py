"""14-round GSPA whitebox cipher REIMPLEMENTED from recovered affine params.

NO ttables.py import. Loads cipher_params.json (recovered per-sub affine params:
S-box stages M1/a/M2/t2/Lcols/c; linear stages lcols/lconst) and reproduces the
cipher using the public round wiring (ShiftRows + column XOR, from the public round wiring)
with each table lookup replaced by its param-computed value.

Bit-identical to the original cipher on all known test vectors.
"""

import base64
import datetime as dt
import json
import os
import struct
import time

SR_POSITIONS = [0, 5, 10, 15, 4, 9, 14, 3, 8, 13, 2, 7, 12, 1, 6, 11]
TSID_EPOCH_MS = int(dt.datetime(2020, 1, 1, tzinfo=dt.UTC).timestamp() * 1000)

# Region IVs (public 16-byte ASCII strings, NOT key material — no ttables import).
IVS = {
    1: b"iv.ccsp.stamp.eu",  # EU
    2: b"iv.ccsp.stamp.sa",  # KR -> SA IV
    3: b"iv.ccsp.stamp.eu",  # EU-alt
    4: b"iv.ccsp.stamp.us",  # US
    5: b"iv.ccsp.stamp.ca",  # CA
    9: b"iv.ccsp.stamp.eu",  # JP -> EU IV
    -1: b"iv.ccsp.stamp.sa",  # SA/ETC
}
_EU_CIPHER_REGION_IVS = {1: IVS[1], 2: IVS[2], 3: IVS[1], 9: IVS[1], -1: IVS[2]}

_PARAMS_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "cipher_params.json"
)
_params = None


def _load_params():
    global _params
    if _params is None:
        with open(_PARAMS_PATH) as f:
            _params = json.load(f)
    return _params


def _mat(cols, x):
    y = 0
    i = 0
    while x:
        if x & 1:
            y ^= cols[i]
        x >>= 1
        i += 1
    return y


def _lapply(cols, c):
    v = 0
    i = 0
    while c:
        if c & 1:
            v ^= cols[i]
        c >>= 1
        i += 1
    return v


class GspaCipher:
    def __init__(self, params=None):
        self.p = params if params is not None else _load_params()
        self.S = [int(x, 16) for x in self.p["sbox"]]
        # pre-parse groups into int form
        self.G = {}
        for g in ("X4", "GAP", "X8"):
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
        for g in ("X27", "POST_X27"):
            self.G[g] = {}
            for sub, p in self.p[g].items():
                self.G[g][int(sub)] = {
                    "lcols": [int(c, 16) for c in p["lcols"]],
                    "lconst": p["lconst"],
                }

    def _sbox_lookup(self, g, sub, x):
        q = self.G[g][sub]
        a1 = _mat(q["M1"], x ^ q["a"])
        s = self.S[a1]
        a2 = _mat(q["M2"], s) ^ q["t2"]
        if q["is_byte"]:
            return (q["c"] ^ a2) & 0xFF
        return q["c"] ^ _lapply(q["Lcols"], a2)

    def _lin_lookup(self, g, sub, b):
        q = self.G[g][sub]
        return q["lconst"] ^ _lapply(q["lcols"], b)

    def _x8(self, k, y):
        return self._sbox_lookup("X8", k, y) & 0xFF

    def _round(self, state, g4, g27, rnd):
        state = bytearray(state[SR_POSITIONS[i]] for i in range(16))
        rbase = rnd * 16
        for col in range(4):
            base = rbase + col * 4
            sbase = col * 4
            x4w = [self._sbox_lookup(g4, base + j, state[sbase + j]) for j in range(4)]
            inter = x4w[0] ^ x4w[1] ^ x4w[2] ^ x4w[3]
            x27w = [
                self._lin_lookup(g27, base + j, (inter >> (8 * (3 - j))) & 0xFF)
                for j in range(4)
            ]
            out = x27w[0] ^ x27w[1] ^ x27w[2] ^ x27w[3]
            for j in range(4):
                state[sbase + j] = (out >> (8 * (3 - j))) & 0xFF
        return state

    def encrypt_block(self, plaintext_16: bytes) -> bytes:
        if len(plaintext_16) != 16:
            raise ValueError("Block must be exactly 16 bytes")
        state = bytearray(plaintext_16)
        for rnd in range(8):
            state = self._round(state, "X4", "X27", rnd)
        for rnd in range(5):
            state = self._round(state, "GAP", "POST_X27", rnd)
        return bytes(self._x8(k, state[SR_POSITIONS[k]]) for k in range(16))

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
        is_production: bool = True,
        tsid: str | None = None,
        epoch_seconds: int | None = None,
        user_id: str = "",
    ) -> str:
        if region in (4, 5):
            raise NotImplementedError("US/CA tables not extracted")
        if region not in _EU_CIPHER_REGION_IVS:
            raise ValueError(f"Unknown region {region!r}")
        if not is_production:
            raise NotImplementedError("staging not extracted — production only")
        if tsid is None:
            tsid = create_tsid()
        if epoch_seconds is None:
            epoch_seconds = int(time.time())
        payload = f"{tsid}:{epoch_seconds}:{user_id}".encode()
        return base64.b64encode(
            self.encrypt_cfb(_EU_CIPHER_REGION_IVS[region], payload)
        ).decode("utf-8")


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


# --- Module-level convenience functions ---

_cipher: "GspaCipher | None" = None


def _get_cipher() -> "GspaCipher":
    global _cipher
    if _cipher is None:
        _cipher = GspaCipher()
    return _cipher


def encrypt_block(plaintext_16: bytes) -> bytes:
    """ECB block encrypt (module-level convenience)."""
    return _get_cipher().encrypt_block(plaintext_16)


def encrypt_cfb(
    iv: bytes, plaintext: bytes, region: int = 1, is_production: bool = True
) -> bytes:
    """CFB-128 encrypt (X-Stamp compatible). region/is_production accepted for
    API parity; only EU-cipher regions + production supported (US/CA/staging
    raise NotImplementedError rather than silently producing a wrong X-Stamp)."""
    if region in (4, 5):
        raise NotImplementedError("US/CA tables not extracted — EU-cipher regions only")
    if region not in _EU_CIPHER_REGION_IVS:
        raise ValueError(f"Unknown region {region!r}")
    if not is_production:
        raise NotImplementedError("staging not extracted — production only")
    return _get_cipher().encrypt_cfb(iv, plaintext)


def compute_x_stamp(
    region: int = 1,
    is_production: bool = True,
    tsid: str | None = None,
    epoch_seconds: int | None = None,
    user_id: str = "",
) -> str:
    return _get_cipher().compute_x_stamp(
        region, is_production, tsid, epoch_seconds, user_id
    )
