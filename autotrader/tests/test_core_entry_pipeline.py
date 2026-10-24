import logging
import time
from types import SimpleNamespace
import pytest
import trader
import openai_analyzer
import config
from adaptive_exit_policy import production_adaptive_exit_policy,policy_sha256
from test_core_gpt_timeout_policy import timeout

SYMBOL='ETH/USDT:USDT'
class State:
    def __init__(self): self.symbols={SYMBOL:{}}
    def snapshot(self): return {'symbols':self.symbols}
    def update_symbol(self,symbol,**kw): self.symbols.setdefault(symbol,{}).update(kw)

class Exchange:
    def __init__(self,minimum=.01): self.minimum=minimum
    def price_to_precision(self,symbol,price): return f"{price:.2f}"
    def market(self,symbol):
        return dict(id='ETH-USDT-SWAP',contractSize=.1,precision={'amount':.01,'price':.01},limits={'amount':{'min':self.minimum}})

class Client:
    def __init__(self,c):
        self.cfg=c; self.symbol=SYMBOL; self.exchange=Exchange();self.orders=[]
        self.position=None;self.protection=[];self.price=100; self.response='filled';self.queries=[]
    def ensure_markets_loaded(self): pass
    def ensure_leverage(self): pass
    def contract_size(self): return .1
    def fetch_last_price(self): return self.price
    def fetch_usdt_equity(self): return 1000
    def fetch_position(self): return self.position
    def fetch_pending_protection_orders(self): return self.protection
    def fetch_order_status_by_client_id(self,cid):
        self.queries.append(cid)
        return self.orders[-1] if self.orders else None
    def fetch_entry_order_fills(self,order_id):
        row=next((r for r in self.orders if r.get('id')==order_id),None)
        if not row or not row.get('filled'):return []
        return [dict(id='fill-proof-1',order=order_id,symbol=SYMBOL,side=row['side'],amount=row['filled'],price=row['average'],timestamp=row['lastTradeTimestamp'])]
    def create_position_with_sl_tp(self,side,amount,sl,tp,**kwargs):
        now=int(time.time()*1000)
        row=dict(timestamp=now-10,lastTradeTimestamp=now,id='exchange-123',symbol=SYMBOL,side='buy' if side=='long' else 'sell',
            clientOrderId=kwargs.get('client_order_id'),status='closed',filled=amount/.1,remaining=0,average=self.price)
        if self.response=='pending': row.update(status='open',filled=0,remaining=amount/.1)
        self.orders.append(row)
        if row['filled']:
            self.position=dict(side=side,contracts=amount/.1,entry_price=self.price,position_id='lifecycle-123',entry_timestamp_ms=now,last_trade_id='fill-proof-1')
            self.protection=[dict(instId='ETH-USDT-SWAP',side='sell' if side=='long' else 'buy',state='live',reduceOnly='true',
                sz=str(amount/.1),slTriggerPx=str(sl),tpTriggerPx=str(tp),algoId='protect-123',
                algoClOrdId=kwargs.get('attach_algo_cl_ord_id'))]
        if self.response=='ambiguous':
            raise trader.okx_client.UnknownOrderStateError('offline lost acknowledgement')
        return row


