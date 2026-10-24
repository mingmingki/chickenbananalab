from dataclasses import replace
from types import SimpleNamespace

import pytest
import candidate_c_decision_engine as dec
import candidate_c_hybrid_live_adapter as live
from adaptive_exit_policy import production_adaptive_exit_policy, policy_sha256
from test_candidate_c_adaptive_exit_live_bounded import legacy_intent, ctx, cfg, Client


def compact_context(symbol):
    value = ctx(approved=policy_sha256(production_adaptive_exit_policy()))
    value = replace(value, symbol=symbol, adaptive_leverage=5)
    object.__setattr__(value, 'compact_protection_enabled', True)
    return value


@pytest.mark.parametrize('symbol', ['DOGE/USDT:USDT', 'SOL/USDT:USDT'])
@pytest.mark.parametrize('side', ['long', 'short'])
@pytest.mark.parametrize('wide_policy', ['risk_cap', 'block'])
def test_compact_geometry_caps_actual_prices_without_wide_sizing_exception(symbol, side, wide_policy):
    intent = replace(legacy_intent(), symbol=symbol, side=side,
                     adaptive_wide_stop_policy=wide_policy)
    result = dec._apply_live_bounded_entry_geometry(
        compact_context(symbol), intent, entry_price=100, atr=2,
        structural_support=92, structural_resistance=108)
    assert result.kind == dec.INTENT_ENTRY
    assert abs(result.raw_stop_price - 100) <= 2 + 1e-10
    assert abs(result.raw_target_price - 100) <= 4 + 1e-10
    assert not result.adaptive_wide_stop_risk_sizing
    assert result.adaptive_wide_stop_policy == 'block'
    cost = 100 * dec.CANDIDATE_C_ROUNDTRIP_COST_RATE
    assert (abs(result.raw_target_price - 100) - cost) / (abs(result.raw_stop_price - 100) + cost) >= 1.1


@pytest.mark.parametrize('side,stop,target,expected_stop,expected_target', [
    ('long', 94, 112, 98, 104), ('short', 106, 88, 102, 96)])
def test_execution_prices_enforce_caps_using_fresh_entry(side, stop, target, expected_stop, expected_target):
    config = cfg(); config.CANDIDATE_C_COMPACT_PROTECTION_ENABLED = True
    config.CANDIDATE_C_ORDER_MODE = 'AUTO_ALL'
    intent = replace(legacy_intent(), side=side, raw_stop_price=stop, raw_target_price=target,
                     adaptive_mode='LIVE_BOUNDED', adaptive_wide_stop_policy='risk_cap')
    assert live._candidate_c_entry_protection_prices(config, intent, 100) == (expected_stop, expected_target)


def test_compact_variable_size_includes_cost_inside_one_percent_budget():
    config = cfg(); config.CANDIDATE_C_COMPACT_PROTECTION_ENABLED = True
    config.CANDIDATE_C_ORDER_MODE = 'AUTO_ALL'
    intent = replace(legacy_intent(), raw_stop_price=98, raw_target_price=104)
    result = live._calculate_candidate_c_entry_amount(config, Client(), intent, 100, 2000)
    assert result['ok']
    coins = result['notional_usdt'] / 100
    assert coins * (2 + 100 * dec.CANDIDATE_C_ROUNDTRIP_COST_RATE) <= 20 + 1e-8


def test_existing_short_stop_is_tightened_to_cap_and_never_loosened():
    context = compact_context('DOGE/USDT:USDT')
    intent = replace(legacy_intent(), kind=dec.INTENT_STOP_UPDATE, side='short', raw_stop_price=111)
    position = dict(side='short', raw_entry_price=100, mark_price=99, high_water=99,
                    current_stop_price=106)
    result = dec._apply_live_bounded_held_stop(context, intent, position, atr=2)
    assert result.raw_stop_price == 102
    position['current_stop_price'] = 100.2
    result = dec._apply_live_bounded_held_stop(context, intent, position, atr=2)
    assert result.raw_stop_price <= 100.2


