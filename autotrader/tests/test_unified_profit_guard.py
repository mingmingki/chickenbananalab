
def test_shared_policy_contract_exists_and_has_single_thresholds():
    import unified_trade_guard as guard
    assert guard.CHASE_30M_PCT == 0.50
    assert guard.LATE_ENTRY_MOVE_ATR == 1.25
    assert guard.MEANINGFUL_PULLBACK_ATR == 0.50
    assert guard.PILOT_ENTRY_FRACTION == 0.50
    assert guard.PROFIT_PROTECT_R == 0.50
    assert guard.PROFIT_LOCK_R == 0.75
    assert guard.HARD_LOSS_R == 0.75


def test_shared_recent_entry_guard_blocks_late_long_and_short_symmetrically():
    import unified_trade_guard as guard
    long_result = guard.evaluate_recent_entry(side='long', current_price=100.6, reference_30m_price=100.0, move_30m_atr=1.50, pullback_atr=0.10)
    short_result = guard.evaluate_recent_entry(side='short', current_price=99.4, reference_30m_price=100.0, move_30m_atr=1.50, pullback_atr=0.10)
    assert long_result['allowed'] is False and long_result['reason'] == 'long_chase_30m_rise'
    assert short_result['allowed'] is False and short_result['reason'] == 'short_chase_30m_drop'


def test_shared_recent_entry_guard_rearms_after_meaningful_pullback():
    import unified_trade_guard as guard
    result = guard.evaluate_recent_entry(side='long', current_price=100.4, reference_30m_price=100.0, move_30m_atr=1.50, pullback_atr=0.60)
    assert result['allowed'] is True
    assert result['reason'] == 'ok'


def test_shared_pilot_fraction_only_for_unresolved_primary_with_lower_tf_alignment():
    import unified_trade_guard as guard
    assert guard.entry_size_fraction(side='long', primary_direction='NONE', direction_1h='LONG', direction_5m='LONG') == 0.5
    assert guard.entry_size_fraction(side='long', primary_direction='LONG', direction_1h='LONG', direction_5m='LONG') == 1.0
    assert guard.entry_size_fraction(side='long', primary_direction='SHORT', direction_1h='LONG', direction_5m='LONG') == 0.0


def test_shared_profit_and_loss_threshold_helpers():
    import unified_trade_guard as guard
    assert guard.profit_protect_eligible(0.50, weakening=True) is True
    assert guard.profit_lock_eligible(0.75, weakening=True, extreme=False) is True
    assert guard.profit_lock_eligible(0.75, weakening=False, extreme=True) is True
    assert guard.profit_lock_eligible(0.74, weakening=True, extreme=True) is False
    assert guard.hard_loss_eligible(0.75, invalidated=True) is True
    assert guard.hard_loss_eligible(0.75, invalidated=False) is False


def test_shared_recent_metrics_are_identical_for_both_engines():
    import unified_trade_guard as guard
    closes = [100.0, 100.3, 100.5, 100.8, 100.5, 100.45, 100.6]
    out = guard.recent_entry_metrics(side='long', current_price=100.6, closes=closes, atr=0.4)
    assert round(out['move_30m_atr'], 4) == 1.5
    assert out['pullback_atr'] >= 0.5
    assert out['reference_30m_price'] == 100.0
