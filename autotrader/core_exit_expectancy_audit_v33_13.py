"""v33-13 read-only post-cost EXIT evidence & expectancy-quality audit.

No trading recommendation, future-return forecast, or permissions to place orders.
A close net reported by an exchange-position ledger is still NOT a verified
order fill unless the matching close execution proof is present. Entry fees,
funding allocation, and exact complete lifecycle attribution are separate.
"""
from collections import Counter,defaultdict
from datetime import datetime,timedelta,timezone
from pathlib import Path
import json,math
from execution_quality_audit_v33_10 import proof_state,net_proof,event_time

KST=timezone(timedelta(hours=9))
SYMBOLS=('BTC/USDT:USDT','ETH/USDT:USDT','XRP/USDT:USDT',
         'ADA/USDT:USDT','DOGE/USDT:USDT','SOL/USDT:USDT')
CUTOVER='v33_13_exit_evidence_cutover.json'

def n(v):
    if isinstance(v,bool):return None
    try:
        out=float(v)
        return out if math.isfinite(out) else None
    except (TypeError,ValueError,OverflowError):
        return None

def _money(v):return round(v,6)

def _group(rows):
    proven=[]
    reported=[]
    estimated=[]
    fee=0.
    gap=Counter()
    for r in rows:
        value,quality=net_proof(r)
        proof=proof_state(r)
        if proof=='consistent' and quality=='recorded' and value is not None:
            proven.append(value)
        elif quality=='recorded' and value is not None:
            reported.append(value)
        else:
            estimated.append(value)
        f=n(r.get('fee'))
        if f is not None and f>=0:fee+=f
        gap[(proof,quality)]+=1
    values=proven
    positives=sum(x>0 for x in values)
    negatives=sum(x<0 for x in values)
    gains=sum(x for x in values if x>0)
    losses=sum(x for x in values if x<0)
    return dict(records=len(rows),order_fills_proven=len(proven),
        ledger_reported_without_fill_proof=len(reported),
        estimated_or_unknown=len(estimated),
        verified_exit_net_usdt=_money(sum(proven)),
        unlinked_reported_exit_net_usdt=_money(sum(reported)),
        proven_avg_exit_net_usdt=_money(sum(proven)/len(proven)) if proven else None,
        proven_win_fraction=round(positives/len(proven),5) if proven else None,
        proven_profit_factor=round(gains/abs(losses),4) if losses else None,
        observed_exit_fees_usdt=_money(fee),
        evidence_coverage_pct=round(100*len(proven)/len(rows),2) if rows else None,
        post_fee_quality='ENOUGH_FOR_EXIT_ONLY_COMPARISON' if (
            len(rows)>=15 and len(proven)>=15 and len(proven)/len(rows)>=.9) else 'INSUFFICIENT_EVIDENCE',
        breakdown={f'{k[0]}:{k[1]}':v for k,v in sorted(gap.items())},
        profits_are_not_account_returns=True)