def setup(tmp_path,monkeypatch,side,verdict='approved'):
    c=config.UserConfig(str(tmp_path));c.logger=logging.getLogger('pipeline-test')
    c.CORE_UNIFIED_MODE='OFF';c.EXECUTION_MODE='LIVE';c.GPT_ENTRY_GATE_ENABLED=True
    c.CORE_GPT_ENTRY_TIMEOUT_BYPASS=True;c.OPENAI_API_KEY='offline-placeholder'
    c.ADAPTIVE_EXIT_MODE='LIVE_BOUNDED';c.CORE_ORDER_MODE='FIXED_MARGIN_AUTO_EXIT';c.CORE_EXIT_MODE='AUTO'
    c.ADAPTIVE_EXIT_APPROVED_POLICY_HASH=policy_sha256(production_adaptive_exit_policy())
    c.LEVERAGE=5;c.POSITION_FIXED_USDT=200;c.POSITION_SIZE_MODE='FIXED';c.MAX_DAILY_LOSS_PCT=5
    c._core_loss_guards={SYMBOL:SimpleNamespace(allow_new_entry=lambda e:True)}
    client=Client(c);state=State()
    proposal=trader._core_adaptive_live_entry_decision(c,symbol=SYMBOL,
        legacy_order_args=(side,10,96 if side=='long' else 104,108 if side=='long' else 92),
        entry_price=100,equity=1000,market_features=dict(atr=2,structural_support=93,
        structural_resistance=107,near_resistance=105,near_support=95,continuation_resistance=120,
        continuation_support=80,source_timestamps=(1000,)))
    assert not proposal['blocked']
    monkeypatch.setattr(trader,'_core_entry_overextension_gate',lambda *a:dict(allowed=True,reason='ok'))
    monkeypatch.setattr(trader,'_evaluate_ai_close_thesis_entry_gate',lambda *a,**k:{'blocked':False})
    monkeypatch.setattr(trader,'_reentry_blocked',lambda *a,**k:(False,0))
    monkeypatch.setattr(trader,'_notify_telegram',lambda *a:None)
    monkeypatch.setattr(trader,'_record_veto_shadow_gate_outcome',lambda *a,**k:None)
    raw=timeout() if verdict=='timeout' else dict(decision=verdict if verdict!='approved' else 'approve_now',confidence=.9,error_reason=None)
    monkeypatch.setattr(openai_analyzer,'verify',lambda *a,**k:dict(raw))
    now=time.time()
    decision={'action':side,'confidence':.8,'_approval_started_at':now,'_bar_closed_at':now}
    return c,state,client,proposal,decision,raw


def run(c,state,client,p,d):
    side,amount,sl,tp=p['order_args']
    trader._handle_new_entry(c,state,client,SYMBOL,side,d,'decision-123','entry',[], '',None,
        100,amount,sl,tp,closed_dfs={},adaptive_plan=p['plan'],adaptive_context=p['context'])

@pytest.mark.parametrize('side',['long','short'])
@pytest.mark.parametrize('verdict',['approved','timeout'])
def test_valid_approval_or_timeout_reaches_actual_order_and_protection(tmp_path,monkeypatch,side,verdict):
    c,s,client,p,d,raw=setup(tmp_path,monkeypatch,side,verdict)
    run(c,s,client,p,d)
    assert len(client.orders)==1, s.symbols[SYMBOL].get('last_entry_attempt',{}).get('reason')
    assert client.position['side']==side and client.protection
    assert s.symbols[SYMBOL]['last_entry_attempt']['status']=='FILLED'
    assert s.symbols[SYMBOL]['last_entry_attempt']['decision_id']=='decision-123'

@pytest.mark.parametrize('side',['long','short'])
@pytest.mark.parametrize('verdict',['wait','reject','ERROR'])
def test_blocked_gpt_results_do_not_submit(tmp_path,monkeypatch,side,verdict):
    c,s,client,p,d,_=setup(tmp_path,monkeypatch,side,verdict)
    run(c,s,client,p,d)
    assert not client.orders and client.position is None

@pytest.mark.parametrize('side',['long','short'])
@pytest.mark.parametrize('verdict',['approved','timeout'])
@pytest.mark.parametrize('fault',['expired','minimum','daily_loss','chase','drift'])
def test_final_local_block_stays_specific_and_never_becomes_order_failure(tmp_path,monkeypatch,side,verdict,fault):
    c,s,client,p,d,_=setup(tmp_path,monkeypatch,side,verdict)
    expected={'expired':'entry_signal_or_approval_expired','minimum':'exchange_minimum_exceeds_risk_budget',
        'daily_loss':'daily_loss_guard','chase':'entry_late_exhaustion_no_pullback','drift':'post_gpt_price_drift'}[fault]
    if fault=='expired': d['_approval_started_at']-=181
    if fault=='minimum': client.exchange.minimum=10000
    if fault=='daily_loss': c._core_loss_guards[SYMBOL].allow_new_entry=lambda e:False
    if fault=='chase': monkeypatch.setattr(trader,'_core_entry_overextension_gate',lambda *a:dict(allowed=False,reason=expected))
    if fault=='drift': client.price=100.3
    run(c,s,client,p,d)
    attempt=s.symbols[SYMBOL]['last_entry_attempt']
    assert not client.orders
    assert attempt['status']=='LOCAL_BLOCKED'
    assert attempt['reason']==expected
    assert attempt['decision_id']=='decision-123'

