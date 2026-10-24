import json
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
import pytest
import core_profit_retention_audit_v33_12 as a
import reduce_v2_state

KST=timezone(timedelta(hours=9))
NOW=datetime(2026,10,15,16,30,tzinfo=KST)
SYMBOL='BTC/USDT:USDT'
POSITION=dict(side='short',position_id='p1',entry_timestamp_ms=1792041074179,
              entry_price=81246.4)
IDENT=reduce_v2_state.position_identity(POSITION)

def state():
    return {SYMBOL:dict(lifecycle_id=IDENT,baseline_known=True,
        pending_order=None,actual_reduced_contracts=.49,
        executed_ids=['cr1'],entry_price=81246.4)}

def reduction(net=2.4):
    return dict(type='reduce',strategy_group='core',symbol=SYMBOL,side='short',
        dry_run=False,pnl_source='order_fills',execution_id='cr1',
        entry_price=81246.4,amount=.49,close_price=80900.,okx_net_pnl=net,
        time='2026-10-15T15:08:44',
        close_execution_evidence=dict(price_basis='verified_order_fills',
              order_id='OID',trade_ids=['F1'],vwap=80900.,filled_contracts=.49))

def lock(hour='14:51:16',reason='ordinary_075r',ident=IDENT):
    return (f'2026-10-15 {hour},352 [WARNING] trader.chickenbananalab: '
      f'[BTC/USDT:USDT] CORE_PROFIT_FLOOR_LOCKED reason={reason} '
      f'mfe_r=0.756538 live_current_r=0.689596 entry=81246.4 '
      f'initial_r=703.6 mark=80761.2 fee_pct=0.1266 sl=81950.0 -> 81143.5 '
      f'algo_id=123 contracts=1.96 tp=79650 identity={ident}')

def test_parse_lock_and_real_pre_partial_classification():
    logs=a.parse_locks(lock())
    assert len(logs)==1 and logs[0]['new_stop']==81143.5
    result=a.analyze([reduction()],state(),logs,now=NOW)
    assert result['total_lock_events']==1
    assert result['lock_reasons']=={'ordinary_075r':1}
    assert result['core'][SYMBOL]['classification']=='PROTECTED_BEFORE_VERIFIED_PARTIAL'
    assert result['core'][SYMBOL]['reduction']['net_usdt']==2.4
    assert result['realized_retention_effect']=='NOT_ESTABLISHED'

def test_later_lock_never_claimed_to_be_caused_by_partial():
    logs=a.parse_locks(lock('15:15:16',reason='ordinary_075r'))
    result=a.analyze([reduction()],state(),logs,now=NOW)
    assert result['core'][SYMBOL]['classification']=='LOCK_AFTER_VERIFIED_PARTIAL_NEEDS_ORDER_CAUSALITY'
    assert result['verified_partial_triggered_locks']==0

def test_actual_explicit_partial_trigger_is_counted_separately():
    logs=a.parse_locks(lock('15:15:16',reason='verified_profitable_partial_close'))
    result=a.analyze([reduction()],state(),logs,now=NOW)
    assert result['verified_partial_triggered_locks']==1

@pytest.mark.parametrize('change',[
    lambda st,rows: st[SYMBOL].update(lifecycle_id='wrong:1792140000000:short'),
    lambda st,rows: st[SYMBOL].update(pending_order=dict(id='x')),
    lambda st,rows: st[SYMBOL].update(executed_ids=['missing']),
    lambda st,rows: rows[0].update(okx_net_pnl=-1.),
    lambda st,rows: rows[0].update(pnl_source='estimated'),
    lambda st,rows: rows[0].update(close_execution_evidence=None),
])
def test_incomplete_evidence_never_claims_profitable_partial(change):
    st=state();rows=[reduction()];change(st,rows)
    x=a.analyze(rows,st,a.parse_locks(lock()),now=NOW)
    assert x['core'][SYMBOL]['reduction']['status']!='PROVEN_POSITIVE'
    assert x['core'][SYMBOL]['classification']=='PROOF_NOT_SATISFIED'

def test_log_coverage_truncated_is_declared():
    x=a.analyze([reduction()],state(),[],now=NOW,coverage_limited=True)
    assert x['log_coverage_limited'] is True
    assert x['core'][SYMBOL]['classification']=='NO_PROVEN_LOCK_EVENT_IN_COVERAGE'

def test_watchdog_snapshot_reports_field_in_real_entry_point(tmp_path,monkeypatch):
    import ops_watchdog as w
    import ops_observability as obs
    (tmp_path/'trades_log.jsonl').write_text(json.dumps(reduction())+'\n')
    (tmp_path/'reduce_v2_state.json').write_text(json.dumps(state()))
    result=a.from_user_dir(tmp_path,logdir=tmp_path,now=NOW)
    assert result['status']=='KNOWN'
    assert result['log_coverage_limited']
