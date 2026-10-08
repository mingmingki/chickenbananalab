"""Behavior regressions for eligible-clock wide-stop admission (no exchange access).

Native DOGE replay uses the supplied checkpoint, not a renamed SOL fixture.
Synthetic DOGE/SOL geometry covers both directions independently.
"""
from dataclasses import replace
from types import SimpleNamespace

import pytest
import candidate_c_decision_engine as dec
import candidate_c_hybrid_live_adapter as live
import candidate_c_setup_tracker as st
from adaptive_exit_policy import production_adaptive_exit_policy, policy_sha256
from test_candidate_c_adaptive_exit_live_bounded import legacy_intent
from test_candidate_c_doge_live_entry import inputs, SYMBOL

COST = 0.0022  # two 5bp taker fees + two (3bp spread + 3bp slippage) legs


def context(symbol=SYMBOL):
    p = production_adaptive_exit_policy()
    return dec.DecisionContext(account_id='test', symbol=symbol, strategy_id='candidate_c',
        config_version_id='v1', config_hash='fixture', risk_per_trade_pct=1,
        sizing_mode='FIXED_MARGIN', strategy_policy=__import__('candidate_c_strategy_policy').production_strategy_policy(),
        adaptive_exit_mode='LIVE_BOUNDED', adaptive_exit_policy=p, adaptive_leverage=5,
        adaptive_approved_policy_hash=policy_sha256(p))


def entry(side, wide_policy='risk_cap', symbol=SYMBOL):
    result = replace(legacy_intent(), side=side, symbol=symbol, adaptive_mode='LIVE_BOUNDED')
    # Inject the future contract in v1 without requiring its dataclass field to exist;
    # failures must be wrong geometry/sizing, never constructor errors.
    object.__setattr__(result, 'adaptive_wide_stop_policy', wide_policy)
    return result


def geometry(side, wide_policy='risk_cap', structure_distance=7):
    return dec._apply_live_bounded_entry_geometry(context(), entry(side, wide_policy),
        entry_price=100, atr=2, structural_support=100-structure_distance,
        structural_resistance=100+structure_distance)


@pytest.mark.parametrize('side,stop,target', [('long',92.5,112),('short',107.5,88)])
def test_fresh_geometry_preserves_structural_stop_and_caps_actual_tp2(side, stop, target):
    result = geometry(side)
    assert result.kind == dec.INTENT_ENTRY
    assert result.raw_stop_price == pytest.approx(stop)
    assert result.raw_target_price == pytest.approx(target)
    net_rr = (abs(target-100)-100*COST)/(abs(stop-100)+100*COST)
    assert net_rr >= 1.10


@pytest.mark.parametrize('side', ['long','short'])
def test_wide_recheck_cannot_turn_valid_by_clamping_stop(side):
    result = geometry(side, 'block')
    assert result.kind == dec.INTENT_NO_ACTION
    assert result.reason_code == 'adaptive_entry_blocked:stop_distance_above_leverage_cap'
    assert result.raw_stop_price is None and result.raw_target_price is None


@pytest.mark.parametrize('side', ['long','short'])
def test_tp2_cap_requires_post_cost_rr_before_admitting_very_wide_stop(side):
    # 11% risk, 12% capped reward has gross RR 1.09, below policy 1.10.
    result = geometry(side, structure_distance=10.5)
    assert result.kind == dec.INTENT_NO_ACTION
    assert result.reason_code == 'adaptive_entry_blocked:post_cost_rr_below_minimum'


@pytest.mark.parametrize('side', ['long','short'])
def test_post_cost_rr_blocks_boundary_that_gross_rr_would_allow(side):
    # Risk 10.75%, reward 12%: gross RR 1.116; net RR 1.074.
    result = geometry(side, structure_distance=10.25)
    assert result.kind == dec.INTENT_NO_ACTION
    assert result.reason_code == 'adaptive_entry_blocked:post_cost_rr_below_minimum'