@pytest.mark.parametrize('side',['long','short'])
def test_revised_gpt_prices_and_tp2_rr_flow_through_final_order(tmp_path,monkeypatch,side):
    c,s,client,p,d,raw=setup(tmp_path,monkeypatch,side)
    sign=1 if side=='long' else -1
    raw.update(exit_plan_decision='revise',exit_plan={'stop_loss_price':100-sign*4,
        'take_profit_1_price':100+sign*4.4,'take_profit_2_price':100+sign*8})
    run(c,s,client,p,d)
    assert len(client.orders)==1, s.symbols[SYMBOL].get('last_entry_attempt',{}).get('reason')
    assert float(client.protection[0]['slTriggerPx'])==100-sign*4
    assert float(client.protection[0]['tpTriggerPx'])==100+sign*8

@pytest.mark.parametrize('side',['long','short'])
def test_timeout_never_uses_fabricated_gpt_plan(tmp_path,monkeypatch,side):
    c,s,client,p,d,raw=setup(tmp_path,monkeypatch,side,'timeout')
    raw.update(exit_plan_decision='revise',exit_plan={'stop_loss_price':99,'take_profit_1_price':102,'take_profit_2_price':108})
    run(c,s,client,p,d)
    assert len(client.orders)==1, s.symbols[SYMBOL].get('last_entry_attempt',{}).get('reason')
    assert float(client.protection[0]['slTriggerPx'])==p['order_args'][2]
    assert float(client.protection[0]['tpTriggerPx'])==p['order_args'][3]


def test_ambiguous_exchange_ack_is_queried_and_not_resubmitted(tmp_path,monkeypatch):
    c,s,client,p,d,_=setup(tmp_path,monkeypatch,'short')
    client.response='ambiguous'
    run(c,s,client,p,d)
    assert client.queries
    assert s.symbols[SYMBOL]['last_entry_attempt']['status']=='FILLED'
    run(c,s,client,p,d)
    assert len(client.orders)==1, s.symbols[SYMBOL].get('last_entry_attempt',{}).get('reason')


def test_accepted_pending_order_is_not_reported_failed_or_filled(tmp_path,monkeypatch):
    c,s,client,p,d,_=setup(tmp_path,monkeypatch,'short')
    client.response='pending'
    run(c,s,client,p,d)
    assert s.symbols[SYMBOL]['last_entry_attempt']['status']=='ORDER_PENDING'
    run(c,s,client,p,d)
    assert len(client.orders)==1, s.symbols[SYMBOL].get('last_entry_attempt',{}).get('reason')


def test_client_id_query_never_fabricates_requested_identity(monkeypatch):
    client=object.__new__(trader.okx_client.OkxClient)
    client.symbol=SYMBOL
    client.exchange=SimpleNamespace(fetch_order=lambda *a,**k:dict(id='other-order',clientOrderId='foreign-id',
        symbol=SYMBOL,side='sell',status='closed',filled=1,remaining=0))
    result=client.fetch_order_status_by_client_id('our-id')
    assert result['clientOrderId']=='foreign-id'
    assert result['symbol']==SYMBOL and result['side']=='sell'


def test_pending_original_order_blocks_new_decision_without_new_submit(tmp_path,monkeypatch):
    c,s,client,p,d,_=setup(tmp_path,monkeypatch,'short')
    client.response='pending'
    run(c,s,client,p,d)
    new=dict(d)
    side,amount,sl,tp=p['order_args']
    trader._handle_new_entry(c,s,client,SYMBOL,side,new,'new-decision','entry',[], '',None,
        100,amount,sl,tp,closed_dfs={},adaptive_plan=p['plan'],adaptive_context=p['context'])
    assert len(client.orders)==1
    assert s.symbols[SYMBOL]['last_entry_attempt']['status']=='LOCAL_BLOCKED'
    assert s.symbols[SYMBOL]['last_entry_attempt']['reason']=='unresolved_prior_entry_order'


