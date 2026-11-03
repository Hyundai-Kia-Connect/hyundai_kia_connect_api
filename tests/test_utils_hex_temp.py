import pytest

from hyundai_kia_connect_api.utils import (
    get_hex_temp_into_index,
    get_index_into_hex_temp,
)


@pytest.mark.parametrize(
    ("hex_temp", "index"), [("00H", 0), ("0AH", 10), ("1FH", 31), ("1A0H", 416)]
)
def test_hex_temp_round_trip(hex_temp: str, index: int) -> None:
    assert get_hex_temp_into_index(hex_temp) == index
    assert get_index_into_hex_temp(index) == hex_temp


def test_hex_temp_none() -> None:
    assert get_hex_temp_into_index(None) is None
    assert get_index_into_hex_temp(None) is None