def native_decide(tracker):
    import candidate_c_exit_management as cem
    import candidate_c_reversal_state_machine as rsm
    at, book, bars = inputs(9,30)
    return dec.decide(context(), as_of_ms=at-300000,
        bars_4h_confirmed_up_to_asof=bars['4h'], bars_1h_confirmed_up_to_asof=bars['1h'],
        bars_1d_confirmed_up_to_asof=[], bars_5m_for_10m_up_to_asof=bars['5m'],
        setup_tracker=tracker, epoch_store=cem.PositionEpochStore.in_memory(),
        reversal_machine=rsm.SymbolReversalMachine(SYMBOL), current_position=None,
        weakening_prev=False, lot_step=1, min_size=1, indicator_fn=book.indicators)


def test_native_doge_nonempty_tracker_first_eligible_bar_keeps_wide_structure(tmp_path):
    at, _, _ = inputs(8,30)
    tracker = st.SetupTracker.load(str(tmp_path/'setup.jsonl'))
    tracker.observe(SYMBOL,'short',at-600000,True,entry_eligible=False)
    tracker = st.SetupTracker.load(tracker.log_path)
    assert tracker.all_setup_ids() and tracker.current_state(SYMBOL,'short')
    result = native_decide(tracker)
    assert result.kind == dec.INTENT_ENTRY
    assert result.pilot_entry
    assert getattr(result, 'adaptive_wide_stop_policy', None) == 'risk_cap'
    assert result.raw_stop_price == pytest.approx(0.0988574809457604)
    assert result.raw_target_price == pytest.approx(0.09354 * 0.88)
    assert tracker.continuous_entry_eligible_started_ms(SYMBOL,'short') is None


def test_native_doge_eligible_wide_recheck_blocks_without_reset(tmp_path):
    at, book, bars = inputs(9,30)
    snap = dec.tfc.build_as_of_snapshot(at-300000,bars_4h=bars['4h'],bars_1h=bars['1h'],
        bars_1d=[],bars_5m_for_10m=bars['5m'])
    current = snap.bar_10m_current['open_time_ms']
    tracker = st.SetupTracker.load(str(tmp_path/'setup.jsonl'))
    tracker.observe(SYMBOL,'short',current-600000,True,entry_eligible=True)
    tracker = st.SetupTracker.load(tracker.log_path)
    result = native_decide(tracker)
    assert result.kind == dec.INTENT_NO_ACTION
    assert result.reason_code == 'adaptive_entry_blocked:stop_distance_above_leverage_cap'
    assert tracker.continuous_entry_eligible_started_ms(SYMBOL,'short') == current-600000


@pytest.mark.parametrize('side', ['long','short'])
@pytest.mark.parametrize('symbol', [SYMBOL,'SOL/USDT:USDT'])
@pytest.mark.parametrize('fraction', [1.0,0.25])
def test_fixed_margin_wide_stop_reduces_quantity_with_actual_loss_and_notional_caps(side,symbol,fraction):
    cfg = SimpleNamespace(CANDIDATE_C_ORDER_MODE='FIXED_MARGIN_AUTO_EXIT',
        CANDIDATE_C_LEVERAGE=5,CANDIDATE_C_MAX_ORDER_NOTIONAL_USDT=5000,
        CANDIDATE_C_FIXED_MARGIN_USDT=150,CANDIDATE_C_RISK_PER_TRADE_PCT=1)
    client = SimpleNamespace(instrument_metadata=lambda:dict(contract_size=0.1,lot_step=0.1,min_contracts=0.1))
    stop = 92.5 if side == 'long' else 107.5
    intent = replace(entry(side,symbol=symbol),raw_stop_price=stop,entry_size_fraction=fraction)
    object.__setattr__(intent,'adaptive_wide_stop_policy','risk_cap')
    result = live._calculate_candidate_c_entry_amount(cfg,client,intent,100,1000)
    assert result['ok']
    loss = result['amount_coin'] * (7.5+100*COST)
    assert loss <= 10*fraction + 1e-9
    assert result['notional_usdt'] <= 750*fraction + 1e-9
    assert result.get('wide_stop_fixed_margin_fallback') is True
    assert result['planned_loss_usdt'] == pytest.approx(loss)