def test_pending_fill_recovers_without_new_gpt_or_new_order(tmp_path,monkeypatch):
    c,s,client,p,d,_=setup(tmp_path,monkeypatch,'short')
    client.response='pending'
    run(c,s,client,p,d)
    order=client.orders[0]
    order.update(status='closed',filled=order['remaining'],remaining=0)
    client.position=dict(side='short',contracts=order['filled'],entry_price=100,position_id='lifecycle-123',entry_timestamp_ms=order['lastTradeTimestamp'],last_trade_id='fill-proof-1')
    client.protection=[dict(instId='ETH-USDT-SWAP',side='buy',state='live',reduceOnly='true',sz=str(order['filled']),
        slTriggerPx=str(p['order_args'][2]),tpTriggerPx=str(p['order_args'][3]),algoId='protect-123',algoClOrdId='ca'+order['clientOrderId'][2:])]
    def forbidden(*a,**k): raise AssertionError('reconciliation must not call AI')
    monkeypatch.setattr(openai_analyzer,'verify',forbidden)
    reconcile=getattr(trader,'_reconcile_pending_core_entry',None)
    assert callable(reconcile)
    assert reconcile(c,s,client,SYMBOL)
    assert len(client.orders)==1
    assert s.symbols[SYMBOL]['last_entry_attempt']['status']=='FILLED'
    assert s.symbols[SYMBOL]['last_entry_attempt']['decision_id']=='decision-123'


def test_disabled_core_gpt_gate_cannot_silently_place_unapproved_entry(tmp_path,monkeypatch):
    c,s,client,p,d,_=setup(tmp_path,monkeypatch,'long')
    c.GPT_ENTRY_GATE_ENABLED=False
    run(c,s,client,p,d)
    assert not client.orders
    assert s.symbols[SYMBOL]['last_entry_attempt']['status']=='GPT_ERROR'


@pytest.mark.parametrize('verdict',['wait','reject','ERROR'])
def test_gpt_blocked_dashboard_preserves_decision_and_raw_result(tmp_path,monkeypatch,verdict):
    c,s,client,p,d,_=setup(tmp_path,monkeypatch,'short',verdict)
    run(c,s,client,p,d)
    row=s.symbols[SYMBOL]['last_entry_attempt']
    assert row['decision_id']=='decision-123'
    assert row['gpt_raw_result']==verdict
    assert row['gate_processing'] in ('GPT_WAIT','GPT_REJECT','GPT_ERROR')


def test_pending_exchange_state_never_journals_position_without_fill_proof(tmp_path,monkeypatch):
    import json
    c,s,client,p,d,_=setup(tmp_path,monkeypatch,'short')
    client.response='pending'
    run(c,s,client,p,d)
    order=client.orders[0]
    contracts=order['remaining']
    client.position=dict(side='short',contracts=contracts,entry_price=100,position_id='lifecycle-123',entry_timestamp_ms=order['lastTradeTimestamp'],last_trade_id='fill-proof-1')
    client.protection=[dict(instId='ETH-USDT-SWAP',side='buy',state='live',reduceOnly='true',sz=str(contracts),
        slTriggerPx=str(p['order_args'][2]),tpTriggerPx=str(p['order_args'][3]),algoId='protect-123',algoClOrdId='ca'+order['clientOrderId'][2:])]
    monkeypatch.setattr(trader.order_safety,'notify_critical',lambda *a:None)
    assert not trader._reconcile_pending_core_entry(c,s,client,SYMBOL)
    assert not trader._reconcile_pending_core_entry(c,s,client,SYMBOL)
    journal=tmp_path/'trades_log.jsonl'
    assert not journal.exists(), 'a position alone is not original order fill proof'
    order.update(status='closed',filled=contracts,remaining=0)
    assert trader._reconcile_pending_core_entry(c,s,client,SYMBOL)
    rows=[json.loads(line) for line in journal.read_text().splitlines()]
    assert len(rows)==1
    assert rows[0]['amount']==pytest.approx(contracts*.1)
    assert rows[0]['execution_id']=='core-entry:decision-123'


def test_protection_lookup_exception_freezes_account_and_keeps_receipt_pending(tmp_path,monkeypatch):
    import core_entry_events
    c,s,client,p,d,_=setup(tmp_path,monkeypatch,'long')
    def failed(*a,**k): raise RuntimeError('offline unavailable protection read')
    monkeypatch.setattr(trader.order_safety,'verify_protection',failed)
    run(c,s,client,p,d)
    assert len(client.orders)==1
    assert trader.core_kill_switch.is_active(c.user_dir)
    assert core_entry_events.pending_order(c.user_dir,SYMBOL) is not None
    assert s.symbols[SYMBOL]['last_entry_attempt']['status']=='ORDER_PENDING'


