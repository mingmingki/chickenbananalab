"""v33-19 read-only CORE close-lifecycle provenance audit of pre-GPT blocks."""
from datetime import datetime,timedelta,timezone
from pathlib import Path
from collections import Counter,defaultdict
import json,sqlite3
from core_profit_stop_reentry import verified_favorable_stop
KST=timezone(timedelta(hours=9))
CORE=('BTC/USDT:USDT','ETH/USDT:USDT','XRP/USDT:USDT','ADA/USDT:USDT')
FOCUS=('cooldown','protected_stop_stale_setup')
def when(s):
 try:
  x=datetime.fromisoformat(str(s))
  return (x if x.tzinfo else x.replace(tzinfo=KST)).astimezone(KST)
 except (ValueError,TypeError,OverflowError):return None
def examine(event,latest,older_external):
    time=when(event.get('time'))
    side=event.get('side')
    if time is None:return 'BAD_EVENT_TIME'
    if not latest:return 'NO_PREVIOUS_CLOSE'
    close_at=when(latest.get('time'))
    if close_at is None:return 'BAD_CLOSE_TIME'
    age=(time-close_at).total_seconds()/60
    if age<0:return 'FUTURE_CLOSE'
    if event.get('reason')=='protected_stop_stale_setup':
        if verified_favorable_stop(latest) and latest.get('side')==side:
            return 'CURRENT_VERIFIED_FAVORABLE_STOP_REQUIRES_FRESH_SETUP'
        return 'STOP_PROVENANCE_MISMATCH_NEEDS_REVIEW'
    if older_external is not None and older_external!=latest:
        # This is an observational warning, not proof cooldown was wrong:
        # a new AI close can independently require cooldown.
        if latest.get('reason')=='position_ai_close_all':
            return 'LATEST_AI_CLOSE_OWN_COOLDOWN'
        if latest.get('reason') in ('manual_stop','external_close_unknown'):
            return 'LATEST_MANUAL_OR_UNKNOWN_COOLDOWN'
        return 'OLDER_EXTERNAL_CLOSE_PRESENT_RECHECK'
    if verified_favorable_stop(latest) and latest.get('side')==side:
        return 'CURRENT_VERIFIED_FAVORABLE_STOP_30M_LOCK' if age<30 else 'SUSPECT_STOP_30M_EXPIRED'
    if age<=30:return 'RECENT_CLOSE_WITHIN_30M'
    return 'SUSPECT_OTHER_EXPIRED_COOLDOWN'
def audit(user_dir,*,now=None):
    now=now or datetime.now(KST)
    if now.tzinfo is None:now=now.replace(tzinfo=KST)
    cutoff=now-timedelta(days=7)
    home=Path(user_dir)
    try:
        with sqlite3.connect(f'file:{home/"core_entry_events.sqlite3"}?mode=ro',uri=True,timeout=2) as db:
            events=[json.loads(p) for (p,) in db.execute('SELECT payload FROM events ORDER BY seq DESC LIMIT 10000')]
        tradepath=home/'trades_log.jsonl'
        closes=defaultdict(list)
        for line in tradepath.read_text(encoding='utf-8').splitlines():
            try:x=json.loads(line)
            except (ValueError,TypeError):continue
            if isinstance(x,dict) and x.get('type')=='close' and x.get('symbol') in CORE and when(x.get('time')):
                closes[x['symbol']].append((when(x['time']),x))
        for entries in closes.values():entries.sort(key=lambda x:x[0])
        tally=Counter();examples=[]
        focus_count=0
        for row in events:
            if (row.get('symbol') not in CORE or (row.get('engine') or 'CORE')!='CORE'
                or row.get('status')!='LOCAL_BLOCKED' or row.get('reason') not in FOCUS):continue
            time=when(row.get('time'))
            if time is None or not cutoff<=time<=now:continue
            focus_count+=1
            previous=[x for when_close,x in closes[row['symbol']] if when_close<=time]
            last=previous[-1] if previous else None
            external=next((x for x in reversed(previous) if x.get('reason') in
                ('stop_loss','take_profit','external_close_unknown')),None)
            classification=examine(row,last,external)
            tally[(row['reason'],classification)]+=1
            if 'SUSPECT' in classification and len(examples)<8:
                examples.append(dict(event_at=row['time'],symbol=row['symbol'],reason=row['reason'],
                    classification=classification,prior_close_at=last.get('time') if last else None))
        return dict(status='KNOWN',generated_at=now.isoformat(),days=7,
            focus_event_rows=focus_count,breakdown={
                '|'.join(k):v for k,v in sorted(tally.items())},suspect_cases=examples,
            note='Observational journal-event linkage; repeated attempts are not distinct trades. No approval or performance claim.')
    except (OSError,ValueError,sqlite3.Error,TypeError) as exc:
        return dict(status='UNKNOWN',reason=type(exc).__name__)