def test_native_doge_confirmed_bars_keep_entry_with_compact_prices():
    import candidate_c_exit_management as cem
    import candidate_c_reversal_state_machine as rsm
    import candidate_c_setup_tracker as tracker
    import candidate_c_strategy_policy as policy
    from test_candidate_c_doge_live_entry import inputs
    at, book, bars = inputs(9, 30)
    context = replace(compact_context('DOGE/USDT:USDT'),
                      strategy_policy=policy.production_strategy_policy())
    object.__setattr__(context, 'compact_protection_enabled', True)
    result = dec.decide(context, as_of_ms=at-300000,
        bars_4h_confirmed_up_to_asof=bars['4h'], bars_1h_confirmed_up_to_asof=bars['1h'],
        bars_1d_confirmed_up_to_asof=[], bars_5m_for_10m_up_to_asof=bars['5m'],
        setup_tracker=tracker.SetupTracker.in_memory(), epoch_store=cem.PositionEpochStore.in_memory(),
        reversal_machine=rsm.SymbolReversalMachine(context.symbol), current_position=None,
        weakening_prev=False, lot_step=1, min_size=1, indicator_fn=book.indicators)
    assert result.kind == dec.INTENT_ENTRY
    assert result.side == 'short'
    entry = bars['5m'][-1]['close']
    assert abs(result.raw_stop_price-entry)/entry <= .02 + 1e-10
    assert abs(result.raw_target_price-entry)/entry <= .04 + 1e-10


@pytest.mark.parametrize('side,stop,target', [('long', 99.5, 101.5), ('short', 100.5, 98.5)])
def test_closer_valid_prices_are_preserved(side, stop, target):
    config = cfg(); config.CANDIDATE_C_COMPACT_PROTECTION_ENABLED = True
    config.CANDIDATE_C_ORDER_MODE = 'AUTO_ALL'
    intent = replace(legacy_intent(), side=side, raw_stop_price=stop, raw_target_price=target)
    assert live._candidate_c_entry_protection_prices(config, intent, 100) == (stop, target)

@pytest.mark.parametrize('side', ['long', 'short'])
def test_exact_tick_prices_stay_inside_both_distance_caps(side):
    import candidate_c_protection_bounds as bounds
    e=.100003
    stop,target=bounds.normalized_prices(side,e,.09 if side=='long' else .11,
                                         .12 if side=='long' else .08,.00001)
    assert abs(stop-e)/e <= .02
    assert abs(target-e)/e <= .04
    assert round(stop/.00001)==pytest.approx(stop/.00001)
    assert round(target/.00001)==pytest.approx(target/.00001)


def test_live_position_schema_supplies_epoch_stop():
    import candidate_c_position_reconciliation as recon
    epoch=SimpleNamespace(side='short',entry_intent_id='e',remaining_contracts=1,
        raw_entry_price=100,initial_stop_price=106,current_stop_price=102,high_water=99,
        effective_entry_price=100,entry_fee_usdt=0,contract_size=1,fee_rate=.0005,
        spread_bps=3,slippage_bps=3)
    assert recon.build_candidate_c_position_state(epoch)['current_stop_price']==102


def test_already_tighter_stop_preserves_profit_lock_activation_reason():
    intent=replace(legacy_intent(),kind=dec.INTENT_STOP_UPDATE,side='long',
        raw_stop_price=100.22,reason_code='profit_lock_activated')
    p=dict(side='long',raw_entry_price=100,current_stop_price=101,mark_price=102,high_water=102)
    result=dec._apply_live_bounded_held_stop(compact_context(intent.symbol),intent,p,atr=2)
    assert result.reason_code=='profit_lock_activated'
    assert result.raw_stop_price==101


def test_actual_fill_adjustment_amends_owned_oco_and_verifies_both_prices():
    c=cfg();c.CANDIDATE_C_COMPACT_PROTECTION_ENABLED=True;c.CANDIDATE_C_ORDER_MODE='AUTO_ALL'
    intent=replace(legacy_intent(),raw_stop_price=98,raw_target_price=104)
    class FillClient:
        def __init__(self):self.order=dict(algo_id='a',algo_cl_ord_id='owned',sl_price=98,tp_price=104,sz=2,side='sell',state='live');self.calls=[]
        def fetch_pending_protection_algo_ids(self):return ['a']
        def fetch_protection_order_by_algo_id(self,a):return self.order.copy()
        def instrument_metadata(self):return {'tick_size':.01}
        def fetch_last_price(self):return 100.1
        def amend_protective_stop(self,a,**kw):
            self.calls.append((a,kw));self.order.update(sl_price=kw['new_sl_price'],tp_price=kw['new_tp_price']);return {'ok':True}
    client=FillClient();record=SimpleNamespace(attach_algo_cl_ord_id='owned',tick_size=.01)
    result=live._compact_actual_fill_protection(c,client,intent,dict(contracts=2,entry_price=100.1),record)
    assert result.raw_stop_price==98.1
    assert client.calls[0][0]=='a'
    assert client.calls[0][1]==dict(new_sl_price=98.1,new_tp_price=104)
    assert client.order['sz']==2
    client.order['algo_cl_ord_id']='other'
    with pytest.raises(ValueError,match='ownership'):live._compact_actual_fill_protection(c,client,intent,dict(contracts=2,entry_price=100.1),record)


