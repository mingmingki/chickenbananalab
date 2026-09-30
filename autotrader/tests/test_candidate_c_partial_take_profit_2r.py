import candidate_c_exit_management as cem
import candidate_c_strategy_policy as policy
import candidate_c_hybrid_live_adapter as live

def epoch(**kw):
    return cem.PositionEpochState(**kw)

def test_policy_v2_production_and_v1_backward_readable():
    p=policy.production_strategy_policy()
    assert p["schema_version"]==2
    assert p["partial_take_profit_activation_r"]=="2.0"
    assert p["partial_take_profit_pct"]=="25"
    assert policy.validate_strategy_policy(policy._POLICY_V1)["schema_version"]==1

def test_long_2r_takes_25pct_of_original_after_prior_50pct_derisk():
    e=epoch(original_contracts=10.0, derisk_done=True)
    d=cem.evaluate_partial_take_profit(
        side="long", current_quantity=5.0, original_quantity=10.0,
        lot_step=0.1, min_size=0.1, raw_entry_price=100.0, price_r=10.0,
        confirmed_5m_close=120.0, epoch=e, activation_r=2.0, take_profit_pct=25.0)
    assert d.action=="partial_reduce"
    assert d.reduce_quantity==2.5
    assert d.target_residual==2.5
    assert d.reason=="partial_take_profit_2r"

def test_before_2r_no_partial_take_profit():
    e=epoch(original_contracts=10.0)
    d=cem.evaluate_partial_take_profit(
        side="long", current_quantity=10.0, original_quantity=10.0,
        lot_step=0.1, min_size=0.1, raw_entry_price=100.0, price_r=10.0,
        confirmed_5m_close=119.9, epoch=e)
    assert d.action=="none"

def test_short_is_symmetric():
    e=epoch(original_contracts=8.0)
    d=cem.evaluate_partial_take_profit(
        side="short", current_quantity=8.0, original_quantity=8.0,
        lot_step=0.1, min_size=0.1, raw_entry_price=100.0, price_r=10.0,
        confirmed_5m_close=80.0, epoch=e)
    assert d.action=="partial_reduce"
    assert d.reduce_quantity==2.0 and d.target_residual==6.0

def test_partial_take_profit_is_one_time_per_epoch():
    e=epoch(original_contracts=10.0, partial_take_profit_done=True)
    d=cem.evaluate_partial_take_profit(
        side="long", current_quantity=7.5, original_quantity=10.0,
        lot_step=0.1, min_size=0.1, raw_entry_price=100.0, price_r=10.0,
        confirmed_5m_close=130.0, epoch=e)
    assert d.action=="none"
    assert d.reason=="partial_take_profit_already_done"

def test_risk_derisk_has_priority_over_partial_tp_and_partial_over_profit_lock():
    assert cem.decide_action_priority({
        "1h_structural_derisk":True,
        "partial_take_profit_2r":True,
        "profit_lock_tightening":True})=="1h_structural_derisk"
    assert cem.decide_action_priority({
        "partial_take_profit_2r":True,
        "profit_lock_tightening":True})=="partial_take_profit_2r"

def test_live_partial_tp_flag_does_not_mark_structural_derisk():
    e=epoch()
    live._apply_reduce_lifecycle_flags(e,"partial_take_profit_2r")
    assert e.partial_take_profit_done is True
    assert e.derisk_done is False
    live._apply_reduce_lifecycle_flags(e,None)
    assert e.derisk_done is True

def test_entry_fee_scales_with_remaining_contracts():
    assert live._scale_remaining_entry_fee(0.4,8.0,4.0)==0.2

def test_backtest_partial_tp_flag_does_not_mark_derisk():
    import candidate_c_backtest_signal_adapter as bt
    e=epoch()
    bt._apply_completed_reduce_epoch_flags(e,"partial_take_profit_2r")
    assert e.partial_take_profit_done is True
    assert e.derisk_done is False

def test_decide_emits_partial_tp_reduce_intent_after_prior_derisk():
    import candidate_c_decision_engine as dec
    import candidate_c_setup_tracker as st
    import candidate_c_reversal_state_machine as rsm
    import candidate_c_strategy_policy as pol

    step=300000
    bars=[]
    for i in range(42):
        o=i*step
        close=110.0 if i<41 else 120.0
        bars.append(dict(
            inst_id="X", timeframe="5m", price_type="last",
            open_time_ms=o, close_time_ms=o+step, confirm=1,
            open=close, high=close+1, low=close-1, close=close,
            volume_contracts=1, volume_ccy=1, volume_ccy_quote=1,
            metadata_version=1, collected_at_iso="x",
        ))
    asof=bars[-1]["open_time_ms"]
    end=bars[-1]["close_time_ms"]
    base=dict(inst_id="X",price_type="last",confirm=1,open=110,high=111,low=109,close=110)
    bars4=[dict(base,open_time_ms=0,close_time_ms=end-1)]
    bars1=[dict(base,open_time_ms=0,close_time_ms=end-1)]

    def indicators(rows,donchian_n=20):
        out=[]
        for bar in rows:
            row=dict(bar)
            row.update(ema_20=100.0,ema_50=90.0,atr_14=5.0)
            out.append(row)
        return out

    store=cem.PositionEpochStore.in_memory()
    store.save("p1",cem.PositionEpochState(
        original_contracts=10.0,remaining_contracts=5.0,derisk_done=True,
        profit_lock_active=True,partial_take_profit_done=False))
    machine=rsm.SymbolReversalMachine("X")
    machine.request_entry("long")
    machine.confirm_entry_filled()
    ctx=dec.DecisionContext(
        account_id="a",symbol="X",strategy_id="candidate_c",
        config_version_id="v",config_hash="h",risk_per_trade_pct=1.0,
        strategy_policy=pol.production_strategy_policy())
    position=dict(
        side="long",position_id="p1",contracts=5.0,
        raw_entry_price=100.0,initial_stop_price=90.0,high_water=120.0,
        effective_entry_price=100.0,entry_fee_usdt=.1,contract_size=1.0,
        fee_rate=.0005,spread_bps=3.0,slippage_bps=3.0)
    intent=dec.decide(
        ctx,as_of_ms=asof,bars_4h_confirmed_up_to_asof=bars4,
        bars_1h_confirmed_up_to_asof=bars1,bars_1d_confirmed_up_to_asof=[],
        bars_5m_for_10m_up_to_asof=bars,setup_tracker=st.SetupTracker.in_memory(),
        epoch_store=store,reversal_machine=machine,current_position=position,
        weakening_prev=False,lot_step=.1,min_size=.1,indicator_fn=indicators)
    assert intent.kind==dec.INTENT_REDUCE
    assert intent.reason_code=="partial_take_profit_2r"
    assert intent.reduce_quantity==2.5
    assert intent.target_residual==2.5
