import pandas as pd

import trader


def test_core_adaptive_features_capture_dataframe_timestamp_as_milliseconds():
    ts = pd.Timestamp("2026-10-01T01:23:45")
    frame = pd.DataFrame([
        {"timestamp": ts, "high": 105.0, "low": 95.0, "atr_14": 2.5},
    ])

    out = trader._extract_core_adaptive_market_features({"4h": frame})

    assert out["source_timestamps"] == (int(ts.value // 1_000_000),)