def test_compact_native_live_and_public_backtest_adapters_match(monkeypatch,tmp_path):
    import gzip,json
    from pathlib import Path
    import candidate_c_backtest_signal_adapter as ba
    import candidate_c_hybrid_cycle as cycle
    import candidate_c_exit_management as cem
    import candidate_c_setup_tracker as st
    import candidate_c_strategy_policy as policy
    from test_candidate_c_doge_live_entry import inputs
    from test_candidate_c_live_backtest_per_bar_recheck import _cfg,_state,_reject_live,_Window
    symbol='DOGE/USDT:USDT';state=_state(tmp_path);config=_cfg(tmp_path)
    config.ADAPTIVE_EXIT_MODE='LIVE_BOUNDED';config.CANDIDATE_C_COMPACT_PROTECTION_ENABLED=True
    config.CANDIDATE_C_ORDER_MODE='AUTO_ALL';config.CANDIDATE_C_SIZING_MODE='VARIABLE_RISK'
    config.ADAPTIVE_EXIT_APPROVED_POLICY_HASH=policy_sha256(production_adaptive_exit_policy())
    monkeypatch.setattr(cycle.cycle_recon,'reconcile_managed_position',lambda *a,**kw:dict(critical=False,reason='verified_flat'))
    _,book,_=inputs(14,50)
    fixture=json.loads(gzip.decompress((Path(__file__).parent/'fixtures/candidate_c_doge_20261007.json.gz').read_bytes()))
    monkeypatch.setattr(ba,'build_causal_indicator_lookup',lambda *a,**kw:book.indicators)
    back=ba.CandidateCBacktestAdapterState(st.SetupTracker.in_memory(),cem.PositionEpochStore.in_memory())
    signal=ba.build_candidate_c_signal(symbol,bars_4h=fixture['candles']['4h'],bars_1h=fixture['candles']['1h'],
        bars_1d=[],lot_step=1,min_size=1,adapter_state=back,compact_protection_enabled=True,
        adaptive_exit_mode='LIVE_BOUNDED',adaptive_exit_policy=production_adaptive_exit_policy(),
        adaptive_approved_policy_hash=config.ADAPTIVE_EXIT_APPROVED_POLICY_HASH,adaptive_leverage=5)
    entries=[]
    for tick in range(8*60+30,10*60+1,5):
        h,m=divmod(tick,60);at,book,bars=inputs(h,m)
        prepared=cycle._prepare_steady_state_decision_locked(config,object(),symbol,
            bars_4h=bars['4h'],bars_1h=bars['1h'],bars_5m=bars['5m'],state=state,
            account_id='test',config_version_id='v1',config_hash='fixture',risk_per_trade_pct=1,
            lot_step=1,min_size=1,strategy_policy=policy.production_strategy_policy(),indicator_fn=book.indicators)
        signal(symbol,_Window(bars['5m']),None);b=back.all_intents_seen[-1];l=prepared.get('intent')
        if l is None:
            assert prepared['result']['intent_kind']==b.kind
            assert prepared['result']['reason_code']==b.reason_code
        else:
            assert (l.kind,l.side,l.raw_stop_price,l.raw_target_price)==(b.kind,b.side,b.raw_stop_price,b.raw_target_price)
            if l.kind==dec.INTENT_ENTRY:
                entries.append((h,m));e=bars['5m'][-1]['close']
                assert abs(l.raw_stop_price-e)/e<=.02+1e-10
                assert abs(l.raw_target_price-e)/e<=.04+1e-10
                _reject_live(tmp_path,state,prepared)
    assert entries==[(9,30)]


def test_amend_tp_and_sl_use_one_existing_order_without_cancel():
    from okx_client import OkxClient
    calls=[]
    exchange=SimpleNamespace(market=lambda s:{'id':'DOGE-USDT-SWAP'},
        price_to_precision=lambda s,p:str(p),
        private_post_trade_amend_algos=lambda req:(calls.append(req) or {'code':'0','data':[{'sCode':'0','algoId':'owned'}]}))
    client=OkxClient.__new__(OkxClient);client.symbol='DOGE/USDT:USDT';client.exchange=exchange
    assert client.amend_protective_stop('owned',new_sl_price=.0867,new_tp_price=.0816)['ok']
    assert calls==[[{'algoId':'owned','instId':'DOGE-USDT-SWAP','cxlOnFail':False,
                    'newSlTriggerPx':'0.0867','newSlTriggerPxType':'last',
                    'newTpTriggerPx':'0.0816','newTpTriggerPxType':'last'}]]


