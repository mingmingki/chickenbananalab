"""v33-72: immutable OKX reduce proof + candidate risk/profit diagnostics (read-only).
Does not alter live PnL/position/risk, trade journal, orders, or trading decisions.
"""
from collections import Counter,defaultdict
from decimal import Decimal,InvalidOperation
from pathlib import Path
import datetime as dt
import hashlib
import json

REPORT=Path('/var/lib/autotrader/deployment-evidence/candidate_c_reduce_exact_fills_v33_72_20261020KST/read_only_order_fill_audit.json')
SHA=REPORT.with_name('sha256.txt')
KST=dt.timezone(dt.timedelta(hours=9))
def decimal(x):
    if x is None or isinstance(x,bool):raise ValueError("unknown_numeric")
    v=Decimal(str(x))
    if not v.is_finite():raise ValueError("invalid_numeric")
    return v

def event_key(r):
    return (r.get('type'),r.get('symbol'),r.get('execution_id'),r.get('time'))

def digest_row(row):
    return hashlib.sha256(json.dumps(row,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def bounded_report():
    raw=REPORT.read_bytes()
    checksum=hashlib.sha256(raw).hexdigest()
    lines=SHA.read_text().strip().splitlines()
    if len(lines)!=1 or lines[0]!=checksum+'  '+str(REPORT):
        raise ValueError("immutable_proof_checksum_mismatch")
    report=json.loads(raw)
    if report.get('version')!='v33-72' or report.get('mode')!='READ_ONLY':
        raise ValueError("unsupported_proof_version")
    cases=report.get('cases')
    if not isinstance(cases,list) or len(cases)!=25:
        raise ValueError("invalid_expected_case_count")
    if any(x.get('status')!='OKX_ORDER_FILLS_VERIFIED'
           or not x.get('trade_ids') or not x.get('bill_ids')
           or not x.get('order_reduce_only') for x in cases):
        raise ValueError("incomplete_exact_order_proof")
    if len({int(x['row_index']) for x in cases})!=len(cases):
        raise ValueError("duplicate_case_indices")
    return report,checksum

def summarize_events(events):
    values=[float(x['net']) for x in events]
    wins=[x for x in values if x>0]
    losses=[x for x in values if x<=0]
    return {
        'events':len(events),
        'net_usdt':round(sum(values),6),
        'event_win_pct':round(len(wins)/len(values)*100,3) if values else None,
        'event_profit_factor':round(sum(wins)/abs(sum(losses)),5) if sum(losses)<0 else None,
        'event_avg_net_usdt':round(sum(values)/len(values),6) if values else None,
        'not_independent_trade_lifecycles':True,
    }

def analyze(journal_rows,proof):
    chosen=[(i,r) for i,r in enumerate(journal_rows)
          if r.get('type') in ('reduce','close') and
          r.get('strategy_group')=='candidate_c' and not r.get('dry_run')]
    proofcases=proof['cases']
    attested={}
    for x in proofcases:
        i=x['row_index']
        if not isinstance(i,int) or i<0 or i>=len(journal_rows):
            raise ValueError("missing_original_journal_row")
        row=journal_rows[i]
        if digest_row(row)!=x['source_row_sha256'] or row.get('okx_net_pnl') is not None:
            raise ValueError("historical_row_rewritten_or_already_corrected")
        if row.get('type')!='reduce' or row.get('symbol')!=x['symbol'] or row.get('execution_id')!=x['execution_id']:
            raise ValueError("proof_row_identity_mismatch")
        if i in attested:raise ValueError("duplicate_attested_row")
        attested[i]=x
    if len(attested)!=25:
        raise ValueError('unmatched_historical_reductions')
    fallback=[]
    reconciled=[]
    for i,r in chosen:
        gross=decimal(r.get('pnl') or 0)
        fee=decimal(r.get('fee') or 0)
        net=decimal(r['okx_net_pnl']) if r.get('okx_net_pnl') is not None else gross-fee
        fallback.append(dict(index=i,symbol=r['symbol'],reason=r.get('reason'),date=str(r['time'])[:10],net=net))
        if i in attested:
            # Only the cash delta of the verified close-order fills is available
            # here. Entry fees/funding require independent lifecycle attribution.
            exact=decimal(attested[i]['exchange_close_order_balance_change'])
            reconciled.append(dict(index=i,entry_reference=r['execution_id'],date=str(r['time'])[:10],
                symbol=r['symbol'],legacy_net_fallback=str(net),
                exchange_close_order_cash=str(exact),delta_vs_fallback=str(exact-net),
                gross_matches_journal=attested[i]['gross_matches_journal']))
    if len(reconciled)!=25:raise ValueError("incomplete_event_coverage")
    grossdiff=sum((decimal(x['delta_vs_fallback']) for x in reconciled),Decimal(0))
    selected_periods={
        'full_record':fallback,
        'pre_2026_10_05':[x for x in fallback if x['date']<'2026-10-05'],
        '2026_10_05_to_12':[x for x in fallback if '2026-10-05'<=x['date']<'2026-10-13'],
        'since_2026_10_13':[x for x in fallback if x['date']>='2026-10-13'],
    }
    cohorts={name:summarize_events(rows) for name,rows in selected_periods.items()}
    bysymbol={name:summarize_events([x for x in fallback if x['symbol'].startswith(name)])
              for name in ('DOGE','SOL')}
    group=defaultdict(list)
    for x in fallback:
        reason=x['reason'] or ('LEGACY_REDUCE_UNLABELED' if journal_rows[x['index']]['type']=='reduce'
                               else 'CLOSE_UNLABELED')
        group[reason].append(x)
    reasons={k:summarize_events(v) for k,v in group.items()}
    largest_losses=sorted(fallback,key=lambda x:x['net'])[:7]
    return dict(version='v33-72',contract='candidate_c_exact_bills_and_profits_v33_72',
         source='read_only_exact_okx_fill_proof',
         verified_reduce_events=25,
         verified_exchange_gross_usdt=proof['summary']['verified_close_order_gross_total'],
         verified_exchange_exit_fees_signed_usdt=proof['summary']['verified_close_order_fee_total_signed'],
         verified_exchange_close_order_cash_usdt=proof['summary']['verified_close_order_balance_change_total'],
         exact_vs_current_fallback_delta_usdt=str(grossdiff),
         caution='Do not add the close-order total to existing PnL: existing app includes estimated fallback. Entry fees/funding and whole-account balance are not yet fully reconciled.',
         gross_mismatch_count=proof['summary']['mismatched_gross'],
         cohorts=cohorts,by_symbol=bysymbol,by_exit_reason=reasons,
         largest_losses=[dict(symbol=x['symbol'],date=x['date'],net_usdt=round(float(x['net']),4),reason=x['reason']) for x in largest_losses],
         recent_negative_edge_advisory=bool(cohorts['since_2026_10_13']['events']>=20 and
                                   (cohorts['since_2026_10_13']['event_profit_factor'] or 0)<1.0),
         orders_or_strategy_modified=False,
         evidence_rows=[x for x in reconciled])
def status(user_dir,*,details=False):
    proof,sha=bounded_report()
    journal=Path(user_dir)/'trades_log.jsonl'
    content=journal.read_bytes()
    rows=[json.loads(x) for x in content.decode().splitlines() if x.strip()]
    report=analyze(rows,proof)
    report['proof_sha256']=sha
    report['journal_sha256_now']=hashlib.sha256(content).hexdigest()
    report['journal_original_snapshot_matches']=report['journal_sha256_now']==proof['journal_sha256']
    report['as_of_proof']=proof.get('time')
    if not details:report.pop('evidence_rows',None)
    return report
