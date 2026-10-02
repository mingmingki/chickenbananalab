import candidate_c_timeframe_contract as tfc


def _snapshot(*, close: float, upper: float = 100.0, lower: float = 90.0):
    prior = [{"high": upper, "low": lower} for _ in range(20)]
    return tfc.AsOfSnapshot(
        as_of_ms=1,
        bar_5m={},
        bar_4h={},
        bar_1h={},
        bar_1d=None,
        bar_10m_current={"close": close},
        bars_10m_prior_20=prior,
    )


def test_long_setup_accepts_price_within_one_percent_below_upper_boundary():
    assert tfc.donchian_setup_condition(_snapshot(close=99.0), "long") is True


def test_long_setup_rejects_price_more_than_one_percent_below_upper_boundary():
    assert tfc.donchian_setup_condition(_snapshot(close=98.99), "long") is False


def test_short_setup_accepts_price_within_one_percent_above_lower_boundary():
    assert tfc.donchian_setup_condition(_snapshot(close=90.9), "short") is True


def test_short_setup_rejects_price_more_than_one_percent_above_lower_boundary():
    assert tfc.donchian_setup_condition(_snapshot(close=90.91), "short") is False


def test_existing_full_breakout_remains_a_setup():
    assert tfc.donchian_setup_condition(_snapshot(close=101.0), "long") is True
    assert tfc.donchian_setup_condition(_snapshot(close=89.0), "short") is True
