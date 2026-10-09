"""CORE GPT panel and Telegram routing keep Candidate C separate."""
from types import SimpleNamespace
import json
import logging

import pytest

import core_entry_events
import gpt_shadow_log
import notification_policy
import candidate_c_trader_adapter
import telegram_notify


def _cfg(tmp_path):
    return SimpleNamespace(user_dir=str(tmp_path), logger=logging.getLogger('test'),
                           TELEGRAM_BOT_TOKEN='mock', TELEGRAM_CHAT_ID='mock-chat')


def _core_event(status, symbol='BTC/USDT:USDT', reason=None, **kw):
    return dict(engine='CORE', symbol=symbol, decision_id='decision-'+status+symbol,
                status=status, reason=reason, side='long',
                **kw)


@pytest.mark.parametrize('status,reason,expected',[
    ('FILLED','protected_fill_confirmed',True),
    ('FILLED','unprotected_fill',False),
    ('GPT_APPROVED','approved',False),
    ('GPT_WAIT','wait',False),
    ('GPT_REJECT','rejected',False),
    ('GPT_ERROR','api_error',True),
    ('TIMEOUT_BYPASS','confirmed_timeout',True),
    ('NO_RESPONSE_BYPASS','empty_response',True),
    ('ORDER_PENDING','unresolved_fill',True),
    ('ORDER_FAILED','exchange_rejected',True),
    ('LOCAL_BLOCKED','cooldown',False),
    ('LOCAL_BLOCKED','core_kill_switch',True),
])
def test_core_telegram_only_filled_entry_and_gate_or_order_issues(status, reason, expected):
    assert notification_policy.should_send_core_telegram(
        _core_event(status,reason=reason)) is expected


@pytest.mark.parametrize('symbol', ['DOGE/USDT:USDT','SOL/USDT:USDT'])
def test_candidate_excluded_from_core_gate_and_outbox(symbol, tmp_path, monkeypatch):
    e=_core_event('GPT_ERROR',symbol=symbol,reason='api_error')
    assert not notification_policy.should_send_core_telegram(e)
    assert core_entry_events.record(_cfg(tmp_path), e)
    assert core_entry_events.recent(str(tmp_path))[0]['symbol']==symbol
    with core_entry_events._db(str(tmp_path)) as db:
        assert db.execute('SELECT delivery FROM events').fetchone()[0]=='SUPPRESSED'
    seen=[]
    monkeypatch.setattr(telegram_notify,'send',lambda *_:seen.append(1))
    core_entry_events.deliver_pending(_cfg(tmp_path))
    assert seen==[]


def test_old_queued_noncore_events_suppressed_before_send(tmp_path, monkeypatch):
    e=_core_event('GPT_ERROR',symbol='SOL/USDT:USDT',reason='api_error')
    assert core_entry_events.record(_cfg(tmp_path),e)
    with core_entry_events._db(str(tmp_path)) as db:
        db.execute("UPDATE events SET delivery='QUEUED'")
    sent=[]
    monkeypatch.setattr(telegram_notify,'send',lambda *args:sent.append(args))
    core_entry_events.deliver_pending(_cfg(tmp_path))
    assert not sent
    with core_entry_events._db(str(tmp_path)) as db:
        assert db.execute('SELECT delivery FROM events').fetchone()[0]=='SUPPRESSED'


def test_core_panel_and_summary_filter_old_candidate_records(tmp_path):
    rows=[
        dict(mode='entry_gate',symbol='DOGE/USDT:USDT',decision_id='old-doge',
             gpt_decision='reject',gate_result='blocked_reject'),
        dict(mode='entry_gate',symbol='SOL/USDT:USDT',decision_id='old-sol',
             gpt_decision='wait',gate_result='blocked_wait'),
        dict(mode='entry_gate',symbol='ETH/USDT:USDT',decision_id='old-eth',
             gpt_decision='approve_now',gate_result='approved'),
    ]
    (tmp_path/'gpt_shadow_log.jsonl').write_text(
        ''.join(json.dumps(row)+'\n' for row in rows))
    recent=gpt_shadow_log.recent_by_mode(str(tmp_path),'entry_gate',100)
    assert [r['symbol'] for r in recent]==['ETH/USDT:USDT']
    summary=gpt_shadow_log.summary(str(tmp_path))
    assert summary['gate_count']==1 and summary['gate_approved_count']==1
    assert summary['gate_blocked_wait_count']==0
    assert summary['gate_blocked_reject_count']==0


def test_candidate_c_sends_only_entry_and_closures(monkeypatch,tmp_path):
    sent=[]
    cfg=_cfg(tmp_path)
    monkeypatch.setattr(telegram_notify,'send',lambda c,text:sent.append(text))
    events=[
        dict(event_type='entry',symbol='DOGE/USDT:USDT',intent_id='1',side='long',price=1,contracts=2),
        dict(event_type='reduce',symbol='SOL/USDT:USDT',intent_id='2',side='long',price=2,contracts=1),
        dict(event_type='close',symbol='DOGE/USDT:USDT',intent_id='3',side='long',price=2,contracts=2),
        dict(event_type='entry_gate',symbol='DOGE/USDT:USDT',intent_id='4'),
        dict(event_type='GPT_ERROR',symbol='SOL/USDT:USDT',intent_id='5'),
        dict(event_type='entry',symbol='BTC/USDT:USDT',intent_id='6'),
    ]
    state=SimpleNamespace(notification_events=events,notification_store=None)
    candidate_c_trader_adapter._dispatch_candidate_c_notifications(cfg,state)
    assert len(sent)==3
    assert any('진입 DOGE' in msg for msg in sent)
    assert any('부분감축 SOL' in msg for msg in sent)
    assert any('청산 DOGE' in msg for msg in sent)
    assert not any('GPT' in msg for msg in sent)


def test_confirmed_core_entry_is_delivered_once_and_normal_stages_are_silent(tmp_path,monkeypatch):
    cfg=_cfg(tmp_path);sent=[]
    monkeypatch.setattr(telegram_notify,'send',
                        lambda _,msg:sent.append(msg) or {'ok':True,'message_id':123})
    for status in ('GPT_APPROVED','ORDER_SUBMITTED','FILLED'):
        assert core_entry_events.record(cfg,_core_event(
            status,reason='protected_fill_confirmed' if status=='FILLED' else 'normal'))
    core_entry_events.deliver_pending(cfg)
    assert len(sent)==1 and 'FILLED' in sent[0]
    assert len(core_entry_events.recent(str(tmp_path)))==3
