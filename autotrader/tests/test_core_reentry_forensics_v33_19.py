"""Read-only historical block attribution does not invent distinct trades or profits."""
import json,sqlite3
from datetime import datetime,timedelta,timezone
import core_reentry_forensics_v33_19 as audit
from test_core_profit_stop_reentry_v33_6 import proven
import trade_log
KST=timezone(timedelta(hours=9))
NOW=datetime(2026,10,16,8,20,tzinfo=KST)

def db(tmp_path,rows):
    with sqlite3.connect(tmp_path/'core_entry_events.sqlite3') as c:
        c.execute('CREATE TABLE events (seq INTEGER PRIMARY KEY,payload TEXT)')
        for i,row in enumerate(rows,1):
            c.execute('INSERT INTO events VALUES (?,?)',(i,json.dumps(row)))

def row(reason,t):
    return dict(time=t.isoformat(),symbol='BTC/USDT:USDT',
       side='short',engine='CORE',status='LOCAL_BLOCKED',
       reason=reason,gate_processing='NOT_REQUESTED')

def test_forensic_current_favorable_stop_remains_authoritative(tmp_path):
    at=NOW-timedelta(minutes=40)
    stop=proven(at)
    trade_log._append(str(tmp_path),stop)
    db(tmp_path,[row('protected_stop_stale_setup',NOW),
                 row('cooldown',at+timedelta(minutes=10))])
    r=audit.audit(tmp_path,now=NOW)
    assert r['status']=='KNOWN'
    assert r['focus_event_rows']==2
    assert r['breakdown']['protected_stop_stale_setup|CURRENT_VERIFIED_FAVORABLE_STOP_REQUIRES_FRESH_SETUP']==1
    assert r['breakdown']['cooldown|CURRENT_VERIFIED_FAVORABLE_STOP_30M_LOCK']==1
    assert not r['suspect_cases']

def test_forensic_newer_ai_close_supersedes_old_stop_without_overriding_ai_cooldown(tmp_path):
    at=NOW-timedelta(minutes=20)
    trade_log._append(str(tmp_path),proven(at))
    ai=dict(type='close',symbol='BTC/USDT:USDT',side='long',
            reason='position_ai_close_all',time=(NOW-timedelta(minutes=7)).isoformat())
    trade_log._append(str(tmp_path),ai)
    db(tmp_path,[row('cooldown',NOW-timedelta(minutes=2))])
    r=audit.audit(tmp_path,now=NOW)
    assert r['breakdown']['cooldown|LATEST_AI_CLOSE_OWN_COOLDOWN']==1
    assert r['focus_event_rows']==1