def test_unbound_or_changed_lifecycle_never_authorizes_emergency_close(tmp_path,monkeypatch):
    import core_entry_events,core_entry_orders
    c,s,client,p,d,_=setup(tmp_path,monkeypatch,'short')
    client.response='pending';run(c,s,client,p,d)
    receipt=core_entry_events.order_receipt(c.user_dir,'decision-123')
    order=client.orders[0];order.update(status='closed',filled=order['remaining'],remaining=0)
    client.position=dict(side='short',contracts=order['filled'],entry_price=100,position_id='another-lifecycle',entry_timestamp_ms=2000000)
    closes=[];client.close_position=lambda *a,**k:closes.append(k)
    result=core_entry_orders.reconcile(c,client,receipt)
    assert not trader._recover_unprotected_core_entry(c,client,SYMBOL,result,client.position)
    assert not closes
    core_entry_events.update_order(c.user_dir,'decision-123','ORDER_PENDING',bound_position_identity='original-lifecycle')
    assert not trader._recover_unprotected_core_entry(c,client,SYMBOL,result,client.position)
    assert not closes and trader.core_kill_switch.is_active(c.user_dir)


def test_state_cache_failure_does_not_drop_durable_gate_and_fill_events(tmp_path,monkeypatch):
    import core_entry_events
    c,s,client,p,d,_=setup(tmp_path,monkeypatch,'long')
    s.update_symbol=lambda *a,**k:(_ for _ in ()).throw(RuntimeError('offline cache write failure'))
    # Test the observation boundary alone; exchange completion does not depend on it.
    trader._set_core_entry_outcome(c,s,SYMBOL,d,'decision-123','GPT_APPROVED','ok')
    assert core_entry_events.recent(c.user_dir)[0]['status']=='GPT_APPROVED'


def test_gate_order_notification_failures_cannot_change_a_protected_fill(tmp_path,monkeypatch):
    import core_entry_events,telegram_notify
    c,s,client,p,d,_=setup(tmp_path,monkeypatch,'short','timeout')
    c.TELEGRAM_BOT_TOKEN='offline';c.TELEGRAM_CHAT_ID='offline'
    monkeypatch.setattr(core_entry_events,'kick',lambda c:None)
    monkeypatch.setattr(telegram_notify,'send',lambda *a:(_ for _ in ()).throw(TimeoutError('offline')))
    run(c,s,client,p,d)
    assert s.symbols[SYMBOL]['last_entry_attempt']['status']=='FILLED'
    events=core_entry_events.recent(c.user_dir)
    assert {r['status'] for r in events}=={'TIMEOUT_BYPASS','ORDER_SUBMITTED','FILLED'}
    core_entry_events.deliver_pending(c)
    assert all(r['notification_status']=='DELIVERY_UNKNOWN' for r in core_entry_events.recent(c.user_dir))
    assert client.position is not None and len(client.orders)==1


@pytest.mark.parametrize('verdict',['approved','timeout'])
def test_final_eligibility_failure_preserves_existing_long_before_reversal(tmp_path,monkeypatch,verdict):
    c,s,client,p,d,_=setup(tmp_path,monkeypatch,'short',verdict)
    old=dict(side='long',contracts=3,entry_price=100,position_id='old-long',entry_timestamp_ms=500000)
    client.position=old
    closes=[]
    monkeypatch.setattr(trader,'_execute_close',lambda *a,**k:closes.append(1))
    d['_approval_started_at']-=181
    side,amount,sl,tp=p['order_args']
    trader._handle_new_entry(c,s,client,SYMBOL,side,d,'decision-123','entry',[],'',old,100,amount,sl,tp,
        reversal_position=old,closed_dfs={},adaptive_plan=p['plan'],adaptive_context=p['context'])
    assert not closes and not client.orders and client.position==old
    assert s.symbols[SYMBOL]['last_entry_attempt']['reason']=='entry_signal_or_approval_expired'


