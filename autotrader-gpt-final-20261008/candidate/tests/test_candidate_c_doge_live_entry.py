"""Regression for production DOGE missed-entry wiring on 2026-10-07."""
import datetime
import gzip
import json
from pathlib import Path

import candidate_c_decision_engine as dec
import candidate_c_indicator_contract as ic
import candidate_c_exit_management as cem
import candidate_c_reversal_state_machine as rsm
import candidate_c_setup_tracker as st
import candidate_c_strategy_policy as policy
import candidate_c_timeframe_contract as tfc

SYMBOL = "DOGE/USDT:USDT"

def inputs(hour, minute):
    fixture = json.loads(gzip.decompress(
        (Path(__file__).parent / "fixtures/candidate_c_doge_20261007.json.gz").read_bytes()))
    at = int(datetime.datetime(2026,10,7,hour,minute,
        tzinfo=datetime.timezone(datetime.timedelta(hours=9))).timestamp()*1000)
    book = ic.IndicatorBook(SYMBOL)
    book.streams = fixture["streams"]
    bars = {tf:[b for b in rows if b["close_time_ms"] <= at][-200:]
            for tf,rows in fixture["candles"].items()}
    return at, book, bars

def intent(hour, minute, tracker=None, current=None, epochs=None):
    at, book, bars = inputs(hour,minute)
    ctx = dec.DecisionContext(account_id="test",symbol=SYMBOL,strategy_id="candidate_c",
        config_version_id="v1",config_hash="fixture",risk_per_trade_pct=1,
        sizing_mode="FIXED_MARGIN",strategy_policy=policy.production_strategy_policy())
    return dec.decide(ctx,as_of_ms=at-300000,
        bars_4h_confirmed_up_to_asof=bars["4h"],
        bars_1h_confirmed_up_to_asof=bars["1h"],
        bars_1d_confirmed_up_to_asof=[],bars_5m_for_10m_up_to_asof=bars["5m"],
        setup_tracker=tracker or st.SetupTracker.in_memory(),
        epoch_store=epochs or cem.PositionEpochStore.in_memory(),
        reversal_machine=rsm.SymbolReversalMachine(SYMBOL),
        current_position=current,weakening_prev=False,lot_step=1,min_size=1,
        indicator_fn=book.indicators)

def test_actual_checkpoint_supports_causal_5m_entry_confirmation():
    _, book, bars = inputs(10,20)
    row = book.indicators(bars["5m"],donchian_n=20)[-1]
    assert row["close"] < row["ema_20"] < row["ema_50"]
    assert row["atr_14"] > 0

def test_actual_doge_early_short_is_not_hidden_by_long_candidate():
    result = intent(9,30)
    assert result.kind == dec.INTENT_ENTRY
    assert result.side == "short"
    assert result.reason_code == "setup_pilot_4h_none_lower_tf_aligned"
    assert result.entry_size_fraction == 0.25

def test_actual_doge_short_evaluation_uses_current_5m_confirmation():
    result = intent(14,40)
    assert result.side == "short"
    assert result.reason_code == "entry_risk_strong_confirmation_required"

def test_actual_crash_entry_remains_blocked_before_order_submission():
    result = intent(11,0)
    assert result.kind == dec.INTENT_NO_ACTION
    assert result.reason_code == "short_chase_30m_drop"

def test_direction_ineligible_setup_does_not_spend_entry_window(tmp_path):
    tracker = st.SetupTracker.load(str(tmp_path / "setup.jsonl"))
    at, _, _ = inputs(8, 30)
    tracker.observe(SYMBOL, "short", at-600000, True, entry_eligible=False)
    tracker = st.SetupTracker.load(tracker.log_path)
    result = intent(9, 30, tracker=tracker)
    assert result.kind == dec.INTENT_ENTRY
    assert result.side == "short"

