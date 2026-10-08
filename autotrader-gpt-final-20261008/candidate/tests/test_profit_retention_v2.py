import importlib

import candidate_c_decision_engine as dec
import candidate_c_exit_management as cem
import candidate_c_reversal_state_machine as rsm
import candidate_c_setup_tracker as st
import candidate_c_strategy_policy as pol
import unified_trade_guard as guard


def test_shared_profit_retention_thresholds_are_single_source():
    assert guard.PROFIT_LOCK_R == 0.75
    assert guard.MFE_ARM_R == 0.50
    assert guard.MFE_GIVEBACK_R == 0.25
    assert guard.BREAKEVEN_FEE_PCT == 0.10
    assert guard.profit_floor_eligible(0.75) is True
    assert guard.profit_floor_eligible(0.749) is False
    assert guard.mfe_giveback_eligible(0.70, 0.40) is True
    assert guard.mfe_giveback_eligible(0.70, 0.46) is False
    assert guard.mfe_giveback_eligible(0.70, -0.01) is False


def test_shared_breakeven_floor_is_symmetric():
    assert abs(guard.breakeven_floor_price('long', 100.0) - 100.1) < 1e-9
    assert abs(guard.breakeven_floor_price('short', 100.0) - 99.9) < 1e-9


def test_core_profit_floor_uses_mfe_and_never_loosens():
    trader = importlib.import_module('trader')
    assert abs(trader._core_profit_floor_target('long', 100.0, 108.0, 90.0, 0.80) - 100.1) < 1e-9
    assert trader._core_profit_floor_target('long', 100.0, 108.0, 101.0, 0.80) == 101.0
    assert trader._core_profit_floor_target('long', 100.0, 100.05, 90.0, 0.80) == 90.0
    assert trader._core_profit_floor_target('long', 100.0, 108.0, 90.0, 0.70) == 90.0


def _candidate_intent(*, close, high_water, mfe_done=False, htf_close=None, contracts=10.0):
    step = 300000
    bars = []
    for i in range(42):
        o = i * step
        bars.append(dict(
            inst_id='X', timeframe='5m', price_type='last', open_time_ms=o,
            close_time_ms=o + step, confirm=1, open=close, high=close + .2,
            low=close - .2, close=close, volume_contracts=1, volume_ccy=1,
            volume_ccy_quote=1, metadata_version=1, collected_at_iso='x'))
    asof = bars[-1]['open_time_ms']
    end = bars[-1]['close_time_ms']
    htf_close = close if htf_close is None else htf_close
    base = dict(inst_id='X', price_type='last', confirm=1, open=htf_close,
                high=htf_close + 1, low=htf_close - 1, close=htf_close)
    bars4 = [dict(base, timeframe='4h', open_time_ms=0, close_time_ms=end - 1)]
    bars1 = [dict(base, timeframe='1h', open_time_ms=0, close_time_ms=end - 1)]

    def indicators(rows, donchian_n=20):
        out = []
        for bar in rows:
            row = dict(bar)
            row.update(ema_20=100.0, ema_50=90.0, atr_14=5.0)
            out.append(row)
        return out

    store = cem.PositionEpochStore.in_memory()
    store.save('p1', cem.PositionEpochState(
        original_contracts=10.0, remaining_contracts=contracts,
        derisk_done=False, profit_lock_active=False,
        partial_take_profit_done=False, mfe_profit_reduce_done=mfe_done))
    machine = rsm.SymbolReversalMachine('X')
    machine.request_entry('long')
    machine.confirm_entry_filled()
    ctx = dec.DecisionContext(
        account_id='a', symbol='X', strategy_id='candidate_c',
        config_version_id='v', config_hash='h', risk_per_trade_pct=1.0,
        strategy_policy=pol.production_strategy_policy())
    position = dict(
        side='long', position_id='p1', contracts=contracts,
        raw_entry_price=100.0, initial_stop_price=90.0, high_water=high_water,
        effective_entry_price=100.0, entry_fee_usdt=.1, contract_size=1.0,
        fee_rate=.0005, spread_bps=3.0, slippage_bps=3.0)
    return dec.decide(
        ctx, as_of_ms=asof, bars_4h_confirmed_up_to_asof=bars4,
        bars_1h_confirmed_up_to_asof=bars1, bars_1d_confirmed_up_to_asof=[],
        bars_5m_for_10m_up_to_asof=bars, setup_tracker=st.SetupTracker.in_memory(),
        epoch_store=store, reversal_machine=machine, current_position=position,
        weakening_prev=False, lot_step=.1, min_size=.1, indicator_fn=indicators)


def test_candidate_profit_floor_arms_from_high_water_even_after_retrace():
    # Initial R=10. MFE high-water=+0.80R, but current close is only +0.60R.
    intent = _candidate_intent(close=106.0, high_water=108.0)
    assert intent.kind == dec.INTENT_STOP_UPDATE
    assert intent.reason_code == 'profit_lock_activated'


