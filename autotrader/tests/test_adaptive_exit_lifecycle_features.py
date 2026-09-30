import math

from trade_learning_features import compute_lifecycle_excursions


def test_mae_mfe_use_only_candles_between_entry_and_actual_exit():
    lifecycle = {
        "side":"long", "entry_price":100.0, "entry_time":"2026-09-25T10:00:00",
        "exit_time":"2026-09-25T10:15:00", "exit_price":106.0, "initial_stop_distance":5.0,
    }
    candles = [
        {"time":"2026-09-25T10:00:00", "high":101.0, "low":99.0},
        {"time":"2026-09-25T10:05:00", "high":110.0, "low":97.0},
        {"time":"2026-09-25T10:10:00", "high":108.0, "low":98.0},
        {"time":"2026-09-25T10:15:00", "high":106.0, "low":99.0},
        {"time":"2026-09-25T10:20:00", "high":150.0, "low":50.0},
    ]
    evidence = compute_lifecycle_excursions(lifecycle, candles)
    assert math.isclose(evidence["mfe_r"], 2.0)
    assert math.isclose(evidence["mae_r"], 0.6)
    assert evidence["time_to_mfe"] == 5.0
    assert evidence["time_to_mae"] == 5.0
    assert evidence["mfe_giveback_pct"] >= 0.0


def test_missing_candle_range_remains_unknown():
    lifecycle = {
        "side":"long", "entry_price":100.0, "entry_time":"2026-09-25T10:00:00",
        "exit_time":"2026-09-25T10:15:00", "exit_price":106.0, "initial_stop_distance":5.0,
    }
    evidence = compute_lifecycle_excursions(lifecycle, [])
    assert evidence["mfe_r"] is None
    assert evidence["mae_r"] is None
