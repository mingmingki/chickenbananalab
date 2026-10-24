from datetime import datetime,timedelta,timezone
from pathlib import Path
import json
import pytest
import core_exit_expectancy_audit_v33_13 as a

KST=timezone(timedelta(hours=9))
NOW=datetime(2026,10,15,17,35,tzinfo=KST)
def event(t='close',source='order_fills',proof=True,net=-1.0,reason='position_ai_close_all',
          time='2026-10-15T17:12:00'):
    r=dict(strategy_group='core',symbol='BTC/USDT:USDT',type=t,
           dry_run=False,reason=reason,side='short',
           time=time,pnl_source=source,okx_net_pnl=net,
           pnl=net+.1,fee=.1,amount=1.2,close_price=80000.)
    if proof:
        r['close_execution_evidence']=dict(price_basis='verified_order_fills',
            order_id='oid',trade_ids=['tid'],vwap=80000.,filled_contracts=1.2)
    return r

def test_verified_and_unverified_exits_not_mixed_as_expectancy():
    rows=[event(proof=False,net=-20),event(net=2)]
    result=a.analyze(rows,[],now=NOW,cutover=dict(baseline_line_count=1))
    p=result['by_exit_reason']['core|close|position_ai_close_all']
    assert p['records']==2
    assert p['order_fills_proven']==1
    assert p['ledger_reported_without_fill_proof']==1
    assert p['verified_exit_net_usdt']==2
    assert p['unlinked_reported_exit_net_usdt']==-20
    assert p['post_fee_quality']=='INSUFFICIENT_EVIDENCE'
    assert result['post_cutover_core_ai_exits']['verified']==1
    assert result['post_cutover_core_ai_exits']['unproven']==0
    assert not result['trading_change_recommended']

def test_no_proof_cannot_make_positive_ai_expectancy_actionable():
    rows=[event(proof=False,net=10) for _ in range(40)]
    x=a.analyze(rows,[],now=NOW)
    p=x['by_exit_reason']['core|close|position_ai_close_all']
    assert p['unlinked_reported_exit_net_usdt']==400
    assert p['verified_exit_net_usdt']==0
    assert p['post_fee_quality']=='INSUFFICIENT_EVIDENCE'

def test_cutover_scans_only_actual_new_entries():
    rows=[event(proof=False),event(proof=True),event(proof=False)]
    x=a.analyze(rows,[],now=NOW,cutover={'baseline_line_count':1})
    d=x['post_cutover_core_ai_exits']
    assert (d['count'],d['verified'],d['unproven'])==(2,1,1)
    assert len(d['unproven_examples'])==1
    x=a.analyze(rows,[],now=NOW,cutover={'baseline_line_count':99})
    assert x['cutover_status']=='INVALID_CURSOR'
    assert x['post_cutover_core_ai_exits']['count']==0

def test_only_recent_real_user_trade_rows():
    old='2026-10-01T11:00:00'
    rows=[event(time=old),event(),dict(event(),dry_run=True),
          dict(event(),strategy_group='legacy'),dict(event(),type='open')]
    x=a.analyze(rows,[],now=NOW)
    assert x['by_exit_reason']['core|close|position_ai_close_all']['records']==1

def test_reentry_shadow_is_observational_and_deduplicated():
    s=dict(exit_id='E1',exit_time='2026-10-15T16:00:00',
        analytical_outcome='reentry_loss',reentry_within_30m=True,
        actual_exit_coverage='complete',next_lifecycle_coverage='complete',
        churn_cycle_net=-4.3)
    x=a.analyze([], [dict(s,churn_cycle_net=-1),s],now=NOW)
    d=x['short_horizon_reentry_shadow']
    assert d['observed_ai_exits']==1
    assert d['same_side_30m_with_complete_shadow']==1
    assert d['combined_churn_net_reported_usdt']==-4.3
    assert 'not causal' in d['interpretation']

def test_missing_or_truncated_file_fails_without_accidental_financial_claim(tmp_path):
    assert a.from_user_dir(tmp_path)['status']=='UNKNOWN'
    (tmp_path/'trades_log.jsonl').write_text('{"incomplete":')
    # The incomplete tail is ignored, but no trade is claimed.
    x=a.from_user_dir(tmp_path,now=NOW)
    assert x['status']=='KNOWN'
    assert x['by_symbol']=={}

def test_aggregate_without_entry_fees_not_claimed_account_profit():
    x=a.analyze([event(net=3)],[],now=NOW)
    d=x['by_symbol']['core|BTC/USDT:USDT']
    assert d['profits_are_not_account_returns']
    assert x['net_scope'].startswith('EXIT / REDUCE')
