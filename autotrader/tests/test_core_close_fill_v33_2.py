"""Strict new CORE close fill attribution regression."""
import contextlib,json,time
from types import SimpleNamespace
import pytest
import core_close_fill_evidence as proof
import trader,trade_log
from state import TraderState

S='XRP/USDT:USDT'
T=1792003000000
P=dict(side='short',entry_price=1.34,contracts=3.,position_id='p1',
       entry_timestamp_ms=12345,mark_price=1.36,_approval_started_at=time.time(),
       _gemini_assessment='invalidated')
ORDER=dict(id='O1',symbol=S,status='closed',side='buy',filled=3.,remaining=0.,
           timestamp=T,info={'ordId':'O1'})
def fill(id,qty,price,when=T+1000,order='O1',side='buy'):
    return dict(id=id,order=order,symbol=S,side=side,amount=qty,price=price,
                timestamp=when,info={'ordId':order,'tradeId':id})
class Client:
    symbol=S
    def __init__(self,rows=None,order=None,receipt=None):
        self.rows=rows if rows is not None else [fill('F1',1.,1.350),fill('F2',2.,1.353)]
        self.order=ORDER if order is None else order
        self.receipt=receipt if receipt is not None else dict(id='O1',symbol=S)
        self.closed=False
        self.exchange=SimpleNamespace(fetch_order=lambda order_id,symbol:self.order)
    def fetch_trades_for_order(self,order_id,since_ms,limit=100):return list(self.rows)
    def fetch_position(self):return None if self.closed else dict(P)
    def close_position(self,*a,**k):self.closed=True;return self.receipt

def evidence(client=None):
    client=client or Client()
    return proof.verified_close_fills(client,P,client.receipt,start_ms=T)

def test_verified_multi_fill_vwap_and_duplicate():
    x=evidence(Client(rows=[fill('F1',1.,1.35),fill('F1',1.,1.35),
                            fill('F2',2.,1.353)]))
    assert x['order_id']=='O1' and x['trade_ids']==['F1','F2']
    assert x['vwap']==pytest.approx((1.35+2*1.353)/3)
    assert x['filled_contracts']==3.
    assert x['price_basis']=='verified_order_fills'

@pytest.mark.parametrize('scenario',[
 'wrong_fill_order','wrong_fill_side','missing_qty','bad_receipt_order',
 'order_open','wrong_order_side','wrong_order_id','no_rows','stale_timestamp',
 'duplicate_conflict','wrong_symbol','partial_filled','wrong_receipt_raw_id',
])
def test_unproven_fills_never_confer_price_authority(scenario):
    c=Client()
    if scenario=='wrong_fill_order':c.rows=[fill('F1',1.,1.35,order='O2'),fill('F2',2.,1.353)]
    elif scenario=='wrong_fill_side':c.rows=[fill('F1',1.,1.35,side='sell'),fill('F2',2.,1.353)]
    elif scenario=='missing_qty':c.rows=[fill('F1',1.,1.35)]
    elif scenario=='bad_receipt_order':c.receipt={'id':'O2'}
    elif scenario=='order_open':c.order=dict(ORDER,status='open')
    elif scenario=='wrong_order_side':c.order=dict(ORDER,side='sell')
    elif scenario=='wrong_order_id':c.order=dict(ORDER,id='O2',info={'ordId':'O2'})
    elif scenario=='no_rows':c.rows=[]
    elif scenario=='stale_timestamp':c.rows=[fill('F1',1.,1.35,when=T-20000),fill('F2',2.,1.353)]
    elif scenario=='duplicate_conflict':c.rows=[fill('F1',1.,1.35),fill('F1',1.1,1.35),fill('F2',2.,1.353)]
    elif scenario=='wrong_symbol':c.rows=[dict(fill('F1',1.,1.35),symbol='BTC/USDT:USDT'),fill('F2',2.,1.353)]
    elif scenario=='partial_filled':c.order=dict(ORDER,filled=2.)
    elif scenario=='wrong_receipt_raw_id':c.receipt={'id':'O1','info':{'ordId':'O2'}}
    assert evidence(c) is None

def test_missing_receipt_is_never_proven():
    assert proof.verified_close_fills(Client(),P,None,start_ms=T) is None

def test_journal_persists_fill_ids_without_pnl_change(tmp_path):
    x=evidence()
    trade_log.record_close(str(tmp_path),S,'short',1.34,3.,-2.,'position_ai_close_all',False,
                           fee=.3,okx_net_pnl=-2.3,close_price=x['vwap'],
                           close_execution_evidence=x,strategy_group='core')
    row=json.loads((tmp_path/'trades_log.jsonl').read_text())
    assert row['close_price']==pytest.approx(x['vwap'])
    assert row['close_execution_evidence']['trade_ids']==['F1','F2']
    assert row['okx_net_pnl']==-2.3