def test_eligible_entry_window_still_expires_and_survives_restart(tmp_path):
    tracker = st.SetupTracker.load(str(tmp_path / "setup.jsonl"))
    at, _, _ = inputs(8, 30)
    tracker.observe(SYMBOL, "short", at-600000, True, entry_eligible=True)
    tracker = st.SetupTracker.load(tracker.log_path)
    assert intent(9, 30, tracker=tracker).reason_code == "setup_stale_after_30m"

def test_early_short_thesis_survives_4h_none_after_restart(tmp_path):
    store = cem.PositionEpochStore.load(str(tmp_path / "epochs.jsonl"))
    store.save("pilot", cem.PositionEpochState(pilot_entry=True))
    store = cem.PositionEpochStore.load(store.log_path)
    current = dict(side="short", position_id="pilot", contracts=10,
        raw_entry_price=.09354, initial_stop_price=.10, high_water=.09354)
    result = intent(9,35,current=current,epochs=store)
    assert result.kind not in (dec.INTENT_REVERSAL, dec.INTENT_EXIT)
    assert result.reason_code != "4h_direction_invalidated"

def test_standard_entry_keeps_original_4h_invalidation():
    current = dict(side="short", position_id="normal", contracts=10,
        raw_entry_price=.09354, initial_stop_price=.10, high_water=.09354)
    result = intent(9,35,current=current)
    assert result.reason_code == "4h_direction_invalidated"

def test_5m_confirmation_rejects_gapped_and_unconfirmed_history():
    import pytest
    _, book, bars = inputs(9,30)
    with pytest.raises(ic.IndicatorUnavailable):
        book.indicators(bars["5m"][:-2]+bars["5m"][-1:],donchian_n=20)
    bad = [dict(row) for row in bars["5m"]]
    bad[-1]["confirm"]=0
    with pytest.raises(ic.IndicatorUnavailable):
        book.indicators(bad,donchian_n=20)

def test_initial_short_reaches_same_existing_proximity_contract_at_order_boundary():
    from types import SimpleNamespace
    import candidate_c_hybrid_cycle as cycle
    at, _, bars = inputs(9,30)
    snap = tfc.build_as_of_snapshot(at-300000,bars_4h=bars["4h"],
        bars_1h=bars["1h"],bars_1d=[],bars_5m_for_10m=bars["5m"])
    result = intent(9,30)
    lower=min(b["low"] for b in snap.bars_10m_prior_20)
    snapshot={"donchian_lower":lower,"donchian_upper":max(b["high"] for b in snap.bars_10m_prior_20)}
    client=SimpleNamespace(fetch_last_price=lambda:.09354)
    check=cycle._build_entry_still_valid_fn(client,result,snapshot)
    assert check()
    client.fetch_last_price=lambda:lower*1.011
    assert not check()
    client.fetch_last_price=lambda:result.raw_stop_price
    assert not check()

def test_pilot_fill_basis_is_durable_before_exchange_submit(tmp_path):
    import candidate_c_intent_ledger as il
    ledger=il.IntentLedger.load(str(tmp_path/"intents.jsonl"),str(tmp_path),SYMBOL)
    record=ledger.persist_intent(account_id="test",symbol=SYMBOL,strategy_id="candidate_c",
        setup_id="s1",position_epoch=None,config_version_id="v1",config_hash="hash",
        kind=dec.INTENT_ENTRY,requested_side="short")
    import candidate_c_hybrid_live_adapter as live
    epochs=cem.PositionEpochStore.load(str(tmp_path/"epochs.jsonl"))
    live._persist_entry_basis_before_submit(epochs,record,intent(9,30))
    reloaded=il.IntentLedger.load(ledger.log_path,str(tmp_path),SYMBOL).get(record.intent_id)
    saved=cem.PositionEpochStore.load(epochs.log_path).get(reloaded.intent_id)
    assert saved.pilot_entry is True
    assert "pilot_entry" not in json.loads(Path(ledger.log_path).read_text().splitlines()[0])

