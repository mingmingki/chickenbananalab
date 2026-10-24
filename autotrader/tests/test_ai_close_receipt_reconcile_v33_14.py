"""v33-14 exact receipt recovery, fail-closed proof and horizon evidence."""
from datetime import datetime,timedelta,timezone
from pathlib import Path
from types import SimpleNamespace
import contextlib,json
import pytest

import core_ai_close_receipt_reconcile_v33_14 as repair
import core_ai_exit_horizon_v33_14 as horizon
import core_close_fill_evidence
import trader,trade_log
from state import TraderState
from test_core_close_fill_v33_2 import Client,P,S,T

KST=timezone(timedelta(hours=9))
TSEC=T/1000
WHEN=datetime.fromtimestamp(TSEC+20,KST).isoformat()

def close_row(*,ref=True):
    r=dict(type='close',strategy_group='core',reason='position_ai_close_all',
         symbol=S,side='short',amount=3.,dry_run=False,entry_price=1.34,
         pnl=-2.,fee=.3,pnl_source='okx_realized',okx_net_pnl=-2.3,
         time=WHEN,close_price=None)
    if ref:
        r['close_order_reference']=dict(
          order_id='O1',start_ms=T,expected_contracts=3.,side='short',
          basis='owned_market_reduce_only_order_receipt')
    return r

def journal(tmp_path,rows,baseline=0):
    p=tmp_path/'trades_log.jsonl'
    p.write_text(''.join(json.dumps(row)+'\n' for row in rows))
    (tmp_path/repair.CUTOVER).write_text(json.dumps(
        dict(baseline_line_count=baseline)))
    return p

def valid_proof():
    return dict(order_id='O1',trade_ids=['F1','F2'],vwap=1.352,
        filled_contracts=3.,price_basis='verified_order_fills')

def test_exact_receipt_recorded_when_fill_proof_delayed(monkeypatch,tmp_path):
    class Log:
        def info(self,*x,**kw):pass
        def warning(self,*x,**kw):pass
        def error(self,*x,**kw):pass
        def exception(self,*x,**kw):pass
    cfg=SimpleNamespace(user_dir=str(tmp_path),logger=Log(),
        EXECUTION_MODE='LIVE',REENTRY_COOLDOWN_MINUTES=15)
    client=Client()
    trade_log._append(str(tmp_path),dict(
        type='open',symbol=S,side='short',price=1.34,amount=3.,
        sl_price=1.37,tp_price=1.30))
    monkeypatch.setattr(trader.cc_ownership,'account_order_lock',lambda *_:contextlib.nullcontext())
    monkeypatch.setattr(trader.core_unified_service,'routed_close',lambda *_:None)
    monkeypatch.setattr(trader,'_resolve_external_close_pnl',lambda *a,**kw:dict(
        gross_pnl=-2.,fee=.3,net_pnl=-2.3,
        source='okx_realized',exit_price=None,funding_fee=0.))
    monkeypatch.setattr(trader,'_notify_telegram',lambda *a,**kw:None)
    monkeypatch.setattr(trader.core_reentry_thesis,'record_ai_close',lambda *a,**kw:None)
    monkeypatch.setattr(trader.core_short_downgrade,'record_close',lambda *a,**kw:None)
    monkeypatch.setattr(trader.reduce_v2_state,'clear',lambda *a,**kw:None)
    monkeypatch.setattr(trader.core_add_position_state,'clear',lambda *a,**kw:None)
    monkeypatch.setattr(trader.time,'time',lambda:TSEC)
    monkeypatch.setattr(core_close_fill_evidence,'verified_close_fills',lambda *a,**kw:None)
    assert trader._execute_close(cfg,TraderState(),client,S,
                dict(P,_approval_started_at=TSEC),'position_ai_close_all')
    row=trade_log.last_close(str(tmp_path),S)
    assert row['close_order_reference']['order_id']=='O1'
    assert row['close_order_reference']['start_ms']==T
    assert row['close_order_reference']['expected_contracts']==3.
    assert 'close_execution_evidence' not in row
    assert row['okx_net_pnl']==-2.3