def test_actual_fill_preserves_original_cost_inclusive_budget():
    # Adverse fill changes entry-stop distance even when SL is within 2%.
    config=cfg();config.CANDIDATE_C_COMPACT_PROTECTION_ENABLED=True
    config.CANDIDATE_C_ORDER_MODE='AUTO_ALL';config.CANDIDATE_C_SIZING_MODE='VARIABLE_RISK'
    coins=20/1.22
    record=SimpleNamespace(attach_algo_cl_ord_id='owned',tick_size=.001,
        reserved_risk_usdt=coins,requested_quantity=coins,contract_size=1,entry_price_estimate=100)
    intent=replace(legacy_intent(),raw_stop_price=99,raw_target_price=103)
    class BudgetClient:
        def __init__(self):self.o=dict(algo_cl_ord_id='owned',sz=coins,side='sell',sl_price=99,tp_price=103)
        def fetch_pending_protection_algo_ids(self):return ['a']
        def fetch_protection_order_by_algo_id(self,a):return self.o.copy()
        def fetch_last_price(self):return 100.1
        def amend_protective_stop(self,a,**kw):self.o.update(sl_price=kw['new_sl_price'],tp_price=kw['new_tp_price']);return {'ok':True}
    result=live._compact_actual_fill_protection(config,BudgetClient(),intent,dict(contracts=coins,entry_price=100.1),record)
    assert coins*(100.1-result.raw_stop_price+100.1*.0022)<=20


@pytest.mark.parametrize('failure',['reject','timeout','read_after_amend'])
def test_pending_compact_entry_retries_after_durable_epoch_exists(tmp_path,monkeypatch,failure):
    import candidate_c_intent_ledger as il
    import candidate_c_exit_management as cem
    import candidate_c_cycle_reconciliation as recon
    import candidate_c_reversal_state_machine as rsm
    from unittest.mock import MagicMock
    symbol='DOGE/USDT:USDT';ledger=il.IntentLedger.load(str(tmp_path/'intents.jsonl'),str(tmp_path),symbol)
    epochs=cem.PositionEpochStore.load(str(tmp_path/'epochs.jsonl'))
    intent=replace(legacy_intent(),raw_stop_price=98,raw_target_price=104)
    record=ledger.persist_intent(account_id='acct',symbol=symbol,strategy_id='candidate_c',setup_id='setup',
        position_epoch=None,config_version_id='1',config_hash='cfg',kind=dec.INTENT_ENTRY,
        requested_side='long',requested_quantity=2,requested_stop_price=98,requested_target_price=104,
        contract_size=1,lot_step=.01,min_contracts=.01,tick_size=.01,reservation_id='r',reserved_risk_usdt=4)
    config=cfg();config.user_dir=str(tmp_path);config.logger=None;config.CANDIDATE_C_LIVE_EXECUTE=True
    config.CANDIDATE_C_COMPACT_PROTECTION_ENABLED=True;config.CANDIDATE_C_ORDER_MODE='AUTO_ALL'
    position=dict(side='long',contracts=2,entry_price=100.1,position_id='p',entry_timestamp_ms=1000)
    order=dict(id='o',status='closed',filled=2,average=100.1)
    class RecoveryClient:
        def __init__(self):
            self.fail=True;self.post_read=False
            self.o=dict(algo_id='a',algo_cl_ord_id=record.attach_algo_cl_ord_id,sz=2,side='sell',sl_price=98,tp_price=104)
            self.exchange=SimpleNamespace(fetch_open_orders=lambda s:[])
        def fetch_position(self):return position.copy()
        def fetch_pending_protection_algo_ids(self):return ['a']
        def fetch_protection_order_by_algo_id(self,a):
            if self.fail and self.post_read:raise TimeoutError('read')
            return self.o.copy()
        def fetch_order_status_by_client_id(self,a):return order.copy()
        def fetch_last_price(self):return 100.1
        def fetch_trades_for_order(self,*a,**k):return []
        def amend_protective_stop(self,a,**kw):
            if self.fail and failure=='timeout':raise TimeoutError('amend')
            if self.fail and failure=='reject':return {'ok':False}
            self.o.update(sl_price=kw['new_sl_price'],tp_price=kw['new_tp_price'])
            self.post_read=self.fail and failure=='read_after_amend';return {'ok':True}
    client=RecoveryClient()
    monkeypatch.setattr(live,'_verify_protection_with_retry',lambda *a,**kw:dict(ok=True,oco_count=1))
    monkeypatch.setattr(recon.adapter,'reconcile_pending_management',lambda *a,**kw:{})
    monkeypatch.setattr(recon.ownership,'validate_candidate_c_position_owner',lambda *a,**kw:dict(allowed=True))
    monkeypatch.setattr(recon.order_safety,'verify_protection',lambda *a,**kw:dict(ok=True))
    first=live._verify_and_finalize_entry(config,client,intent,{},2,order,ledger,epochs,record,gate_result='approved')
    assert first['pending'];assert epochs.get(record.intent_id).entry_intent_id
    client.fail=False;client.post_read=False
    machine=rsm.SymbolReversalMachine(symbol)
    state=SimpleNamespace(intent_ledger=ledger,epoch_store=epochs,notification_events=[],
        reversal_store=SimpleNamespace(get=lambda s:machine,persist=lambda *a:None))
    second=recon.reconcile_managed_position(config,client,symbol,state,2000)
    assert not second.get('critical'),second
    assert ledger.get(record.intent_id).state==il.IntentState.PROTECTED.value
    assert epochs.get(record.intent_id).protective_algo_ids==['a']
    assert epochs.get(record.intent_id).current_stop_price==98.1