@pytest.mark.parametrize('order_cap,equity', [(80,1000),(5000,100000)])
def test_wide_sizing_respects_both_order_and_fixed_margin_cap(order_cap,equity):
    cfg=SimpleNamespace(CANDIDATE_C_ORDER_MODE='FIXED_MARGIN_AUTO_EXIT',CANDIDATE_C_LEVERAGE=5,
        CANDIDATE_C_MAX_ORDER_NOTIONAL_USDT=order_cap,CANDIDATE_C_FIXED_MARGIN_USDT=150,
        CANDIDATE_C_RISK_PER_TRADE_PCT=1)
    intent=entry('short'); object.__setattr__(intent,'raw_stop_price',107.5)
    client=SimpleNamespace(instrument_metadata=lambda:dict(contract_size=1,lot_step=.01,min_contracts=.01))
    result=live._calculate_candidate_c_entry_amount(cfg,client,intent,100,equity)
    assert result['ok'] and result.get('adaptive_risk_capped') is True
    assert result['notional_usdt'] <= min(order_cap,750)


def test_fixed_wide_sizing_missing_budget_fails_closed():
    cfg=SimpleNamespace(CANDIDATE_C_ORDER_MODE='FIXED_MARGIN_AUTO_EXIT',CANDIDATE_C_LEVERAGE=5,
        CANDIDATE_C_MAX_ORDER_NOTIONAL_USDT=5000,CANDIDATE_C_FIXED_MARGIN_USDT=150)
    intent=entry('short'); object.__setattr__(intent,'raw_stop_price',107.5)
    client=SimpleNamespace(instrument_metadata=lambda:dict(contract_size=1,lot_step=.01,min_contracts=.01))
    result=live._calculate_candidate_c_entry_amount(cfg,client,intent,100,1000)
    assert result['ok'] is False


@pytest.mark.parametrize('side', ['long','short'])
def test_core_tp2_rescue_final_audit_agrees_with_order_decision(tmp_path,side):
    import trader
    from test_core_wide_stop_risk_sizing import _cfg
    features=dict(atr=2,structural_support=93,structural_resistance=107,
        near_resistance=105,near_support=95,continuation_resistance=120,continuation_support=80,
        source_timestamps=(1000,),input_snapshot_hash='rescue-final')
    legacy=(side,10,96 if side=='long' else 104,106 if side=='long' else 94)
    result=trader._core_adaptive_live_entry_decision(_cfg(tmp_path),symbol='PI/USDT:USDT',
        legacy_order_args=legacy,entry_price=100,equity=1000,market_features=features)
    assert result['blocked'] is False
    assert result['reason']=='adaptive_live_bounded_tp2_rr_rescue'
    assert result['plan'].planned_loss_usdt <= 50 + 1e-9
    assert 0 < result['order_args'][1] < 10
    assert result['order_args'][3] == (112 if side=='long' else 88)
    from pathlib import Path
    import json
    records=[json.loads(row) for p in Path(tmp_path).glob('*adaptive*jsonl') for row in p.read_text().splitlines()]
    assert records and records[-1]['entry_allowed'] is True
    assert records[-1]['reason_code']=='adaptive_live_bounded_tp2_rr_rescue'
    assert records[-1]['base_entry_allowed'] is False
    assert records[-1]['base_reason_code']=='post_cost_rr_below_minimum'