def test_real_5m_live_cycle_and_backtest_first_eligible_entry_match(monkeypatch, tmp_path):
    import candidate_c_hybrid_cycle as cycle
    import candidate_c_backtest_signal_adapter as ba
    from test_candidate_c_live_backtest_per_bar_recheck import _cfg, _state, _reject_live, _Window
    state = _state(tmp_path)
    monkeypatch.setattr(cycle.cycle_recon,"reconcile_managed_position",
        lambda *a,**k:{"critical":False,"reason":"verified_flat"})
    _, book, _ = inputs(14,50)
    full=json.loads(gzip.decompress(
        (Path(__file__).parent/"fixtures/candidate_c_doge_20261007.json.gz").read_bytes()))["candles"]
    monkeypatch.setattr(ba,"build_causal_indicator_lookup",lambda *a,**k:book.indicators)
    back_state=ba.CandidateCBacktestAdapterState(
        setup_tracker=st.SetupTracker.in_memory(),epoch_store=cem.PositionEpochStore.in_memory())
    signal=ba.build_candidate_c_signal(SYMBOL,bars_4h=full["4h"],bars_1h=full["1h"],
        bars_1d=[],lot_step=1,min_size=1,adapter_state=back_state)
    live_entries=[]
    for minute_offset in range(0,65,5):
        hour,minute=divmod(8*60+30+minute_offset,60)
        at,book,bars=inputs(hour,minute)
        prepared=cycle._prepare_steady_state_decision_locked(_cfg(tmp_path),object(),SYMBOL,
            bars_4h=bars["4h"],bars_1h=bars["1h"],bars_5m=bars["5m"],
            state=state,account_id="test",config_version_id="v1",config_hash="fixture",
            risk_per_trade_pct=1,lot_step=1,min_size=1,strategy_policy=policy.production_strategy_policy(),
            indicator_fn=book.indicators)
        signal(SYMBOL,_Window(bars["5m"]),None)
        b=back_state.all_intents_seen[-1]
        l=prepared.get("intent")
        if l is not None:
            assert l.kind == b.kind
            assert l.side == b.side
            assert l.setup_id == b.setup_id
            if l.kind == dec.INTENT_ENTRY:
                live_entries.append((hour,minute))
                assert l.pilot_entry and b.pilot_entry
                _reject_live(tmp_path,state,prepared)
        else:
            assert prepared["result"]["intent_kind"] == b.kind
            assert prepared["result"]["reason_code"] == b.reason_code
    assert live_entries == [(9,30)]
    current=dict(side="short",position_id="pilot",contracts=10,
        entry_price=.09354,stop_price=.10,initial_stop_price=.10,
        high_water=.09354,contract_size=1,entry_time_ms=at)
    at,_,bars=inputs(9,35)
    signal(SYMBOL,_Window(bars["5m"]),current)
    assert back_state.all_intents_seen[-1].kind not in (dec.INTENT_EXIT,dec.INTENT_REVERSAL)
    assert back_state.epoch_store.get("pilot").pilot_entry

def test_pilot_ends_if_original_1h_direction_reverses(monkeypatch):
    original=ic.IndicatorBook.indicators
    def indicators(book,bars,*,donchian_n):
        rows=original(book,bars,donchian_n=donchian_n)
        if bars[-1]["close_time_ms"]-bars[-1]["open_time_ms"] == 3600000:
            rows=[dict(r) for r in rows]
            rows[-1].update(ema_20=.09,ema_50=.085,close=.093)
        return rows
    monkeypatch.setattr(ic.IndicatorBook,"indicators",indicators)
    store=cem.PositionEpochStore.in_memory()
    store.save("pilot",cem.PositionEpochState(pilot_entry=True))
    current=dict(side="short",position_id="pilot",contracts=10,
        raw_entry_price=.09354,initial_stop_price=.10,high_water=.09354)
    result=intent(9,35,current=current,epochs=store)
    assert result.reason_code == "4h_direction_invalidated"

