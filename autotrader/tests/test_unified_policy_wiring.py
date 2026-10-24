import candidate_c_decision_engine as candidate
import trader


def test_core_thresholds_come_from_unified_policy():
    import unified_trade_guard as guard
    assert trader.CORE_SHORT_CHASE_30M_DROP_PCT == guard.CHASE_30M_PCT
    assert trader.CORE_LONG_CHASE_30M_RISE_PCT == guard.CHASE_30M_PCT
    assert trader.CORE_LATE_ENTRY_MOVE_ATR == guard.LATE_ENTRY_MOVE_ATR
    assert trader.CORE_MEANINGFUL_PULLBACK_ATR == guard.MEANINGFUL_PULLBACK_ATR
    assert trader.CORE_PROFIT_PROTECT_R == guard.PROFIT_PROTECT_R
    assert trader.CORE_PROFIT_LOCK_R == guard.PROFIT_LOCK_R
    assert trader.CORE_HARD_LOSS_CLOSE_R == guard.HARD_LOSS_R


def test_candidate_decision_engine_imports_unified_policy_module():
    import unified_trade_guard as guard
    assert candidate.unified_trade_guard is guard


def test_core_profit_lock_delegates_to_shared_guard(monkeypatch):
    import unified_trade_guard as guard
    called = {}
    def fake(profit_r, *, weakening, extreme):
        called.update(profit_r=profit_r, weakening=weakening, extreme=extreme)
        return True
    monkeypatch.setattr(guard, 'profit_lock_eligible', fake)
    over = {'allowed': False, 'reason': trader.entry_overextension_guard.BLOCK_REASON}
    assert trader._profit_lock_eligible('long', 107.5, 100.0, 10.0, over) is True
    assert called['extreme'] is True


def test_core_hard_loss_delegates_to_shared_guard(monkeypatch):
    import unified_trade_guard as guard
    import pandas as pd
    called = {}
    def fake(loss_r, *, invalidated):
        called.update(loss_r=loss_r, invalidated=invalidated)
        return True
    monkeypatch.setattr(guard, 'hard_loss_eligible', fake)
    df1 = pd.DataFrame([{'close':90.0,'ema_20':95.0,'ema_50':100.0,'macd':-1.0}])
    df5 = pd.DataFrame([
        {'close':95.0,'ema_20':96.0,'macd':-0.5},
        {'close':94.0,'ema_20':95.0,'macd':-1.0},
    ])
    out = trader._hard_loss_close_diagnostics('long', {'1h':df1,'5m':df5}, 92.0, 100.0, 90.0)
    assert out['triggered'] is True
    assert called['invalidated'] is True
