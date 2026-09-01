"""Kia EU CCI door-split — gated until live verification (D6)."""

import pytest

from hyundai_kia_connect_api.KiaCciApiEU import KiaCciApiEU


def test_kia_door_methods_are_gated():
    api = KiaCciApiEU(9, 1, "en")
    for meth in (
        api.lock_door,
        api.unlock_door,
        api.lock_door_safety,
        api.unlock_door_safety,
    ):
        with pytest.raises(NotImplementedError):
            meth(None, None)