@pytest.mark.parametrize('symbol', [SYMBOL,'SOL/USDT:USDT'])
@pytest.mark.parametrize('side', ['long','short'])
@pytest.mark.parametrize('age,eligible', [(None,False),(10,True),(30,True),(40,True)])
def test_shared_eligible_clock_fresh_recheck_and_stale(symbol,side,age,eligible,monkeypatch,tmp_path):
    from test_candidate_c_live_backtest_per_bar_recheck import _patch_shared_inputs, _bars
    import candidate_c_exit_management as cem
    import candidate_c_reversal_state_machine as rsm
    _patch_shared_inputs(monkeypatch)
    monkeypatch.setattr(dec.tfc,'donchian_setup_condition',lambda snap,wanted:wanted==side)
    def indicators(bars,donchian_n=20):
        return [dict(ema_20=100,ema_50=90 if side=='long' else 110,
                     close=110 if side=='long' else 90,atr_14=2)]
    current=3_600_000
    bars4,bars1,bars5=_bars(current+300000)
    tracker=st.SetupTracker.load(str(tmp_path/'clock.jsonl'))
    prior=current-(600000 if age is None else age*60000)
    tracker.observe(symbol,side,prior,True,entry_eligible=eligible)
    tracker=st.SetupTracker.load(tracker.log_path)
    result=dec._decide_legacy(context(symbol),as_of_ms=current+300000,
        bars_4h_confirmed_up_to_asof=bars4,bars_1h_confirmed_up_to_asof=bars1,
        bars_1d_confirmed_up_to_asof=[],bars_5m_for_10m_up_to_asof=bars5,
        setup_tracker=tracker,epoch_store=cem.PositionEpochStore.in_memory(),
        reversal_machine=rsm.SymbolReversalMachine(symbol),current_position=None,
        weakening_prev=False,lot_step=.01,min_size=.01,indicator_fn=indicators)
    if age==40:
        assert result.kind==dec.INTENT_NO_ACTION
        assert result.reason_code=='setup_stale_after_30m'
        return
    assert result.kind==dec.INTENT_ENTRY
    assert result.reason_code=='setup_bar_recheck'  # raw condition was already True
    assert getattr(result,'adaptive_wide_stop_policy',None)==('risk_cap' if age is None else 'block')
    out=dec._apply_live_bounded_entry_geometry(context(symbol),result,entry_price=100,atr=2,
        structural_support=93,structural_resistance=107)
    assert out.kind==(dec.INTENT_ENTRY if age is None else dec.INTENT_NO_ACTION)
    # Normal-width rechecks retain admission (same clock and guards, no clamp).
    narrow=dec._apply_live_bounded_entry_geometry(context(symbol),result,entry_price=100,atr=.5,
        structural_support=98,structural_resistance=102)
    assert narrow.kind==dec.INTENT_ENTRY
    assert narrow.raw_stop_price==pytest.approx(97.875 if side=='long' else 102.125)
    assert tracker.continuous_entry_eligible_started_ms(symbol,side)==(prior if eligible else None)


def test_timeout_retry_wide_stop_remains_blocked_by_shared_policy(monkeypatch,tmp_path):
    from test_candidate_c_live_backtest_per_bar_recheck import _patch_shared_inputs, _bars, _indicators
    import candidate_c_exit_management as cem
    import candidate_c_reversal_state_machine as rsm
    _patch_shared_inputs(monkeypatch)
    tracker=st.SetupTracker.load(str(tmp_path/'retry.jsonl'))
    sid=tracker.observe(SYMBOL,'long',0,True,entry_eligible=True)
    tracker.record_attempt_outcome(sid,'timeout')
    tracker.arm_timeout_retry(SYMBOL,'long',sid,600000)
    tracker=st.SetupTracker.load(tracker.log_path)
    bars4,bars1,bars5=_bars(900000)
    out=dec._decide_legacy(context(),as_of_ms=900000,
        bars_4h_confirmed_up_to_asof=bars4,bars_1h_confirmed_up_to_asof=bars1,
        bars_1d_confirmed_up_to_asof=[],bars_5m_for_10m_up_to_asof=bars5,
        setup_tracker=tracker,epoch_store=cem.PositionEpochStore.in_memory(),
        reversal_machine=rsm.SymbolReversalMachine(SYMBOL),current_position=None,
        weakening_prev=False,lot_step=.01,min_size=.01,indicator_fn=_indicators)
    assert out.reason_code=='timeout_retry_edge_triggered'
    assert getattr(out,'adaptive_wide_stop_policy',None)=='block'
    assert dec._apply_live_bounded_entry_geometry(context(),out,entry_price=100,atr=2,
        structural_support=93,structural_resistance=107).kind==dec.INTENT_NO_ACTION


def test_fixed_wide_minimum_contracts_does_not_round_risk_budget_up():
    cfg=SimpleNamespace(CANDIDATE_C_ORDER_MODE='FIXED_MARGIN_AUTO_EXIT',CANDIDATE_C_LEVERAGE=5,
        CANDIDATE_C_MAX_ORDER_NOTIONAL_USDT=5000,CANDIDATE_C_FIXED_MARGIN_USDT=150,
        CANDIDATE_C_RISK_PER_TRADE_PCT=.01)
    intent=entry('short'); object.__setattr__(intent,'raw_stop_price',107.5)
    client=SimpleNamespace(instrument_metadata=lambda:dict(contract_size=1,lot_step=1,min_contracts=1))
    result=live._calculate_candidate_c_entry_amount(cfg,client,intent,100,1000)
    assert result=={'ok':False,'reason':'below_minimum'}