def test_close_path_links_shadow_and_order_without_recalculating_pnl(monkeypatch,tmp_path):
    class Log:
        def info(self,*a,**k):pass
        def warning(self,*a,**k):pass
        def error(self,*a,**k):pass
        def exception(self,*a,**k):pass
    cfg=SimpleNamespace(user_dir=str(tmp_path),logger=Log(),EXECUTION_MODE='LIVE',
                        REENTRY_COOLDOWN_MINUTES=15)
    client=Client()
    trade_log._append(str(tmp_path),dict(type='open',symbol=S,side='short',price=1.34,
                                        amount=3.,sl_price=1.37,tp_price=1.30))
    monkeypatch.setattr(trader.cc_ownership,'account_order_lock',lambda *_:contextlib.nullcontext())
    monkeypatch.setattr(trader.core_unified_service,'routed_close',lambda *_:None)
    monkeypatch.setattr(trader,'_resolve_external_close_pnl',lambda *a,**k:dict(
        gross_pnl=-2.0,fee=.3,net_pnl=-2.3,source='okx_realized',
        exit_price=None,funding_fee=0.0))
    monkeypatch.setattr(trader,'_notify_telegram',lambda *a,**k:None)
    monkeypatch.setattr(trader.core_reentry_thesis,'record_ai_close',lambda *a,**k:None)
    monkeypatch.setattr(trader.core_short_downgrade,'record_close',lambda *a,**k:None)
    monkeypatch.setattr(trader.reduce_v2_state,'clear',lambda *a,**k:None)
    monkeypatch.setattr(trader.core_add_position_state,'clear',lambda *a,**k:None)
    monkeypatch.setattr(trader.time,'time',lambda:T/1000)
    monkeypatch.setattr(proof,'verified_close_fills',lambda *a,**k:dict(
      order_id='O1',trade_ids=['F1','F2'],vwap=1.352,filled_contracts=3.,
      price_basis='verified_order_fills'))
    ok=trader._execute_close(cfg,TraderState(),client,S,
                             dict(P,_approval_started_at=T/1000),'position_ai_close_all')
    assert ok
    row=trade_log.last_close(str(tmp_path),S)
    assert row['okx_net_pnl']==-2.3
    assert row['close_price']==1.352
    assert row['close_execution_evidence']['order_id']=='O1'
    assert row['execution_id']=='core-close-order:XRP/USDT:USDT:O1'
    shadow=trader.exit_reentry_shadow.recent(str(tmp_path),1)[0]
    assert shadow['exit_order_id']=='O1' and shadow['exit_trade_ids']==['F1','F2']
    assert shadow['exit_price_basis']=='verified_order_fills'


@pytest.mark.parametrize("confirmed", [True, False])
def test_core_close_terminal_receipt_fallback_is_fail_closed(monkeypatch, tmp_path, confirmed):
    class Log:
        def info(self,*a,**k):pass
        def warning(self,*a,**k):pass
        def error(self,*a,**k):pass
        def exception(self,*a,**k):pass
    cfg=SimpleNamespace(user_dir=str(tmp_path),logger=Log(),
                        EXECUTION_MODE='LIVE',REENTRY_COOLDOWN_MINUTES=15)
    client=Client()
    client.order=dict(ORDER,info={'ordId':'O1',
                                 'reduceOnly':'true' if confirmed else 'false'})
    trade_log._append(str(tmp_path),dict(type='open',symbol=S,side='short',
            price=1.34,amount=3.,sl_price=1.37,tp_price=1.30))
    monkeypatch.setattr(trader.cc_ownership,'account_order_lock',
                        lambda *_:contextlib.nullcontext())
    monkeypatch.setattr(trader.core_unified_service,'routed_close',lambda *_:None)
    monkeypatch.setattr(trader,'_resolve_external_close_pnl',
        lambda *a,**k:dict(gross_pnl=-2.,fee=.3,net_pnl=-2.3,
                          source='okx_realized',exit_price=None,funding_fee=0.))
    monkeypatch.setattr(trader,'_notify_telegram',lambda *a,**k:None)
    monkeypatch.setattr(trader.core_reentry_thesis,'record_ai_close',lambda *a,**k:None)
    monkeypatch.setattr(trader.core_short_downgrade,'record_close',lambda *a,**k:None)
    monkeypatch.setattr(trader.reduce_v2_state,'clear',lambda *a,**k:None)
    monkeypatch.setattr(trader.core_add_position_state,'clear',lambda *a,**k:None)
    monkeypatch.setattr(trader.time,'time',lambda:T/1000)
    monkeypatch.setattr(proof,'verified_close_fills',lambda *a,**k:None)
    ok=trader._execute_close(cfg,TraderState(),client,S,
        dict(P,_approval_started_at=T/1000),'position_ai_close_all')
    assert ok
    row=trade_log.last_close(str(tmp_path),S)
    assert row['okx_net_pnl']==-2.3
    assert row.get('execution_id')==(
        'core-close-order:XRP/USDT:USDT:O1' if confirmed else None)
    assert row.get('close_execution_evidence') is None