def analyze(rows,shadow,*,now=None,days=7,cutover=None):
    now=now or datetime.now(KST)
    if now.tzinfo is None:now=now.replace(tzinfo=KST)
    since=now-timedelta(days=days)
    qualified=[]
    for idx,r in enumerate(rows):
        if (not isinstance(r,dict) or r.get('dry_run') is not False
            or r.get('type') not in ('reduce','close')
            or r.get('strategy_group') not in ('core','candidate_c')
            or r.get('symbol') not in SYMBOLS):continue
        when=event_time(r)
        if when is not None and since<=when<=now:
            qualified.append((idx,r))
    groups={}
    by_reason=defaultdict(list)
    for idx,r in qualified:
        group=r['strategy_group']
        symbol=r['symbol']
        key=group+'|'+symbol
        groups.setdefault(key,[]).append(r)
        reason=f"{group}|{r['type']}|{r.get('reason') or 'unclassified'}"
        by_reason[reason].append(r)
    per_symbol={key:_group(recs) for key,recs in sorted(groups.items())}
    reason_summary={key:_group(recs) for key,recs in sorted(by_reason.items())}
    # Shadow events are mutable append-only snapshots. Deduplicate exact ID.
    last={}
    for r in shadow or ():
        if not isinstance(r,dict) or not r.get('exit_id'):continue
        last[r['exit_id']]=r
    shadow_latest=[]
    for s in last.values():
        try:when=datetime.fromisoformat(s['exit_time'])
        except (ValueError,TypeError,KeyError):continue
        when=when.replace(tzinfo=KST) if when.tzinfo is None else when.astimezone(KST)
        if since<=when<=now:shadow_latest.append(s)
    shadow_states=Counter(s.get('analytical_outcome') or 'unknown' for s in shadow_latest)
    confirmed_churn=[]
    for x in shadow_latest:
        if (x.get('reentry_within_30m') is True
            and x.get('actual_exit_coverage')=='complete'
            and x.get('next_lifecycle_coverage')=='complete'
            and n(x.get('churn_cycle_net')) is not None):
            confirmed_churn.append(x)
    # The shadow lifecycle pairing can depend on temporal attribution and is
    # not an experimental control. Never label this as a causal effect.
    cutover_status='NOT_INITIALIZED'
    post_ai=dict(count=0,verified=0,unproven=0,unproven_examples=[])
    post_all=dict(count=0,verified=0,unproven=0)
    if isinstance(cutover,dict) and type(cutover.get('baseline_line_count')) is int:
        baseline=cutover['baseline_line_count']
        if 0<=baseline<=len(rows):
            cutover_status='KNOWN'
            for i,r in qualified:
                if i<baseline or r.get('strategy_group')!='core' or r.get('type')!='close':continue
                proof_ok=proof_state(r)=='consistent'
                post_all['count']+=1
                post_all['verified' if proof_ok else 'unproven']+=1
                if r.get('reason')=='position_ai_close_all':
                    post_ai['count']+=1
                    post_ai['verified' if proof_ok else 'unproven']+=1
                    if not proof_ok and len(post_ai['unproven_examples'])<8:
                        post_ai['unproven_examples'].append({
                            'time':r.get('time'),'symbol':r.get('symbol'),
                            'proof_state':proof_state(r)})
        else:
            cutover_status='INVALID_CURSOR'
    return dict(status='KNOWN',as_of=now.isoformat(),window_days=days,
        by_symbol=per_symbol,by_exit_reason=reason_summary,
        short_horizon_reentry_shadow=dict(observed_ai_exits=len(shadow_latest),
            analytical_outcomes=dict(shadow_states),
            same_side_30m_with_complete_shadow=len(confirmed_churn),
            combined_churn_net_reported_usdt=_money(sum(n(s['churn_cycle_net']) for s in confirmed_churn)),
            interpretation='observational temporal lifecycle pairing, not causal intervention'),
        cutover_status=cutover_status,
        post_cutover_core_ai_exits=post_ai,post_cutover_all_core_closes=post_all,
        trading_change_recommended=False,
        net_scope='EXIT / REDUCE results only. Entry commissions and funding may be missing; no predictive expectancy or account PnL is inferred.')

def from_user_dir(user_dir,*,now=None,days=7):
    root=Path(user_dir)
    try:
        p=root/'trades_log.jsonl'
        raw=p.read_text(encoding='utf-8')
        if raw and not raw.endswith('\n'):
            raw=raw.rsplit('\n',1)[0]+'\n' if '\n' in raw else ''
        rows=[json.loads(x) for x in raw.splitlines() if x.strip()]
        if not all(isinstance(x,dict) for x in rows):raise ValueError('non_mapping_trade')
        path=root/'exit_reentry_shadow.jsonl'
        shadow=[]
        if path.exists():
            for line in path.read_text(encoding='utf-8').splitlines():
                try:s=json.loads(line)
                except (ValueError,TypeError):continue
                if isinstance(s,dict):shadow.append(s)
        cutoff=root/CUTOVER
        cutover=json.loads(cutoff.read_text()) if cutoff.exists() else None
        return analyze(rows,shadow,now=now,days=days,cutover=cutover)
    except (OSError,ValueError,TypeError,OverflowError) as exc:
        return dict(status='UNKNOWN',reason=type(exc).__name__)