def test_native_causal_live_backtest_with_nonempty_tracker_share_bounded_admission(monkeypatch,tmp_path):
    """Replay both real adapters; provide equal adaptive context at decide boundary.

    The historical adapter's public default remains adaptive OFF. This test
    exercises its causal tracker plus the shared LIVE_BOUNDED decision flow;
    it does not claim the historical fill simulator sizes adaptive orders.
    """
    import gzip, json
    from pathlib import Path
    import candidate_c_backtest_signal_adapter as ba
    import candidate_c_hybrid_cycle as cycle
    import candidate_c_exit_management as cem
    from test_candidate_c_live_backtest_per_bar_recheck import _cfg,_state,_reject_live,_Window
    state=_state(tmp_path)
    cfg=_cfg(tmp_path)
    cfg.ADAPTIVE_EXIT_MODE='LIVE_BOUNDED'
    cfg.ADAPTIVE_EXIT_APPROVED_POLICY_HASH=policy_sha256(production_adaptive_exit_policy())
    monkeypatch.setattr(cycle.cycle_recon,'reconcile_managed_position',
        lambda *a,**k:dict(critical=False,reason='verified_flat'))
    _,book,_=inputs(14,50)
    fixture=json.loads(gzip.decompress((Path(__file__).parent/'fixtures/candidate_c_doge_20261007.json.gz').read_bytes()))
    monkeypatch.setattr(ba,'build_causal_indicator_lookup',lambda *a,**k:book.indicators)
    original=dec.decide
    def equal_context(ctx,**kwargs):
        if ctx.adaptive_exit_mode=='OFF':
            ctx=replace(ctx,sizing_mode='FIXED_MARGIN',adaptive_exit_mode='LIVE_BOUNDED',
                adaptive_exit_policy=production_adaptive_exit_policy(),adaptive_leverage=5,
                adaptive_approved_policy_hash=cfg.ADAPTIVE_EXIT_APPROVED_POLICY_HASH)
        return original(ctx,**kwargs)
    monkeypatch.setattr(dec,'decide',equal_context)
    back=ba.CandidateCBacktestAdapterState(setup_tracker=st.SetupTracker.in_memory(),
        epoch_store=cem.PositionEpochStore.in_memory())
    signal=ba.build_candidate_c_signal(SYMBOL,bars_4h=fixture['candles']['4h'],
        bars_1h=fixture['candles']['1h'],bars_1d=[],lot_step=1,min_size=1,adapter_state=back)
    entries=[]
    denied_wide=[]
    for tick in range(8*60+30,10*60+1,5):
        h,m=divmod(tick,60)
        at,book,bars=inputs(h,m)
        if (h,m)==(9,30):
            assert state.setup_tracker.all_setup_ids()
            assert state.setup_tracker.current_state(SYMBOL,'short')
            assert state.setup_tracker.continuous_entry_eligible_started_ms(SYMBOL,'short') is None
            assert back.setup_tracker.all_setup_ids()
        prepared=cycle._prepare_steady_state_decision_locked(cfg,object(),SYMBOL,
            bars_4h=bars['4h'],bars_1h=bars['1h'],bars_5m=bars['5m'],state=state,
            account_id='test',config_version_id='v1',config_hash='fixture',risk_per_trade_pct=1,
            lot_step=1,min_size=1,strategy_policy=context().strategy_policy,indicator_fn=book.indicators)
        signal(SYMBOL,_Window(bars['5m']),None)
        b=back.all_intents_seen[-1]
        l=prepared.get('intent')
        if l is not None:
            assert (l.kind,l.side,l.setup_id)==(b.kind,b.side,b.setup_id)
            assert l.raw_stop_price==b.raw_stop_price and l.raw_target_price==b.raw_target_price
            assert getattr(l,'adaptive_wide_stop_policy',None)==getattr(b,'adaptive_wide_stop_policy',None)
            if l.kind==dec.INTENT_ENTRY:
                entries.append((h,m))
                assert l.raw_stop_price==pytest.approx(.0988574809457604)
                assert l.raw_target_price==pytest.approx(.0823152)
                _reject_live(tmp_path,state,prepared)
        else:
            assert prepared['result']['intent_kind']==b.kind
            assert prepared['result']['reason_code']==b.reason_code
            if b.reason_code=='adaptive_entry_blocked:stop_distance_above_leverage_cap':
                denied_wide.append((h,m))
    assert entries==[(9,30)]
    # Later native bars remain governed by direction/recent-entry guards.


