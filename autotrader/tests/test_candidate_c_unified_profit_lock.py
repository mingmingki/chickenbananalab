import candidate_c_decision_engine as dec
import candidate_c_exit_management as cem
import candidate_c_reversal_state_machine as rsm
import candidate_c_setup_tracker as st
import candidate_c_strategy_policy as pol


def test_candidate_c_uses_shared_075r_profit_lock_overlay():
    step = 300000
    bars = []
    for i in range(42):
        close = 108.0
        o = i * step
        bars.append(dict(
            inst_id='X', timeframe='5m', price_type='last', open_time_ms=o,
            close_time_ms=o + step, confirm=1, open=close, high=close + .5,
            low=close - .5, close=close, volume_contracts=1, volume_ccy=1,
            volume_ccy_quote=1, metadata_version=1, collected_at_iso='x'))
    asof = bars[-1]['open_time_ms']
    end = bars[-1]['close_time_ms']
    base = dict(inst_id='X', price_type='last', confirm=1, open=108, high=109, low=107, close=108)
    bars4 = [dict(base, open_time_ms=0, close_time_ms=end - 1)]
    bars1 = [dict(base, open_time_ms=0, close_time_ms=end - 1)]

    def indicators(rows, donchian_n=20):
        out = []
        for bar in rows:
            row = dict(bar)
            row.update(ema_20=100.0, ema_50=90.0, atr_14=5.0)
            out.append(row)
        return out

    store = cem.PositionEpochStore.in_memory()
    store.save('p1', cem.PositionEpochState(
        original_contracts=10.0, remaining_contracts=10.0, derisk_done=True,
        profit_lock_active=False, partial_take_profit_done=False))
    machine = rsm.SymbolReversalMachine('X')
    machine.request_entry('long')
    machine.confirm_entry_filled()
    ctx = dec.DecisionContext(
        account_id='a', symbol='X', strategy_id='candidate_c',
        config_version_id='v', config_hash='h', risk_per_trade_pct=1.0,
        strategy_policy=pol.production_strategy_policy())
    position = dict(
        side='long', position_id='p1', contracts=10.0,
        raw_entry_price=100.0, initial_stop_price=90.0, high_water=108.5,
        effective_entry_price=100.0, entry_fee_usdt=.1, contract_size=1.0,
        fee_rate=.0005, spread_bps=3.0, slippage_bps=3.0)
    intent = dec.decide(
        ctx, as_of_ms=asof, bars_4h_confirmed_up_to_asof=bars4,
        bars_1h_confirmed_up_to_asof=bars1, bars_1d_confirmed_up_to_asof=[],
        bars_5m_for_10m_up_to_asof=bars, setup_tracker=st.SetupTracker.in_memory(),
        epoch_store=store, reversal_machine=machine, current_position=position,
        weakening_prev=False, lot_step=.1, min_size=.1, indicator_fn=indicators)
    assert intent.kind == dec.INTENT_STOP_UPDATE
    assert intent.reason_code == 'profit_lock_activated'
