import json
from types import SimpleNamespace
from pathlib import Path
import sqlite3

import core_trade_telegram_outbox as core
import candidate_c_notification_delivery as candidate
import candidate_c_trader_adapter as adapter
import telegram_notify


def cfg(tmp_path):
    return SimpleNamespace(user_dir=str(tmp_path),TELEGRAM_BOT_TOKEN='test',
                           TELEGRAM_CHAT_ID='test',logger=SimpleNamespace(
                               info=lambda *a,**k:None,warning=lambda *a,**k:None))


def row(t='open',execution_id='cx'):
    return dict(strategy_group='core',type=t,symbol='BTC/USDT:USDT',
                side='short',amount=2.25,price=81610.3,close_price=81520.0,
                time='2026-10-15T12:15:00',reason='stop_loss',
                okx_net_pnl=-.14,execution_id=execution_id,dry_run=False)


def put(tmp_path,rows):
    (tmp_path/'trades_log.jsonl').write_text(''.join(json.dumps(x)+'\n' for x in rows))


def status(tmp_path):
    with sqlite3.connect(tmp_path/'core_trade_telegram_outbox.sqlite3') as db:
        return db.execute('select status,message_id from items').fetchall()


def test_core_existing_history_suppressed_and_new_fill_sent(tmp_path,monkeypatch):
    c=cfg(tmp_path)
    put(tmp_path,[row()])
    assert core.ingest(tmp_path)==0  # migration baseline, no historical flood
    put(tmp_path,[row(),row('close','cx2')])
    assert core.ingest(tmp_path)==1
    sent=[]
    monkeypatch.setattr(telegram_notify,'send',lambda _c,msg:sent.append(msg) or {'ok':True,'message_id':721})
    core.dispatch(c)
    assert len(sent)==1 and '전량청산 완료' in sent[0]
    assert '순손익' in sent[0] and '체결알림 ID' in sent[0]
    assert status(tmp_path)==[('sent','721')]
    core.dispatch(c)
    assert len(sent)==1
    assert core.ingest(tmp_path)==0


def test_core_entry_is_coin_not_contract_and_unknown_fee(tmp_path):
    msg=core._fmt(row(),'A123')
    assert '진입 코인 수량' in msg and 'SL:' in msg
    assert '체결알림 ID' in msg
    close=row('close')
    close['okx_net_pnl']=None
    close['pnl']=None
    assert '정산 확인 중' in core._fmt(close,'X')


def test_core_ambiguous_failure_queues_bounded_replay(tmp_path,monkeypatch):
    c=cfg(tmp_path)
    put(tmp_path,[row()])
    core.ingest(tmp_path)
    put(tmp_path,[row(),row('reduce','new1')])
    core.ingest(tmp_path)
    calls=[]
    def fail_once(_c,msg):
        calls.append(msg)
        if len(calls)==1:raise TimeoutError()
        return {'ok':True,'message_id':2}
    monkeypatch.setattr(telegram_notify,'send',fail_once)
    core.dispatch(c)
    assert status(tmp_path)[0][0]=='retry'
    with sqlite3.connect(tmp_path/'core_trade_telegram_outbox.sqlite3') as db:
        db.execute("UPDATE items SET next_at=0")
        db.commit()
    core.dispatch(c)
    assert status(tmp_path)==[('sent','2')]
    assert len(calls)==2
    assert calls[0]==calls[1]


def event(intent):
    return dict(event_type='entry',symbol='DOGE/USDT:USDT',intent_id=intent,
                side='short',price=.0812,contracts=1,amount_coin=1000,
                stop_price=.084,target_price=.077)


def test_candidate_successful_ack_is_persisted_across_restart(tmp_path,monkeypatch):
    s=candidate.NotificationDeliveryStore(str(tmp_path),'DOGE/USDT:USDT')
    s.initialize()
    c=cfg(tmp_path)
    assert s.enqueue(event('id1'))
    state=SimpleNamespace(notification_store=s,notification_events=[])
    sent=[]
    monkeypatch.setattr(telegram_notify,'send',lambda _c,msg:sent.append(msg) or {'ok':True,'message_id':3001})
    adapter._dispatch_candidate_c_notifications(c,state)
    assert s.delivery_summary().get('sent')==1
    assert len(sent)==1 and '진입 완료' in sent[0] and '체결알림 ID' in sent[0]
    again=candidate.NotificationDeliveryStore(str(tmp_path),'DOGE/USDT:USDT')
    assert again.claim_pending()==[]
    adapter._dispatch_candidate_c_notifications(c,SimpleNamespace(notification_store=again,notification_events=[]))
    assert len(sent)==1


def test_candidate_timeout_is_retryable_and_past_attempted_not_replayed(tmp_path,monkeypatch):
    c=cfg(tmp_path)
    s=candidate.NotificationDeliveryStore(str(tmp_path),'DOGE/USDT:USDT')
    s.initialize()
    s.enqueue(event('future'))
    claimed=s.claim_pending(now=1000)
    assert [x['intent_id'] for x in claimed]==['future']
    s.finish_delivery(event('future'),error_type='TimeoutError',now=1001)
    assert s.claim_pending(now=1002)==[]
    assert s.claim_pending(now=2000)==[event('future')]
    s.finish_delivery(event('future'),message_id=31,now=2001)
    assert s.delivery_summary().get('sent')==1
    assert s.claim_pending(now=2500)==[]
    # Historical pre-fix attempted records are fundamentally ambiguous.
    with candidate.process_lock.locked(str(tmp_path),s.lock_key):
        d=s._load();d['records']['entry:historical']=dict(status='attempted',event=event('historical'))
        candidate.process_lock.save_json_atomic(s.path,d)
    assert s.claim_pending(now=3000)==[]


def test_candidate_rejected_send_does_not_drop_event(tmp_path,monkeypatch):
    c=cfg(tmp_path)
    s=candidate.NotificationDeliveryStore(str(tmp_path),'SOL/USDT:USDT')
    s.initialize()
    e=dict(event('id2'),symbol='SOL/USDT:USDT')
    s.enqueue(e)
    def fail(*args):
        raise TimeoutError('telegram timeout')
    monkeypatch.setattr(telegram_notify,'send',fail)
    adapter._dispatch_candidate_c_notifications(c,SimpleNamespace(notification_store=s,notification_events=[]))
    assert s.delivery_summary().get('retry')==1