@pytest.mark.parametrize('side',['long','short'])
@pytest.mark.parametrize('verdict',['approved','timeout'])
def test_eligible_reversal_confirms_flat_then_reaches_replacement_order(tmp_path,monkeypatch,side,verdict):
    c,s,client,p,d,_=setup(tmp_path,monkeypatch,side,verdict)
    old=dict(side='short' if side=='long' else 'long',contracts=3,entry_price=100,
        position_id='old-position',entry_timestamp_ms=500000)
    client.position=old;calls=[]
    def close(*a,**k):
        calls.append('close_confirmed');client.position=None;return True
    monkeypatch.setattr(trader,'_execute_close',close)
    _,amount,sl,tp=p['order_args']
    trader._handle_new_entry(c,s,client,SYMBOL,side,d,'decision-123','entry',[],'',old,100,amount,sl,tp,
        reversal_position=old,closed_dfs={},adaptive_plan=p['plan'],adaptive_context=p['context'])
    assert calls==['close_confirmed']
    assert len(client.orders)==1 and client.position['side']==side
    assert s.symbols[SYMBOL]['last_entry_attempt']['status']=='FILLED'


def test_exchange_rejection_exposes_code_and_does_not_look_filled(tmp_path,monkeypatch):
    import ccxt
    c,s,client,p,d,_=setup(tmp_path,monkeypatch,'long')
    def reject(*a,**k): raise ccxt.ExchangeError('{"code":"51000","msg":"offline rejection"}')
    client.create_position_with_sl_tp=reject
    run(c,s,client,p,d)
    row=s.symbols[SYMBOL]['last_entry_attempt']
    assert row['status']=='ORDER_FAILED' and row['exchange_code']=='51000'
    assert not client.position and not client.orders


def test_bound_lifecycle_emergency_lost_ack_is_queried_and_never_closed_twice(tmp_path,monkeypatch):
    import core_entry_events,ccxt
    c,s,client,p,d,_=setup(tmp_path,monkeypatch,'short')
    client.response='pending';run(c,s,client,p,d)
    order=client.orders[0];order.update(status='closed',filled=order['remaining'],remaining=0)
    client.position=dict(side='short',contracts=order['filled'],entry_price=100,
        position_id='original-lifecycle',entry_timestamp_ms=1000000)
    core_entry_events.update_order(c.user_dir,'decision-123','ORDER_PENDING',
        bound_position_identity=trader.reduce_v2_state.position_identity(client.position))
    closes=[];close_order={}
    def close(position,client_order_id):
        closes.append(client_order_id)
        close_order.update(id='safety-close',symbol=SYMBOL,side='buy',status='closed',
            clientOrderId=client_order_id,filled=position['contracts'],remaining=0)
        client.position=None
        raise ccxt.RequestTimeout('offline lost close ack')
    client.close_position=close
    def query(cid):
        client.queries.append(cid)
        return close_order if cid.startswith('ce') else order
    client.fetch_order_status_by_client_id=query
    assert not trader._reconcile_pending_core_entry(c,s,client,SYMBOL)
    assert len(closes)==1
    assert core_entry_events.order_receipt(c.user_dir,'decision-123')['status']=='ORDER_FAILED'
    assert not trader._reconcile_pending_core_entry(c,s,client,SYMBOL)
    assert len(closes)==1 and trader.core_kill_switch.is_active(c.user_dir)


def test_pending_receipt_cannot_rebind_a_changed_position_lifecycle(tmp_path,monkeypatch):
    import core_entry_events
    c,s,client,p,d,_=setup(tmp_path,monkeypatch,'short')
    client.response='pending';run(c,s,client,p,d)
    order=client.orders[0];contracts=order['remaining']/2
    order.update(filled=contracts,remaining=contracts)
    client.position=dict(side='short',contracts=contracts,entry_price=100,
        position_id='original',entry_timestamp_ms=order['lastTradeTimestamp'],last_trade_id='fill-proof-1')
    client.protection=[dict(instId='ETH-USDT-SWAP',side='buy',state='live',reduceOnly='true',sz=str(contracts),
        slTriggerPx=str(p['order_args'][2]),tpTriggerPx=str(p['order_args'][3]),algoId='protect-123',algoClOrdId='ca'+order['clientOrderId'][2:])]
    assert not trader._reconcile_pending_core_entry(c,s,client,SYMBOL)
    before=core_entry_events.order_receipt(c.user_dir,'decision-123')['payload']['bound_position_identity']
    client.position['entry_timestamp_ms']=2000000
    assert not trader._reconcile_pending_core_entry(c,s,client,SYMBOL)
    after=core_entry_events.order_receipt(c.user_dir,'decision-123')['payload']['bound_position_identity']
    assert before==after
    assert s.symbols[SYMBOL]['last_entry_attempt']['reason']=='entry_position_lifecycle_changed'