def test_promoted_pilot_reverts_to_regular_invalidation_and_persists(tmp_path):
    store=cem.PositionEpochStore.load(str(tmp_path/"epochs.jsonl"))
    epoch=cem.PositionEpochState(pilot_entry=True)
    cem.observe_pilot_4h_confirmation(epoch,side="short",direction_4h="SHORT")
    store.save("pilot",epoch)
    store=cem.PositionEpochStore.load(store.log_path)
    assert store.get("pilot").pilot_4h_confirmed
    current=dict(side="short",position_id="pilot",contracts=10,
        raw_entry_price=.09354,initial_stop_price=.10,high_water=.09354)
    assert intent(9,35,current=current,epochs=store).reason_code == "4h_direction_invalidated"

def test_recovered_fill_uses_durable_pilot_basis_and_preserves_promotion(tmp_path,monkeypatch):
    import candidate_c_intent_ledger as il
    import candidate_c_hybrid_live_adapter as live
    from test_candidate_c_entry_weakening_baseline import _Client,_entry_intent
    from types import SimpleNamespace
    ledger=il.IntentLedger.load(str(tmp_path/"intents.jsonl"),str(tmp_path),SYMBOL)
    intent0=_entry_intent(weakening_baseline=False)
    record=ledger.persist_intent(account_id="acct",symbol=SYMBOL,strategy_id="candidate_c",
        setup_id="setup",position_epoch=None,config_version_id="1",config_hash="hash",
        kind=dec.INTENT_ENTRY,requested_side="long",requested_quantity=4,
        requested_stop_price=90,contract_size=.1,lot_step=.1,min_contracts=.1,
        tick_size=.01,reservation_id="r",reserved_risk_usdt=10,
        strategy_policy=intent0.strategy_policy)
    ledger=il.IntentLedger.load(ledger.log_path,str(tmp_path),SYMBOL)
    epochs=cem.PositionEpochStore.load(str(tmp_path/"epochs.jsonl"))
    from dataclasses import replace
    live._persist_entry_basis_before_submit(epochs,record,replace(intent0,pilot_entry=True))
    epochs=cem.PositionEpochStore.load(epochs.log_path)
    monkeypatch.setattr(live,"_verify_protection_with_retry",lambda *a,**k:{"ok":True,"oco_count":1})
    for promoted in (False,True):
        if promoted:
            epoch=epochs.get(record.intent_id)
            epoch.pilot_4h_confirmed=True
            epochs.save(record.intent_id,epoch)
        live._verify_and_finalize_entry(SimpleNamespace(user_dir=str(tmp_path),logger=None),
            _Client(),intent0,{"current_price":101},.4,
            {"id":"o1","status":"closed","filled":4,"average":101},
            ledger=ledger,epoch_store=epochs,record=ledger.get(record.intent_id),gate_result="approved")
        epochs=cem.PositionEpochStore.load(epochs.log_path)
        assert epochs.get(record.intent_id).pilot_entry
        assert epochs.get(record.intent_id).pilot_4h_confirmed == promoted

def test_legacy_raw_setup_without_direction_evidence_does_not_spend_new_window(tmp_path):
    at,_,_=inputs(8,30)
    path=tmp_path/"legacy.jsonl"
    path.write_text(json.dumps({"type":"state","symbol":SYMBOL,"side":"short",
        "timestamp":at-600000,"new_state":True,
        "setup_id":st.make_setup_id(SYMBOL,"short",at-600000)})+"\n")
    tracker=st.SetupTracker.load(str(path))
    assert len(tracker.all_setup_ids()) == 1
    assert intent(9,30,tracker=tracker).kind == dec.INTENT_ENTRY