@pytest.mark.parametrize('side', ['long','short'])
@pytest.mark.parametrize('cost', [.001,.01])
def test_core_actual_tp2_rescue_preserves_supplied_cost_and_risk_caps(tmp_path,monkeypatch,side,cost):
    import trader
    from adaptive_exit_engine import AdaptiveExitContext
    from test_core_wide_stop_risk_sizing import _cfg
    monkeypatch.setattr(trader,'AdaptiveExitContext',
        lambda **kwargs:AdaptiveExitContext(**dict(kwargs,estimated_roundtrip_cost_rate=cost)))
    f=dict(atr=2,structural_support=93,structural_resistance=107,
        near_resistance=105,near_support=95,continuation_resistance=120,continuation_support=80,
        source_timestamps=(1000,),input_snapshot_hash='cost-supplied')
    result=trader._core_adaptive_live_entry_decision(_cfg(tmp_path),symbol='PI/USDT:USDT',
        legacy_order_args=(side,10,96 if side=='long' else 104,106 if side=='long' else 94),
        entry_price=100,equity=1000,market_features=f)
    assert result['blocked'] is False
    order=result['order_args']
    assert order[0]==side and 0<order[1]<=10
    assert abs(order[2]-100)==7.5 and abs(order[3]-100)==12
    assert order[1]*(7.5+100*cost)<=50+1e-9
    assert (12-100*cost)/(7.5+100*cost)>=1.10


@pytest.mark.parametrize('side', ['long','short'])
def test_core_rescue_blocks_when_actual_tp2_cost_adjusted_rr_is_below_minimum(tmp_path,monkeypatch,side):
    import trader
    from adaptive_exit_engine import AdaptiveExitContext
    from test_core_wide_stop_risk_sizing import _cfg
    monkeypatch.setattr(trader,'AdaptiveExitContext',
        lambda **kwargs:AdaptiveExitContext(**dict(kwargs,estimated_roundtrip_cost_rate=.02)))
    # With 8% risk and 12% reward, cost raises risk to 10%, reduces reward
    # to 10%; a gross-RR rescue would incorrectly permit this order.
    f=dict(atr=2,structural_support=92.5,structural_resistance=107.5,
        near_resistance=105,near_support=95,continuation_resistance=120,continuation_support=80,
        source_timestamps=(1000,),input_snapshot_hash='cost-block')
    legacy=(side,10,96 if side=='long' else 104,106 if side=='long' else 94)
    result=trader._core_adaptive_live_entry_decision(_cfg(tmp_path),symbol='PI/USDT:USDT',
        legacy_order_args=legacy,entry_price=100,equity=1000,market_features=f)
    assert result['blocked'] is True and result['order_args']==legacy
    assert result['reason']=='post_cost_rr_below_minimum'


@pytest.mark.parametrize('mode', [None,'OFF','SHADOW','ADVISORY'])
def test_unapplied_adaptive_geometry_marker_cannot_change_fixed_margin_sizing(mode):
    cfg=SimpleNamespace(CANDIDATE_C_ORDER_MODE='FIXED_MARGIN_AUTO_EXIT',CANDIDATE_C_LEVERAGE=5,
        CANDIDATE_C_MAX_ORDER_NOTIONAL_USDT=5000,CANDIDATE_C_FIXED_MARGIN_USDT=150,
        CANDIDATE_C_RISK_PER_TRADE_PCT=1)
    intent=replace(entry('short'),raw_stop_price=107.5,adaptive_mode=mode)
    object.__setattr__(intent,'adaptive_wide_stop_policy','risk_cap')
    client=SimpleNamespace(instrument_metadata=lambda:dict(contract_size=1,lot_step=.01,min_contracts=.01))
    result=live._calculate_candidate_c_entry_amount(cfg,client,intent,100,1000)
    assert result['ok'] and result['notional_usdt']==750
    assert result.get('adaptive_risk_capped') is not True


