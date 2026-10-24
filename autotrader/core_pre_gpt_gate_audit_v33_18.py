"""v33-18 read-only CORE pre-GPT veto audit; never changes order authority."""
from collections import Counter
from datetime import datetime,timedelta,timezone
from pathlib import Path
import json,re,sqlite3

KST=timezone(timedelta(hours=9))
BUDGET=re.compile(r"\[(BTC|ETH|XRP|ADA)/USDT:USDT\] CORE_AI_BUDGET_(CALL|SKIP) reason=([^ ]+)")
STAMP=re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),")
GATE_REASONS=('cooldown','core_kill_switch','post_cost_rr_below_minimum','protected_stop_stale_setup')

def read(user_dir,*,now=None,log_dir=None):
    now=now or datetime.now(KST)
    if now.tzinfo is None:now=now.replace(tzinfo=KST)
    now=now.astimezone(KST)
    since=now-timedelta(days=7)
    home=Path(user_dir)
    try:
        uri=f"file:{home/'core_entry_events.sqlite3'}?mode=ro"
        with sqlite3.connect(uri,uri=True,timeout=2) as con:
            raw=con.execute('SELECT payload FROM events ORDER BY seq DESC LIMIT 10000').fetchall()
        rows=[]
        for (value,) in raw:
            event=json.loads(value)
            if (event.get('engine') or 'CORE')!='CORE':continue
            stamp=event.get('time')
            if not stamp:continue
            t=datetime.fromisoformat(stamp)
            if t.tzinfo is None:t=t.replace(tzinfo=KST)
            if since<=t.astimezone(KST)<=now:rows.append(event)
        local=[r for r in rows if r.get('status')=='LOCAL_BLOCKED']
        before=[r for r in local if r.get('gate_processing')=='NOT_REQUESTED']
        stages=Counter(r.get('gate_processing') or 'UNKNOWN' for r in local)
        reasons=Counter(r.get('reason') or 'unknown' for r in before)
        current_core=[r for r in rows if r.get('symbol') in
                      ('BTC/USDT:USDT','ETH/USDT:USDT','XRP/USDT:USDT','ADA/USDT:USDT')]
        budget=Counter()
        root=Path(log_dir) if log_dir else Path(__file__).parent/'logs'
        for day in (now.date()-timedelta(days=1),now.date()):
            path=root/f"trader_{day.isoformat()}.log"
            if not path.exists():continue
            with path.open('rb') as f:
                size=f.seek(0,2)
                f.seek(max(0,size-2_000_000))
                lines=f.read().decode('utf-8',errors='replace').splitlines()
            if size>2_000_000:lines=lines[1:]
            for line in lines:
                m=STAMP.match(line);b=BUDGET.search(line)
                if not m or not b:continue
                t=datetime.strptime(m.group(1),'%Y-%m-%d %H:%M:%S').replace(tzinfo=KST)
                if now-timedelta(minutes=120)<=t<=now:
                    budget[(b.group(2),b.group(3))]+=1
        return dict(status='KNOWN',generated_at=now.isoformat(),
            window_days=7,core_event_rows=len(rows),core_local_blocks=len(local),
            core_before_gpt_blocks=len(before),by_gate=dict(stages),
            pre_gpt_reason_counts=dict(reasons),
            core_active_symbols_pre_gpt=sum(r.get('symbol') in
                ('BTC/USDT:USDT','ETH/USDT:USDT','XRP/USDT:USDT','ADA/USDT:USDT')
                for r in before),
            ai_budget_last_120m={f'{key[0]}:{key[1]}':v for key,v in budget.items()},
            expected_pre_gpt_gates=list(GATE_REASONS),
            note='Event rows are pipeline phases, not distinct trade opportunities. Candidate C excluded. GPT not requested also includes deliberate safety stops.')
    except (sqlite3.Error,OSError,ValueError,TypeError,KeyError) as exc:
        return dict(status='UNKNOWN',reason=type(exc).__name__)