@pytest.mark.parametrize('side',['long','short'])
@pytest.mark.parametrize('verdict',['approved','timeout','wait','reject','ERROR'])
def test_gate_notifications_include_original_plan_and_compatible_raw_fields(tmp_path,monkeypatch,side,verdict):
    import core_entry_events,telegram_notify
    c,s,client,p,d,raw=setup(tmp_path,monkeypatch,side,verdict)
    run(c,s,client,p,d)
    gate=next(r for r in core_entry_events.recent(c.user_dir) if r['status'].startswith('GPT_') or r['status']=='TIMEOUT_BYPASS')
    assert gate['quantity_coin']==p['order_args'][1]
    assert gate['sl_price']==p['order_args'][2] and gate['tp_price']==p['order_args'][3]
    assert gate['leverage']==c.LEVERAGE
    assert gate['gpt_decision']==gate['gpt_raw_result']==raw['decision']
    assert gate['gpt_confidence']==raw.get('confidence')
    text=telegram_notify.format_core_entry_event(gate)
    assert '수량=' in text and '레버리지=' in text and 'SL=' in text and 'TP=' in text

@pytest.mark.parametrize('fault',['none','foreign_trade','foreign_lifecycle','missing_fill','partial','changed_before_close'])
def test_unprotected_new_fill_recovers_only_with_original_exchange_trade_proof(tmp_path,monkeypatch,fault):
    import core_entry_events
    c,s,client,p,d,_=setup(tmp_path,monkeypatch,'long')
    original=client.create_position_with_sl_tp
    close_calls=[]
    def create(*a,**k):
        order=original(*a,**k)
        now=int(time.time()*1000)
        order.update(timestamp=now-10,lastTradeTimestamp=now)
        client.position.update(entry_timestamp_ms=order['lastTradeTimestamp'],last_trade_id='fill-proof-1')
        client.protection=[]
        if fault=='foreign_trade':client.position['last_trade_id']='foreign-fill'
        if fault=='foreign_lifecycle':client.position['entry_timestamp_ms']=now+10000
        if fault=='partial':order.update(status='open',remaining=1)
        return order
    client.create_position_with_sl_tp=create
    def fills(order_id):
        if fault=='missing_fill':return []
        return [dict(id='fill-proof-1',order='exchange-123',symbol=SYMBOL,side='buy',amount=client.orders[0]['filled'],price=100,timestamp=client.orders[0]['lastTradeTimestamp'])]
    client.fetch_entry_order_fills=fills
    def close(position,**kwargs):
        close_calls.append(kwargs['client_order_id'])
        qty=position['contracts'];client.position=None
        client.orders.append(dict(id='emergency-1',clientOrderId=kwargs['client_order_id'],symbol=SYMBOL,side='sell',status='closed',filled=qty,remaining=0,average=100))
    client.close_position=close
    if fault=='changed_before_close':
        original_fetch=client.fetch_position
        reads=[0]
        def fetch():
            reads[0]+=1
            if reads[0]>=4 and client.position:client.position['last_trade_id']='foreign-after-proof'
            return original_fetch()
        client.fetch_position=fetch
    run(c,s,client,p,d)
    receipt=core_entry_events.order_receipt(c.user_dir,'decision-123')
    if fault=='none':
        assert len(close_calls)==1
        assert client.position is None and receipt['status']=='ORDER_FAILED'
        assert not (tmp_path/'trades_log.jsonl').exists()
        trader._reconcile_pending_core_entry(c,s,client,SYMBOL)
        assert len(close_calls)==1
    else:
        assert not close_calls
        assert client.position is not None and receipt['status']=='ORDER_PENDING'
        assert trader.core_kill_switch.is_active(c.user_dir)