def test_read_only_reconcile_exact_order_after_delayed_fill(tmp_path,monkeypatch):
    r=close_row()
    p=journal(tmp_path,[r])
    original=p.read_bytes()
    calls=[]
    def match(client,position,receipt,*,start_ms):
        calls.append((position,receipt,start_ms))
        return valid_proof()
    monkeypatch.setattr(core_close_fill_evidence,'verified_close_fills',match)
    factory=lambda sym:SimpleNamespace(symbol=sym)
    a=repair.reconcile(tmp_path,now=TSEC+60,client_factory=factory)
    assert a['status']=='KNOWN' and a['attempted']==1
    assert a['states']['VERIFIED']==1
    assert calls[0][1]['id']=='O1' and calls[0][2]==T
    s=repair.summary(tmp_path)
    assert s['post_cutover_exact_fill_proofs']==1
    assert s['post_cutover_owned_receipt_pending']==0
    assert p.read_bytes()==original
    b=repair.reconcile(tmp_path,now=TSEC+420,client_factory=factory)
    assert b['attempted']==0 and len(calls)==1

def test_delayed_proof_is_bounded_and_never_claimed_verified(tmp_path,monkeypatch):
    journal(tmp_path,[close_row()])
    monkeypatch.setattr(core_close_fill_evidence,'verified_close_fills',
                        lambda *a,**kw:None)
    fake=lambda symbol:SimpleNamespace(symbol=symbol)
    first=repair.reconcile(tmp_path,now=TSEC+60,client_factory=fake)
    assert first['states']['RETRY']==1
    assert repair.summary(tmp_path)['post_cutover_exact_fill_proofs']==0
    early=repair.reconcile(tmp_path,now=TSEC+120,client_factory=fake)
    assert early['attempted']==0
    later=repair.reconcile(tmp_path,now=TSEC+700,client_factory=fake)
    assert later['attempted']==1
    assert repair.summary(tmp_path)['post_cutover_owned_receipt_pending']==1

@pytest.mark.parametrize('change',[
    lambda x:x['close_order_reference'].update(order_id=''),
    lambda x:x['close_order_reference'].update(expected_contracts=2.),
    lambda x:x['close_order_reference'].update(start_ms=1),
    lambda x:x['close_order_reference'].update(side='long'),
    lambda x:x['close_order_reference'].update(basis='guessed_time_match'),
    lambda x:x.update(reason='manual_stop'),
])
def test_improper_ownership_reference_never_queried(tmp_path,change):
    r=close_row();change(r)
    journal(tmp_path,[r])
    def forbid(_):raise AssertionError('exchange_read_should_not_happen')
    z=repair.reconcile(tmp_path,now=TSEC+60,client_factory=forbid)
    assert z['attempted']==0
    assert repair.summary(tmp_path)['post_cutover_missing_order_receipts']==(0 if r['reason']=='manual_stop' else 1)

def test_sidecar_fingerprint_change_fails_closed(tmp_path,monkeypatch):
    row=close_row()
    journal(tmp_path,[row])
    monkeypatch.setattr(core_close_fill_evidence,'verified_close_fills',
                        lambda *a,**kw:valid_proof())
    repair.reconcile(tmp_path,now=TSEC+60,
                     client_factory=lambda s:SimpleNamespace(symbol=s))
    row['fee']=0.8
    journal(tmp_path,[row])
    assert repair.reconcile(tmp_path,now=TSEC+800)['status']=='UNKNOWN'
    assert repair.summary(tmp_path)['status']=='UNKNOWN'

def test_horizon_only_grounded_exchange_exit_price_is_compared():
    base=dict(exit_id='one',exit_time='2026-10-15T13:00:00',
      side='short',exit_price=100.,exit_price_basis='verified_order_fills',
      horizons={'15':dict(time='2026-10-15T13:20:00',close=98.),
                '30':dict(time='2026-10-15T13:35:00',close=102.)})
    unverified=dict(base,exit_id='two',exit_price_basis=None,
                    horizons={'15':dict(time='2026-10-15T13:20:00',close=60.)})
    now=datetime(2026,10,15,14,0,tzinfo=KST)
    r=horizon.analyze([base,unverified],now=now)
    assert r['horizon_price_movement']['15']['proven_samples']==1
    assert r['horizon_price_movement']['15']['avg_signed_after_exit_bps']==200.
    assert r['horizon_price_movement']['30']['avg_signed_after_exit_bps']==-200.
    assert r['provenance_counts']['exit_price_unproven']==1

def test_outdated_horizon_does_not_count_as_15_minute_exit_price():
    row=dict(exit_id='one',exit_time='2026-10-15T13:00:00',side='long',
             exit_price=100.,exit_price_basis='verified_order_fills',
             horizons={'15':dict(time='2026-10-15T13:59:00',close=110.)})
    now=datetime(2026,10,15,14,0,tzinfo=KST)
    r=horizon.analyze([row],now=now)
    assert r['horizon_price_movement']['15']['proven_samples']==0
    assert r['horizon_price_movement']['15']['observation_lag_rejected']==1