def test_candidate_mfe_giveback_reduces_25pct_once():
    # Initial R=10. MFE=+0.70R, current=+0.40R -> 0.30R giveback.
    intent = _candidate_intent(close=104.0, high_water=107.0)
    assert intent.kind == dec.INTENT_REDUCE
    assert intent.reason_code == 'unified_mfe_profit_giveback'
    assert intent.reduce_quantity == 2.5
    assert intent.target_residual == 7.5


def test_candidate_mfe_giveback_is_one_time_per_epoch():
    intent = _candidate_intent(close=104.0, high_water=107.0, mfe_done=True)
    assert not (intent.kind == dec.INTENT_REDUCE and intent.reason_code == 'unified_mfe_profit_giveback')


def test_candidate_profit_floor_does_not_place_stop_beyond_current_price_after_full_retrace():
    intent = _candidate_intent(close=99.0, high_water=108.0, htf_close=108.0)
    assert not (intent.kind == dec.INTENT_STOP_UPDATE and intent.reason_code == 'profit_lock_activated')


def test_candidate_mfe_reduce_lifecycle_flag_is_distinct_from_structural_derisk():
    import candidate_c_hybrid_live_adapter as live
    import candidate_c_backtest_signal_adapter as bt
    e_live = cem.PositionEpochState()
    live._apply_reduce_lifecycle_flags(e_live, 'unified_mfe_profit_giveback')
    assert e_live.mfe_profit_reduce_done is True
    assert e_live.derisk_done is False
    assert e_live.partial_take_profit_done is False
    e_bt = cem.PositionEpochState()
    bt._apply_completed_reduce_epoch_flags(e_bt, 'unified_mfe_profit_giveback')
    assert e_bt.mfe_profit_reduce_done is True
    assert e_bt.derisk_done is False
    assert e_bt.partial_take_profit_done is False


def test_candidate_locks_remaining_half_when_mfe_giveback_cannot_reduce_any_more():
    # Existing policy already armed at +0.50R and gave back 0.25R.
    # Half was reduced earlier; protecting the remainder must still be possible.
    intent = _candidate_intent(close=103.0, high_water=106.0, contracts=5.0)
    assert intent.kind == dec.INTENT_STOP_UPDATE
    assert intent.reason_code == 'profit_lock_activated'


def test_remaining_half_floor_still_requires_price_to_cover_costs():
    intent = _candidate_intent(close=100.01, high_water=106.0, contracts=5.0, htf_close=108.0)
    assert not (intent.kind == dec.INTENT_STOP_UPDATE and intent.reason_code == 'profit_lock_activated')

def test_backtest_adapter_preserves_the_remaining_half_profit_floor(monkeypatch):
    import candidate_c_backtest_signal_adapter as ba
    live_intent = _candidate_intent(close=103.0, high_water=106.0, contracts=5.0)
    def bar(tf,opened,step):
        return dict(inst_id='X',timeframe=tf,price_type='last',confirm=1,
                    open_time_ms=opened,close_time_ms=opened+step,
                    open=103.,high=103.2,low=102.8,close=103.,
                    volume_contracts=1.,volume_ccy=1.,volume_ccy_quote=103.,
                    metadata_version=1,collected_at_iso='x')
    bars5=[bar('5m',i*300000,300000) for i in range(42)]
    bars1=[bar('1H',i*3600000,3600000) for i in range(-20,3)]
    bars4=[bar('4H',i*14400000,14400000) for i in range(-20,0)]
    def indicators(rows,donchian_n=20):
        return [dict(row,ema_20=100.,ema_50=90.,atr_14=5.) for row in rows]
    monkeypatch.setattr(ba,'build_causal_indicator_lookup',lambda *_:indicators)
    state=ba.CandidateCBacktestAdapterState(
        setup_tracker=st.SetupTracker.in_memory(),epoch_store=cem.PositionEpochStore.in_memory())
    signal=ba.build_candidate_c_signal(
        'X',bars_4h=bars4,bars_1h=bars1,bars_1d=[],lot_step=.1,min_size=.1,adapter_state=state,
        strategy_policy=pol.production_strategy_policy())
    class Window(list):
        def latest(self):return self[-1]
    held=dict(position_id='p1',side='long',contracts=5.,original_contracts=10.,
              entry_price=100.,raw_entry_price=100.,initial_stop_price=90.,stop_price=90.,
              high_water=106.,entry_fee_usdt=.1,contract_size=1.,
              fee_rate=.0005,spread_bps=3.,slippage_bps=3.)
    signal('X',Window(bars5),held)
    backtest_intent=state.all_intents_seen[-1]
    assert live_intent.reason_code == 'profit_lock_activated'
    assert backtest_intent.reason_code == 'profit_lock_activated'
    assert backtest_intent.raw_stop_price == live_intent.raw_stop_price
    assert 100.1 < backtest_intent.raw_stop_price < 103.
