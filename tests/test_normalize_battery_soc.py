"""Unit tests for normalize_battery_soc helper."""

from hyundai_kia_connect_api.utils import normalize_battery_soc


class TestNormalizeBatterySoc:
    def test_none_value_none_reliability(self):
        assert normalize_battery_soc(None, None) is None

    def test_none_value_zero_reliability(self):
        assert normalize_battery_soc(None, 0) is None

    def test_real_value_missing_reliability(self):
        # legacy EU / other regions: no SensorReliability field in JSON
        assert normalize_battery_soc(88, None) == 88

    def test_real_value_zero_reliability(self):
        # CCS2 happy path: Santa Fe fixture style
        assert normalize_battery_soc(88, 0) == 88

    def test_real_value_unreliable_sensor(self):
        # #1771 correlation: API flags unreliable even with real-looking Level
        assert normalize_battery_soc(88, 1) is None

    def test_sentinel_255_unreliable(self):
        # exact #1771 dump: Level=255 + SensorReliability=1
        assert normalize_battery_soc(255, 1) is None

    def test_sentinel_255_reliable_flag(self):
        # sentinel without flag — heuristic range check
        assert normalize_battery_soc(255, 0) is None

    def test_sentinel_255_no_reliability_field(self):
        # legacy EU / AU / CN / CA / USA / BR with batSoc=255
        assert normalize_battery_soc(255, None) is None

    def test_negative_sentinel(self):
        assert normalize_battery_soc(-1, None) is None

    def test_lower_boundary(self):
        assert normalize_battery_soc(0, 0) == 0

    def test_upper_boundary(self):
        assert normalize_battery_soc(100, 0) == 100

    def test_float_to_int(self):
        assert normalize_battery_soc(50.0, 0) == 50
