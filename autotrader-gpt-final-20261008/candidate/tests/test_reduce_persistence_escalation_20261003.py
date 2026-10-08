import trader


def test_stage2_persistent_adverse_1h_signal_escalates_even_if_macd_not_worse_than_stage1(monkeypatch):
    rows_1h = [
        {'ts': '2026-10-03T00:00:00+00:00', 'close': 98.0, 'ema_20': 100.0, 'macd': -1.7},
        {'ts': '2026-10-03T01:00:00+00:00', 'close': 97.5, 'ema_20': 99.5, 'macd': -1.8},
    ]
    rows_4h = [
        {'ts': '2026-10-03T00:00:00+00:00', 'close': 101.0, 'ema_50': 100.0, 'macd': 0.1},
    ]

    def closed_tail(_raw, tf, n):
        if tf == '1h':
            return rows_1h[-n:]
        if tf == '4h':
            return rows_4h[-n:]
        raise AssertionError(tf)

    monkeypatch.setattr(trader, '_closed_indicator_tail', closed_tail)
    sv = {
        'stage1_1h_macd': -2.0,
        'last_reduction_order_time': None,
    }

    met, candle_ts = trader._reduce_v2_stage2_conditions_met('long', sv, {})

    assert met is True
    assert candle_ts == rows_1h[-1]['ts']