@pytest.mark.parametrize('terminal',[False,True])
def test_old_order_receipt_cannot_adopt_foreign_protected_position(tmp_path,monkeypatch,terminal):
    import core_entry_events
    c,s,client,p,d,_=setup(tmp_path,monkeypatch,'short')
    client.response='pending';run(c,s,client,p,d)
    order=client.orders[0];order.update(filled=order['remaining'],remaining=0 if terminal else 1,status='closed' if terminal else 'open',timestamp=int(time.time()*1000)-20000)
    client.position=dict(side='short',contracts=order['filled'],entry_price=100,position_id='foreign',entry_timestamp_ms=int(time.time()*1000),last_trade_id='foreign-fill')
    client.protection=[dict(instId='ETH-USDT-SWAP',side='buy',state='live',reduceOnly='true',sz=str(order['filled']),slTriggerPx=str(p['order_args'][2]),tpTriggerPx=str(p['order_args'][3]),algoId='stale-protection',algoClOrdId='ca'+order['clientOrderId'][2:])]
    client.fetch_entry_order_fills=lambda oid:[dict(id='original-fill',order=oid,symbol=SYMBOL,side='sell',amount=order['filled'],price=100,timestamp=order['timestamp']+1)]
    trader._reconcile_pending_core_entry(c,s,client,SYMBOL)
    receipt=core_entry_events.order_receipt(c.user_dir,'decision-123')
    assert receipt['status']=='ORDER_PENDING'
    assert not receipt['payload'].get('bound_position_identity')
    assert not (tmp_path/'trades_log.jsonl').exists()
    assert client.position['position_id']=='foreign'


def test_crash_before_terminal_event_retains_recoverable_receipt_and_one_open(tmp_path,monkeypatch):
    import core_entry_events
    c,s,client,p,d,_=setup(tmp_path,monkeypatch,'long')
    original=trader._set_core_entry_outcome
    class ProcessDeath(BaseException):pass
    def crash(*a,**k):
        if a[5]=='FILLED':raise ProcessDeath
        return original(*a,**k)
    monkeypatch.setattr(trader,'_set_core_entry_outcome',crash)
    with pytest.raises(ProcessDeath):run(c,s,client,p,d)
    assert core_entry_events.pending_order(c.user_dir,SYMBOL) is not None
    monkeypatch.setattr(trader,'_set_core_entry_outcome',original)
    trader._reconcile_pending_core_entry(c,s,client,SYMBOL)
    events=core_entry_events.recent(c.user_dir)
    assert sum(r['status']=='FILLED' for r in events)==1
    assert core_entry_events.order_receipt(c.user_dir,'decision-123')['status']=='FILLED'
    import json
    assert len([r for r in (tmp_path/'trades_log.jsonl').read_text().splitlines() if json.loads(r).get('type')=='open'])==1

@pytest.mark.parametrize('foreign',[False,True])
def test_partial_unprotected_cancel_race_requires_complete_original_trade_proof(tmp_path,monkeypatch,foreign):
    import core_entry_events
    c,s,client,p,d,_=setup(tmp_path,monkeypatch,'long')
    original=client.create_position_with_sl_tp
    def create(*a,**k):
        row=original(*a,**k);half=row['filled']/2
        row.update(status='open',filled=half,remaining=half)
        client.position['contracts']=half;client.protection=[]
        return row
    client.create_position_with_sl_tp=create
    def cancel(order_id,symbol):
        row=client.orders[0];row.update(status='closed',filled=row['filled']+row['remaining'],remaining=0)
        client.position.update(contracts=row['filled'],last_trade_id='foreign-fill' if foreign else 'fill-proof-1')
    client.exchange.cancel_order=cancel
    closes=[]
    def close(position,client_order_id):
        closes.append(client_order_id);client.position=None
        client.orders.append(dict(id='safety-close',clientOrderId=client_order_id,symbol=SYMBOL,side='sell',status='closed',filled=position['contracts'],remaining=0))
    client.close_position=close
    run(c,s,client,p,d)
    receipt=core_entry_events.order_receipt(c.user_dir,'decision-123')
    if foreign:
        assert not closes and client.position is not None and receipt['status']=='ORDER_PENDING'
    else:
        assert len(closes)==1 and client.position is None and receipt['status']=='ORDER_FAILED'
        trader._reconcile_pending_core_entry(c,s,client,SYMBOL)
        assert len(closes)==1