def test_monitor_explains_entry_confirmation_wait_instead_of_breakout_ready():
    import candidate_c_trader_adapter as trader
    _,book,bars=inputs(14,40)
    result=intent(14,40)
    monitor=trader._candidate_c_monitor_telemetry(SYMBOL,bars_4h=bars["4h"],
        bars_1h=bars["1h"],bars_5m=bars["5m"],indicator_fn=book.indicators,
        result={"intent_kind":result.kind,"reason_code":result.reason_code},
        blockers=[],live_execute=True)
    assert monitor["stage_text"] == "1H·5m 하락 정렬 확인 대기"
    assert monitor["reason_text"] in monitor["blockers"]

def test_paper_epoch_preserves_same_pilot_hold_thesis():
    import candidate_c_forward_paper_trading as paper
    view=paper._PaperEpochView({"pilot_entry":True,"pilot_4h_confirmed":False})
    assert view.get("paper").pilot_entry is True
    assert view.get("paper").pilot_4h_confirmed is False

def test_pilot_timeout_retry_cannot_become_rule_entry_when_gpt_disabled(monkeypatch,tmp_path):
    import candidate_c_hybrid_cycle as cycle
    from test_candidate_c_live_backtest_per_bar_recheck import _cfg,_state
    state=_state(tmp_path)
    at,book,bars=inputs(9,30)
    snap=tfc.build_as_of_snapshot(at-300000,bars_4h=bars["4h"],
        bars_1h=bars["1h"],bars_1d=[],bars_5m_for_10m=bars["5m"])
    bar_time=snap.bar_10m_current["open_time_ms"]
    sid=state.setup_tracker.observe(SYMBOL,"short",bar_time-600000,True,entry_eligible=True)
    state.setup_tracker.record_attempt_outcome(sid,"timeout")
    state.setup_tracker.arm_timeout_retry(SYMBOL,"short",sid,bar_time)
    state.setup_tracker=st.SetupTracker.load(state.setup_tracker.log_path)
    result=intent(9,30,tracker=state.setup_tracker)
    assert result.kind == dec.INTENT_ENTRY and result.pilot_entry
    assert result.reason_code == "timeout_retry_edge_triggered"
    cfg=_cfg(tmp_path)
    cfg.CANDIDATE_C_GPT_ENTRY_GATE_ENABLED=False
    monkeypatch.setattr(cycle.cycle_recon,"reconcile_managed_position",
        lambda *a,**k:{"critical":False,"reason":"verified_flat"})
    def prepare():
        return cycle._prepare_steady_state_decision_locked(cfg,object(),SYMBOL,
            bars_4h=bars["4h"],bars_1h=bars["1h"],bars_5m=bars["5m"],
            state=state,account_id="test",config_version_id="v1",config_hash="fixture",
            risk_per_trade_pct=1,lot_step=1,min_size=1,strategy_policy=policy.production_strategy_policy(),
            indicator_fn=book.indicators)
    assert prepare()["result"]["gate_result"] == "timeout_retry_skipped_gpt_disabled"
    state.setup_tracker=st.SetupTracker.load(state.setup_tracker.log_path)
    assert state.setup_tracker.pending_timeout_retry_for_bar(SYMBOL,"short",bar_time) is None
    assert prepare()["result"]["intent_kind"] == dec.INTENT_NO_ACTION

def test_native_5m_gap_cannot_skip_recent_chase_guard():
    at,book,bars=inputs(14,40)
    four=book.indicators(bars["4h"],donchian_n=20)[-1]
    gap=bars["5m"][:70]+bars["5m"][71:]
    result=dec._candidate_entry_overextension_gate(side="short",
        entry_price=bars["5m"][-1]["close"],bars_1h=bars["1h"],bars_5m=gap,
        atr14_4h=four["atr_14"],as_of_ms=at,indicator_fn=book.indicators)
    assert result["allowed"] is False
    assert result["reason"] == "recent_entry_data_unavailable"

