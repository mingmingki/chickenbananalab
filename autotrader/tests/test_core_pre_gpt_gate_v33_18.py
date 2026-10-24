from datetime import datetime,timedelta,timezone
from pathlib import Path
import json,sqlite3

import pytest
import trader
import core_pre_gpt_gate_audit_v33_18 as a
from test_core_ai_budget_gate_20261004 import FakeState,frames

KST=timezone(timedelta(hours=9))
UTC=timezone.utc
NOW=datetime(2026,10,16,4,25,tzinfo=KST)
SYM='BTC/USDT:USDT'

def phase(side,origin,kind='first_range_break'):
    return dict(status='ok',side=side,phase='established',
      origin_closed_at=origin,event_key=f'{side}|{origin}|{kind}')

def test_new_confirmed_established_setup_calls_gemini_even_during_budget():
    now=NOW.astimezone(UTC)
    first=trader._core_ai_call_gate(FakeState(),SYM,frames(),{}, {},None,
       now=now,entry_timing=phase('short','2026-10-15T18:35:00+00:00'))
    newer=trader._core_ai_call_gate(FakeState(first['next_memory']),SYM,
       frames(),{}, {},None,now=now+timedelta(minutes=5),
       entry_timing=phase('long','2026-10-15T19:20:00+00:00'))
    assert newer['call_ai'] is True
    assert newer['reason']=='entry_setup_changed'

def test_same_established_setup_is_stable_and_budgeted():
    now=NOW.astimezone(UTC)
    timing=phase('long','2026-10-15T19:15:00+00:00')
    first=trader._core_ai_call_gate(FakeState(),SYM,frames(),{}, {},None,
       now=now,entry_timing=timing)
    second=trader._core_ai_call_gate(FakeState(first['next_memory']),SYM,
       frames(),{}, {},None,now=now+timedelta(minutes=5),entry_timing=timing)
    assert second['call_ai'] is False
    assert second['reason']=='stable_within_budget'

def test_unknown_setup_never_overrides_cost_budget():
    now=NOW.astimezone(UTC)
    first=trader._core_ai_call_gate(FakeState(),SYM,frames(),{}, {},None,
       now=now,entry_timing=phase('short','2026-10-15T18:35:00+00:00'))
    invalid=dict(status='unknown',event_key=None,phase='unknown')
    second=trader._core_ai_call_gate(FakeState(first['next_memory']),SYM,
       frames(),{}, {},None,now=now+timedelta(minutes=5),entry_timing=invalid)
    assert second['call_ai'] is False

def sample(tmp_path,entries):
    p=tmp_path/'core_entry_events.sqlite3'
    with sqlite3.connect(p) as c:
        c.execute('CREATE TABLE events (seq INTEGER PRIMARY KEY,payload TEXT)')
        for i,x in enumerate(entries,1):
            c.execute('INSERT INTO events VALUES (?,?)',(i,json.dumps(x)))
    logs=tmp_path/'logs';logs.mkdir()
    (logs/'trader_2026-10-16.log').write_text(
        '2026-10-16 04:20:00,000 [INFO] trader.u: [BTC/USDT:USDT] CORE_AI_BUDGET_SKIP reason=stable_within_budget age=310s move_atr=0.123\n'
        '2026-10-16 04:25:00,000 [INFO] trader.u: [ETH/USDT:USDT] CORE_AI_BUDGET_CALL reason=entry_setup_changed position=flat\n')
    return logs

def event(engine,status,reason,gate):
    return dict(engine=engine,status=status,reason=reason,
        gate_processing=gate,symbol=SYM,time=NOW.isoformat())

def test_core_only_audit_excludes_candidate_c(tmp_path):
    logdir=sample(tmp_path,[
        event('CORE','LOCAL_BLOCKED','cooldown','NOT_REQUESTED'),
        event('CORE','LOCAL_BLOCKED','post_cost_rr_below_minimum','NOT_REQUESTED'),
        event('CORE','LOCAL_BLOCKED','post_gpt_price_drift','GPT_APPROVED'),
        event('Candidate C','LOCAL_BLOCKED','setup_direction_mismatch_or_no_atr','NOT_REQUESTED'),
        event('CORE','GPT_WAIT',None,'GPT_WAIT'),
    ])
    r=a.read(tmp_path,now=NOW,log_dir=logdir)
    assert r['status']=='KNOWN'
    assert r['core_event_rows']==4
    assert r['core_local_blocks']==3
    assert r['core_before_gpt_blocks']==2
    assert r['pre_gpt_reason_counts']['cooldown']==1
    assert 'setup_direction_mismatch_or_no_atr' not in r['pre_gpt_reason_counts']
    assert r['ai_budget_last_120m']['SKIP:stable_within_budget']==1
    assert r['ai_budget_last_120m']['CALL:entry_setup_changed']==1

def test_absent_evidence_cannot_masquerade_as_zero(tmp_path):
    r=a.read(tmp_path,now=NOW)
    assert r['status']=='UNKNOWN'