def test_manual_order_mode_never_uses_automatic_wide_stop_risk_fallback():
    cfg=SimpleNamespace(CANDIDATE_C_ORDER_MODE='MANUAL_ALL',CANDIDATE_C_LEVERAGE=5,
        CANDIDATE_C_MAX_ORDER_NOTIONAL_USDT=5000,CANDIDATE_C_FIXED_MARGIN_USDT=150,
        CANDIDATE_C_RISK_PER_TRADE_PCT=1)
    intent=replace(entry('short'),raw_stop_price=107.5)
    client=SimpleNamespace(instrument_metadata=lambda:dict(contract_size=1,lot_step=.01,min_contracts=.01))
    result=live._calculate_candidate_c_entry_amount(cfg,client,intent,100,1000)
    assert result['ok'] and result['notional_usdt']==750


def test_wide_geometry_keeps_risk_budget_sizing_if_fresh_price_moves_inside_normal_width():
    # Geometry was wide at the decision (4.1%). A 0.21% move towards the stop
    # must not promote the already risk-capped order back to full margin.
    ctx=context(); intent=entry('short')
    intent=dec._apply_live_bounded_entry_geometry(ctx,intent,entry_price=100,atr=1,
        structural_support=95,structural_resistance=103.85)
    assert intent.kind==dec.INTENT_ENTRY and intent.raw_stop_price==pytest.approx(104.1)
    cfg=SimpleNamespace(CANDIDATE_C_ORDER_MODE='FIXED_MARGIN_AUTO_EXIT',CANDIDATE_C_LEVERAGE=5,
        CANDIDATE_C_MAX_ORDER_NOTIONAL_USDT=5000,CANDIDATE_C_FIXED_MARGIN_USDT=150,
        CANDIDATE_C_RISK_PER_TRADE_PCT=1)
    client=SimpleNamespace(instrument_metadata=lambda:dict(contract_size=1,lot_step=.001,min_contracts=.001))
    result=live._calculate_candidate_c_entry_amount(cfg,client,intent,100.21,1000)
    assert result['ok']
    assert result['amount_coin']*(104.1-100.21+100.21*COST)<=10+1e-9
    assert result.get('wide_stop_fixed_margin_fallback') is True


@pytest.mark.parametrize('side,fresh,expected', [('long',99,110.88),('short',101,88.88)])
def test_order_price_refresh_caps_actual_tp2_in_both_directions(side,fresh,expected):
    intent=geometry(side)
    cfg=SimpleNamespace(CANDIDATE_C_ORDER_MODE='FIXED_MARGIN_AUTO_EXIT',CANDIDATE_C_LEVERAGE=5)
    stop,target=live._candidate_c_entry_protection_prices(cfg,intent,fresh)
    assert stop==(92.5 if side=='long' else 107.5)
    assert target==pytest.approx(expected)
    assert abs(target-fresh)/fresh*5*100<=60+1e-9
    assert (abs(target-fresh)-fresh*COST)/(abs(stop-fresh)+fresh*COST)>=1.1


@pytest.mark.parametrize('side,fresh', [('long',102),('short',98)])
def test_order_refresh_cannot_submit_below_minimum_cost_adjusted_tp2_rr(side,fresh):
    cfg=SimpleNamespace(CANDIDATE_C_ORDER_MODE='FIXED_MARGIN_AUTO_EXIT',CANDIDATE_C_LEVERAGE=5)
    with pytest.raises(ValueError,match='post_cost_rr_below_minimum'):
        live._candidate_c_entry_protection_prices(cfg,geometry(side),fresh)


@pytest.mark.parametrize('side,fresh', [('long',92),('short',108)])
def test_order_refresh_preserves_stop_direction_validation(side,fresh):
    cfg=SimpleNamespace(CANDIDATE_C_ORDER_MODE='FIXED_MARGIN_AUTO_EXIT',CANDIDATE_C_LEVERAGE=5)
    with pytest.raises(ValueError,match='invalid_adaptive_price_direction'):
        live._candidate_c_entry_protection_prices(cfg,geometry(side),fresh)


