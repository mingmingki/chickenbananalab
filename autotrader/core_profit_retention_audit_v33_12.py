"""v33-12 independent, read-only CORE profit-floor causality audit.

A logged SL amendment is not proof of a profitably filled exit. This report
never infers realized lifecycle PnL from nearby journal rows, and never calls
OKX or alters a position. It distinguishes stops locked BEFORE a reduction
from a reduction-triggered or later MFE amendment.
"""
from collections import Counter
from datetime import datetime,timezone,timedelta
from pathlib import Path
import json,math,re

KST=timezone(timedelta(hours=9))
CORE=('BTC/USDT:USDT','ETH/USDT:USDT','XRP/USDT:USDT','ADA/USDT:USDT')
LOCK_RE=re.compile(
    r'^([0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}),.*?'
    r'\[(?P<symbol>[^\]]+)\] CORE_PROFIT_FLOOR_LOCKED reason=(?P<reason>[a-z0-9_]+)'
    r'.*? sl=(?P<prior>[0-9.]+) -> (?P<stop>[0-9.]+)'
    r'.*? identity=(?P<identity>[^ \t\r\n]+)')
MAX_LOG_BYTES=8*1024*1024

def finite(v):
    if isinstance(v,bool):return None
    try:
        f=float(v)
        return f if math.isfinite(f) else None
    except (ValueError,TypeError,OverflowError):return None

def parse_locks(text):
    items=[]
    for line in text.splitlines():
        m=LOCK_RE.search(line)
        if not m or m.group('symbol') not in CORE:continue
        try:
            when=datetime.strptime(m.group(1),'%Y-%m-%d %H:%M:%S').replace(tzinfo=KST)
            before=finite(m.group('prior'));after=finite(m.group('stop'))
            if before is None or after is None or before<=0 or after<=0:
                continue
        except (ValueError,TypeError):
            continue
        items.append(dict(time=when.isoformat(),symbol=m.group('symbol'),
            reason=m.group('reason'),identity=m.group('identity'),
            prior_stop=before,new_stop=after))
    return items

def _day_log(logdir,day):
    path=Path(logdir)/('trader_'+day.strftime('%Y-%m-%d')+'.log')
    try:
        with path.open('rb') as f:
            total=f.seek(0,2)
            f.seek(max(0,total-MAX_LOG_BYTES))
            text=f.read().decode('utf-8',errors='replace')
        return parse_locks(text),bool(total>MAX_LOG_BYTES)
    except OSError:
        return [],True

def _verify_reduction(symbol,identity,states,rows):
    import core_partial_profit_residual_guard as guard
    s=states.get(symbol)
    if not isinstance(s,dict) or s.get('lifecycle_id')!=identity:
        return dict(status='UNVERIFIED',reason='no_matching_durable_lifecycle')
    ids=s.get('executed_ids') or []
    if not ids or not finite(s.get('actual_reduced_contracts')):
        return dict(status='NONE',reason='no_executed_reduction')
    matched=[r for r in rows if r.get('execution_id') in ids and r.get('symbol')==symbol]
    if len(matched)!=len(ids):
        return dict(status='UNVERIFIED',reason='reduction_records_missing_or_duplicated')
    # Need the exact exchange identity, side and entry for the durable state.
    parts=str(identity).split(':')
    if len(parts)!=3:
        return dict(status='UNVERIFIED',reason='identity_invalid')
    position=dict(position_id=parts[0],entry_timestamp_ms=int(parts[1]),
                  side=parts[2],entry_price=s.get('entry_price'))
    if not guard.confirmed_profitable_partial(symbol,position,s,matched):
        return dict(status='UNVERIFIED',reason='not_all_reductions_proven_positive')
    net=sum(float(r['okx_net_pnl']) for r in matched)
    times=[]
    for r in matched:
        try:
            d=datetime.fromisoformat(str(r['time']))
            d=d.replace(tzinfo=KST) if d.tzinfo is None else d.astimezone(KST)
            times.append(d)
        except (TypeError,ValueError):return dict(status='UNVERIFIED',reason='reduction_time_invalid')
    return dict(status='PROVEN_POSITIVE',reason=None,net_usdt=round(net,8),
                execution_ids=len(ids),
                last_reduction_at=max(times).isoformat())