@pytest.mark.parametrize('remaining', [2, 1])
def test_held_pending_amendment_never_reenters_initial_fill_recovery(tmp_path,monkeypatch,remaining):
    import candidate_c_cycle_reconciliation as recon
    import candidate_c_intent_ledger as il
    import candidate_c_exit_management as cem
    import candidate_c_reversal_state_machine as rsm
    symbol='DOGE/USDT:USDT';ledger=il.IntentLedger.load(str(tmp_path/'intents.jsonl'),str(tmp_path),symbol)
    record=ledger.persist_intent(account_id='acct',symbol=symbol,strategy_id='candidate_c',setup_id='s',position_epoch=None,
        config_version_id='1',config_hash='cfg',kind=dec.INTENT_ENTRY,requested_side='long',requested_quantity=2,
        requested_stop_price=98,requested_target_price=104,contract_size=1,lot_step=.01,min_contracts=.01,tick_size=.01,
        reservation_id='r',reserved_risk_usdt=4)
    ledger.mark_filled(record.intent_id,2);ledger.mark_protected(record.intent_id,['a'])
    ledger.mark_remote_submission_finalized(record.intent_id);ledger.mark_protection_pending(record.intent_id)
    epochs=cem.PositionEpochStore.in_memory()
    epoch=cem.PositionEpochState(account_id='acct',symbol=symbol,side='long',entry_intent_id=record.intent_id,
        raw_entry_price=100,initial_stop_price=98,current_stop_price=101,target_price=104,
        original_contracts=2,remaining_contracts=remaining,high_water=103,profit_lock_active=True,
        partial_take_profit_done=remaining==1,protective_algo_ids=['a'])
    epochs.save(record.intent_id,epoch)
    client=SimpleNamespace(fetch_position=lambda:dict(side='long',contracts=remaining,entry_price=100),
        fetch_pending_protection_algo_ids=lambda:['a'],exchange=SimpleNamespace(fetch_open_orders=lambda s:[]))
    config=cfg();config.user_dir=str(tmp_path);config.CANDIDATE_C_COMPACT_PROTECTION_ENABLED=True
    config.CANDIDATE_C_LIVE_EXECUTE=True;config.CANDIDATE_C_ORDER_MODE='AUTO_ALL'
    monkeypatch.setattr(recon.adapter,'reconcile_pending_management',lambda *a,**kw:{})
    monkeypatch.setattr(recon,'_recover_entry_fill',lambda *a,**kw:pytest.fail('held epoch sent to initial fill recovery'))
    monkeypatch.setattr(recon.ownership,'validate_candidate_c_position_owner',lambda *a,**kw:dict(allowed=True))
    monkeypatch.setattr(recon.order_safety,'verify_protection',lambda *a,**kw:dict(ok=True))
    machine=rsm.SymbolReversalMachine(symbol)
    state=SimpleNamespace(intent_ledger=ledger,epoch_store=epochs,notification_events=[],
        reversal_store=SimpleNamespace(get=lambda s:machine,persist=lambda *a:None))
    result=recon.reconcile_managed_position(config,client,symbol,state,2000)
    assert result.get('reason')=='owned_position_reconciled'
    assert epochs.get(record.intent_id).high_water==103
    assert epochs.get(record.intent_id).profit_lock_active
    assert epochs.get(record.intent_id).remaining_contracts==remaining