@pytest.mark.parametrize('side', ['long','short'])
def test_core_rescue_returns_allowed_plan_to_existing_verified_ai_overlay(tmp_path,side):
    import trader
    from adaptive_exit_engine import apply_ai_price_plan
    from test_core_wide_stop_risk_sizing import _cfg
    f=dict(atr=2,structural_support=93,structural_resistance=107,near_resistance=105,near_support=95,
        continuation_resistance=120,continuation_support=80,source_timestamps=(1000,),input_snapshot_hash='ai-rescue')
    result=trader._core_adaptive_live_entry_decision(_cfg(tmp_path),symbol='PI/USDT:USDT',
        legacy_order_args=(side,10,96 if side=='long' else 104,106 if side=='long' else 94),
        entry_price=100,equity=1000,market_features=f)
    assert result['plan'].entry_allowed is True
    selected=dict(stop_loss_price=96 if side=='long' else 104,
                  take_profit_1_price=106 if side=='long' else 94,
                  take_profit_2_price=112 if side=='long' else 88)
    applied,reason=apply_ai_price_plan(result['plan'],result['context'],selected,production_adaptive_exit_policy())
    assert reason=='ai_exit_plan_applied' and applied.entry_allowed


def test_live_entry_validates_verified_ai_prices_then_sizes_remembered_wide_risk(monkeypatch,tmp_path):
    from contextlib import nullcontext
    import logging
    import symbol_entry_control
    cfg=SimpleNamespace(user_dir=str(tmp_path),logger=logging.getLogger('wide-ai-fixture'),
        CANDIDATE_C_GPT_ENTRY_GATE_ENABLED=True,CANDIDATE_C_LIVE_EXECUTE=True,
        CANDIDATE_C_ORDER_MODE='FIXED_MARGIN_AUTO_EXIT',CANDIDATE_C_LEVERAGE=5,
        CANDIDATE_C_MAX_ORDER_NOTIONAL_USDT=5000,CANDIDATE_C_FIXED_MARGIN_USDT=150,
        CANDIDATE_C_RISK_PER_TRADE_PCT=1)
    gate=dict(allowed=True,gate_result='approved',error_reason=None,ai_exit_source='gemini_approved',
        ai_exit_plan=dict(stop_loss_price=98,take_profit_1_price=108,take_profit_2_price=114))
    monkeypatch.setattr(live,'_candidate_c_new_entry_allowed',lambda *a,**k:True)
    monkeypatch.setattr(live,'_candidate_manual_close_entry_block',lambda *a,**k:None)
    monkeypatch.setattr(live.gga,'verify_candidate_signal',lambda *a,**k:gate)
    monkeypatch.setattr(live.ownership,'account_order_lock',lambda *a,**k:nullcontext())
    monkeypatch.setattr(symbol_entry_control,'is_paused',lambda *a,**k:False)
    meta=dict(contract_size=.1,lot_step=.1,min_contracts=.1)
    client=SimpleNamespace(fetch_usdt_equity=lambda:1000,fetch_position=lambda:None,
        ensure_leverage=lambda lev:None,fetch_pending_protection_algo_ids=lambda:[],
        fetch_last_price=lambda:102,instrument_metadata=lambda:meta)
    ledger=SimpleNamespace(refresh=lambda:None,pending_intents=lambda:[])
    original=live._calculate_candidate_c_entry_amount
    class SizedLocally(Exception):
        pass
    def size_then_stop(cfg,client,intent,price,equity):
        assert (intent.raw_stop_price,intent.raw_target_price)==(98,114)
        result=original(cfg,client,intent,price,equity)
        assert result['ok']
        assert result['amount_coin']*(4+102*COST)<=10+1e-9
        assert result.get('wide_stop_fixed_margin_fallback') is True
        raise SizedLocally
    monkeypatch.setattr(live,'_calculate_candidate_c_entry_amount',size_then_stop)
    # At 102 the fallback TP2 RR is insufficient. The safe verified AI plan
    # tightens SL and restores RR before final validation; no order is submitted.
    with pytest.raises(SizedLocally):
        live._execute_entry(cfg,client,geometry('long'),{'atr_4h':2},1000,lambda:True,
            None,ledger=ledger,epoch_store=None)