def test_forward_paper_entry_uses_actual_pilot_fraction(tmp_path):
    from types import SimpleNamespace
    import candidate_c_forward_paper_trading as paper
    _,book,bars=inputs(9,30)
    entry=intent(9,30)
    cfg=SimpleNamespace(user_dir=str(tmp_path),CANDIDATE_C_MAX_CONCURRENT_POSITIONS=2,
        CANDIDATE_C_FIXED_MARGIN_USDT=150,CANDIDATE_C_LEVERAGE=5)
    paper.update(cfg,SYMBOL,{"intent_kind":dec.INTENT_ENTRY,"side":"short",
        "gate_result":"shadow_mode_live_execute_disabled","pilot_entry":entry.pilot_entry,
        "entry_size_fraction":entry.entry_size_fraction,"raw_stop_price":entry.raw_stop_price,
        "raw_target_price":.08},bars["5m"],1,bars_4h=bars["4h"],bars_1h=bars["1h"],
        strategy_policy=policy.production_strategy_policy(),indicator_fn=book.indicators)
    opened=paper._load_open(str(tmp_path),SYMBOL)
    assert opened["pilot_entry"] is True
    assert abs(opened["contracts"]*opened["effective_entry_price"]-187.5)<1e-8

def test_live_bounded_wide_structural_stop_is_capped_for_fixed_margin():
    from adaptive_exit_policy import production_adaptive_exit_policy, policy_sha256
    p=production_adaptive_exit_policy()
    ctx=dec.DecisionContext(account_id="test",symbol=SYMBOL,strategy_id="candidate_c",
        config_version_id="v1",config_hash="fixture",risk_per_trade_pct=1.0,
        sizing_mode="FIXED_MARGIN",strategy_policy=policy.production_strategy_policy(),
        adaptive_exit_mode="LIVE_BOUNDED",adaptive_exit_policy=p,adaptive_leverage=5,
        adaptive_approved_policy_hash=policy_sha256(p))
    legacy=dec.Intent(kind=dec.INTENT_ENTRY,account_id="test",symbol=SYMBOL,strategy_id="candidate_c",
        setup_id="s1",position_epoch=None,config_version_id="v1",config_hash="fixture",
        decision_timestamp=1000,source_candle_close_timestamp=1000,side="short",
        idempotency_key="k",reason_code="setup",input_snapshot_hash="h",requested_risk_pct=1.0)
    result=dec._apply_live_bounded_entry_geometry(ctx,legacy,entry_price=100.0,atr=2.0,
        structural_support=92.0,structural_resistance=107.0)
    assert result.kind == dec.INTENT_ENTRY
    assert abs(result.raw_stop_price-104.0) < 1e-12
    assert abs(result.raw_target_price-88.0) < 1e-12


def test_fixed_margin_sizing_contract_remains_unchanged():
    from types import SimpleNamespace
    import candidate_c_hybrid_live_adapter as live
    cfg=SimpleNamespace(CANDIDATE_C_ORDER_MODE="FIXED_MARGIN_AUTO_EXIT",
        CANDIDATE_C_LEVERAGE=5,CANDIDATE_C_MAX_ORDER_NOTIONAL_USDT=5000.0,
        CANDIDATE_C_FIXED_MARGIN_USDT=150.0,CANDIDATE_C_RISK_PER_TRADE_PCT=1.0)
    client=SimpleNamespace(instrument_metadata=lambda:{"contract_size":1.0,"lot_step":0.01,"min_contracts":0.01})
    wide=SimpleNamespace(raw_stop_price=106.0,entry_size_fraction=1.0)
    result=live._calculate_candidate_c_entry_amount(cfg,client,wide,100.0,1000.0)
    assert result["ok"] is True
    assert result.get("adaptive_risk_capped") is not True
    assert abs(result["notional_usdt"]-750.0) < 1e-8

    narrow=SimpleNamespace(raw_stop_price=103.0,entry_size_fraction=1.0)
    normal=live._calculate_candidate_c_entry_amount(cfg,client,narrow,100.0,1000.0)
    assert normal["ok"] is True
    assert normal.get("wide_stop_fixed_margin_fallback") is None
    assert abs(normal["notional_usdt"]-750.0) < 1e-8