def analyze(rows,states,locks,*,now=None,coverage_limited=False):
    now=now or datetime.now(KST)
    if now.tzinfo is None:now=now.replace(tzinfo=KST)
    window_start=now-timedelta(days=1)
    recent=[]
    for item in locks:
        try:when=datetime.fromisoformat(item['time']).astimezone(KST)
        except (ValueError,KeyError,TypeError):continue
        if window_start<=when<=now and item['symbol'] in CORE:recent.append(item)
    reasons=Counter(x['reason'] for x in recent)
    per_symbol={}
    for sym in CORE:
        state=states.get(sym) or {}
        identity=state.get('lifecycle_id')
        events=[x for x in recent if x['symbol']==sym and x['identity']==identity]
        evidence=_verify_reduction(sym,identity,states,rows)
        cause='NOT_EVALUABLE'
        if evidence['status']=='PROVEN_POSITIVE':
            reduced_at=datetime.fromisoformat(evidence['last_reduction_at'])
            before=[x for x in events if datetime.fromisoformat(x['time'])<reduced_at]
            after=[x for x in events if datetime.fromisoformat(x['time'])>=reduced_at]
            if before and not after:
                cause='PROTECTED_BEFORE_VERIFIED_PARTIAL'
            elif after:
                cause='LOCK_AFTER_VERIFIED_PARTIAL_NEEDS_ORDER_CAUSALITY'
            else:
                cause='NO_PROVEN_LOCK_EVENT_IN_COVERAGE'
        elif evidence['status']=='NONE':
            cause='NO_REDUCTION_YET'
        else:
            cause='PROOF_NOT_SATISFIED'
        per_symbol[sym]=dict(identity_verified_by_durable_state=bool(identity),
            reduction=evidence,classification=cause,
            lock_event_count=len(events),
            last_lock=({k:v for k,v in events[-1].items() if k!='identity'} if events else None))
    return dict(status='KNOWN',generated_at=now.isoformat(),
        window_hours=24,log_coverage_limited=coverage_limited,
        lock_reasons=dict(reasons),total_lock_events=len(recent),
        verified_partial_triggered_locks=reasons.get('verified_profitable_partial_close',0),
        core=per_symbol,
        realized_retention_effect='NOT_ESTABLISHED',
        financial_note='SL amendment is not a confirmed fill; lifecycle-level post-reduction realized net needs exact close fill evidence; no counterfactual profit is inferred')

def from_user_dir(user_dir,*,logdir=None,now=None):
    now=now or datetime.now(KST)
    if now.tzinfo is None:now=now.replace(tzinfo=KST)
    root=Path(user_dir)
    try:
        path=root/'trades_log.jsonl'
        raw=path.read_text()
        if raw and not raw.endswith('\n'):raw=raw.rsplit('\n',1)[0]+'\n'
        rows=[json.loads(x) for x in raw.splitlines() if x.strip()]
        state=json.loads((root/'reduce_v2_state.json').read_text())
        if not isinstance(state,dict):raise ValueError('invalid_state')
        path=Path(logdir) if logdir else Path(__file__).parent/'logs'
        today,limit=_day_log(path,now.date())
        # Tail yesterday's file too, for after-midnight continuity.
        yesterday,prev_limit=_day_log(path,now.date()-timedelta(days=1))
        return analyze(rows,state,yesterday+today,now=now,
                       coverage_limited=limit or prev_limit)
    except (OSError,ValueError,TypeError,OverflowError) as exc:
        return dict(status='UNKNOWN',reason=type(exc).__name__)
