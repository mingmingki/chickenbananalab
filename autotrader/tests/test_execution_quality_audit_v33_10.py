"""v33-10 independent execution-proven accounting audit."""
import json
from datetime import datetime,timedelta,timezone
from pathlib import Path
import pytest
import execution_quality_audit_v33_10 as q
import ops_observability

KST=timezone(timedelta(hours=9))
NOW=datetime(2026,10,15,14,30,tzinfo=KST)

def record(kind,t,net=1.0,eid='e1',side='short',source='order_fills'):
    row=dict(strategy_group='core',type=kind,time=t,symbol='BTC/USDT:USDT',
             dry_run=False,side=side,amount=1.0,entry_price=81000.,
             close_price=80900.,pnl_source=source,okx_net_pnl=net,
             pnl=net+0.05,fee=.05,execution_id=eid)
    row['close_execution_evidence']=dict(price_basis='verified_order_fills',
          order_id=eid,trade_ids=[eid+'fill'],vwap=80900.,
          filled_contracts=1.)
    if kind=='open':
        row['price']=81000.
    return row

def test_exact_price_qty_fill_id_and_owned_algo_claim():
    x=record('reduce', '2026-10-15T13:00:00')
    assert q.proof_state(x)=='consistent'
    x['close_execution_evidence']['vwap']=80000.
    assert q.proof_state(x)=='invalid'
    x['close_execution_evidence']['vwap']=80900.
    x['close_execution_evidence']['trade_ids']=['f','f']
    assert q.proof_state(x)=='invalid'
    x['close_execution_evidence']['trade_ids']=['f']
    x['close_execution_evidence']['price_basis']='verified_owned_protection_fills'
    x['close_reason_evidence']={'order_id':'wrong','trade_ids':['f']}
    assert q.proof_state(x)=='invalid'
    x['close_reason_evidence']={'order_id':'e1','trade_ids':['f']}
    assert q.proof_state(x)=='consistent'

def test_estimated_not_mislabeled_recorded_and_missing_stays_unknown():
    x=record('close','2026-10-15T13:05:00',source='estimated')
    assert q.net_proof(x)[1]=='estimated'
    x['okx_net_pnl']=None;x['pnl']=None
    assert q.net_proof(x)==(None,'unknown')

def test_temporal_not_falsely_called_verified_lifecycle():
    o=record('open','2026-10-15T13:00:00')
    p=record('reduce','2026-10-15T13:10:00',net=2.,eid='r1')
    c=record('close','2026-10-15T13:20:00',net=-3.,eid='c1')
    a=q.summarize([o,p,c],now=NOW)
    assert a['core']['realized_events']==2
    assert a['core']['recorded_net_usdt']==-1.
    assert a['proven_order_temporal_lifecycle_loss_count']==1
    assert 'temporally_linked' in a['proven_order_temporal_lifecycle_losses'][0]['evidence']
    assert a['invalid_proof_count']==0

def test_no_reentry_profit_inference_only_counts():
    close=record('close','2026-10-15T13:00:00',side='short')
    open_=record('open','2026-10-15T13:11:00',side='short')
    report=q.summarize([close,open_],now=NOW)
    assert report['same_side_reentry_within_30m']==1
    assert report['reentry_examples'][0]['note'].startswith('time_order')

def test_duplicate_fill_id_not_recorded_as_unique():
    a=record('reduce','2026-10-15T13:00:00',eid='r1')
    b=record('close','2026-10-15T13:01:00',eid='c1')
    b['close_execution_evidence']['trade_ids']=['r1fill']
    report=q.summarize([a,b],now=NOW)
    assert report['duplicate_fill_id_count']==1

def test_corrupt_incomplete_input_fails_closed_without_chart_numbers(tmp_path):
    (tmp_path/'trades_log.jsonl').write_text('{"type": "close"}\nnot-json\n')
    a=q.from_user_dir(tmp_path,now=NOW)
    assert a['status']=='UNKNOWN'
    assert 'core' not in a

def test_operational_watchdog_has_read_only_quality_report(tmp_path,monkeypatch):
    row=record('close','2026-10-15T14:00:00')
    (tmp_path/'trades_log.jsonl').write_text(json.dumps(row)+'\n')
    result=ops_observability.build_snapshot(tmp_path,now=NOW)
    assert result['trade_execution_audit']['status']=='KNOWN'
    assert result['trade_execution_audit']['core']['realized_events']==1
    assert result['trade_execution_audit']['core']['execution_proof']['consistent']==1
    assert result['health']['critical_count']>=0
