"""v33-17 CORE entry attribution: read-only Gemini HOLD vs historical safety blocks.

Does not approve, replay or place orders. Historical missed signals are not
entries and cannot be converted into positions retroactively.
"""
from pathlib import Path
from datetime import datetime,timedelta,timezone
from collections import Counter
import json,re

KST=timezone(timedelta(hours=9))
SYMBOLS=('BTC/USDT:USDT','ETH/USDT:USDT','XRP/USDT:USDT','ADA/USDT:USDT')
GEMINI=re.compile(r'\[(?P<symbol>[^\]]+)\] Gemini 판단: (?P<decision>long|short|hold) \(확신도 (?P<confidence>[0-9.]+)\)')
SAFETY=re.compile(r'\[(?P<symbol>[^\]]+)\] BLOCK core_kill_switch: (?P<reason>.+?) - 신규 (?P<side>long|short) 진입 차단')
TIMING=re.compile(r'\[(?P<symbol>[^\]]+)\] CORE_ENTRY_TIMING phase=(?P<phase>[^ ]+) side=(?P<side>[^ ]+)')
TIMESTAMP=re.compile(r'^(\d{4}-\d{2}-\d{2} \d\d:\d\d:\d\d),')
MAX_DAY_BYTES=5*1024*1024

def dt_of(line):
    m=TIMESTAMP.match(line)
    if not m:return None
    try:return datetime.strptime(m.group(1),'%Y-%m-%d %H:%M:%S').replace(tzinfo=KST)
    except ValueError:return None

def observed(lines,*,now,release_at=None,kill_active=False,lookback_minutes=120):
    if now.tzinfo is None:now=now.replace(tzinfo=KST)
    now=now.astimezone(KST)
    since=now-timedelta(minutes=lookback_minutes)
    rows={sym:dict(gemini=None,safety_block=None,timing=None,
                   historical_safety_blocks=0,post_release_safety_blocks=0) for sym in SYMBOLS}
    analyzed=0
    for line in lines:
        t=dt_of(line)
        if t is None or t<since or t>now:continue
        for regex,field in ((GEMINI,'gemini'),(SAFETY,'safety_block'),(TIMING,'timing')):
            m=regex.search(line)
            if m is None or m.group('symbol') not in rows:continue
            symbol=m.group('symbol')
            obj={'time':t.isoformat(),**{k:v for k,v in m.groupdict().items() if k!='symbol'}}
            if regex is GEMINI:
                obj['confidence']=float(obj['confidence'])
            rows[symbol][field]=obj
            analyzed+=1
            if regex is SAFETY:
                if release_at and t<=release_at:
                    rows[symbol]['historical_safety_blocks']+=1
                elif release_at and t>release_at:
                    rows[symbol]['post_release_safety_blocks']+=1
            break
    for sym,item in rows.items():
        gem=item['gemini'];block=item['safety_block']
        if kill_active:
            state='GLOBAL_SAFETY_STOP_ACTIVE'
        elif item['post_release_safety_blocks']:
            state='SAFETY_BLOCK_AFTER_RELEASE_NEEDS_RECHECK'
        elif gem and gem['decision']=='hold':
            state='GEMINI_HOLD'
        elif gem and gem['decision'] in ('long','short'):
            state='SIGNAL_NOT_SAME_AS_ORDER_APPROVAL'
        elif block and release_at is None:
            state='SAFETY_RELEASE_EVIDENCE_UNKNOWN'
        else:
            state='NO_RECENT_GEMINI_DECISION'
        item['classification']=state
    return dict(status='KNOWN',generated_at=now.isoformat(),window_minutes=lookback_minutes,
        safety_active=bool(kill_active),
        release_verified_at=release_at.isoformat() if release_at else None,
        parsed_events=analyzed,
        historical_core_safety_blocks=sum(v['historical_safety_blocks'] for v in rows.values()),
        after_release_safety_blocks=sum(v['post_release_safety_blocks'] for v in rows.values()),
        symbols=rows,
        interpretation='Latest Gemini HOLD is a legitimate non-entry; a directional signal is not proof of GPT approval, cost/RR clearance, or order submission. Historical pre-release blocks are not active blocks.')

def _lines(logdir,days):
    result=[]
    for day in days:
        p=Path(logdir)/('trader_'+day.strftime('%Y-%m-%d')+'.log')
        try:
            with p.open('rb') as f:
                n=f.seek(0,2);f.seek(max(0,n-MAX_DAY_BYTES))
                data=f.read().decode('utf-8',errors='replace')
            if n>MAX_DAY_BYTES:data=data.partition('\n')[2]
            result.extend(data.splitlines())
        except OSError:pass
    return result

def from_user_dir(user_dir,*,logdir=None,now=None):
    now=now or datetime.now(KST)
    if now.tzinfo is None:now=now.replace(tzinfo=KST)
    now=now.astimezone(KST)
    base=Path(user_dir)
    release_at=None
    try:
        audit=json.loads((base/'core_flat_reduce_recovery_v33_16_audit.json').read_text())
        if (audit.get('safety_released') is True and
            len(audit.get('verified_passes') or [])==2 and
            all(len(pass_)==6 for pass_ in audit.get('verified_passes'))):
            release_at=datetime.fromisoformat(audit['finished']).astimezone(KST)
    except (OSError,ValueError,TypeError,KeyError):pass
    import core_kill_switch
    active=core_kill_switch.is_active(str(base))
    logroot=Path(logdir) if logdir else Path(__file__).parent/'logs'
    lines=_lines(logroot,[now.date()-timedelta(days=1),now.date()])
    return observed(lines,now=now,release_at=release_at,kill_active=active)
