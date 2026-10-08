import importlib
import importlib.util
import logging
from types import SimpleNamespace
import pytest
import telegram_notify


def events():
    assert importlib.util.find_spec('core_entry_events'), 'durable CORE events missing'
    return importlib.import_module('core_entry_events')


def cfg(tmp_path):
    return SimpleNamespace(user_dir=str(tmp_path),logger=logging.getLogger('notification-test'),
        TELEGRAM_BOT_TOKEN='offline-token',TELEGRAM_CHAT_ID='offline-configured-chat')


def event(status='TIMEOUT_BYPASS'):
    return dict(decision_id='d-1',symbol='ETH/USDT:USDT',side='short',gemini_action='short',gemini_confidence=.8,
        gpt_raw_result='TIMEOUT',gpt_reason='timeout',gate_processing='TIMEOUT_BYPASS',timeout_bypass=True,
        status=status,reason='confirmed_core_gpt_entry_timeout_config_enabled',quantity_coin=2,
        contracts=20,leverage=5,sl_price=104,tp_price=92,order_id='ex-1')


def test_formatter_keeps_raw_timeout_gate_and_order_separate():
    formatter=getattr(telegram_notify,'format_core_entry_event',None)
    assert callable(formatter), 'structured CORE formatter missing'
    text=formatter(event())
    assert 'GPT 응답 없음 — 설정에 따른 예외 통과' in text
    assert 'TIMEOUT_BYPASS' in text and 'TIMEOUT' in text and 'approve_now' not in text
    assert 'd-1' in text and 'SHORT' in text and 'KST' in text
    assert 'SL=104' in text and 'TP=92' in text

@pytest.mark.parametrize('status',['GPT_APPROVED','GPT_WAIT','GPT_REJECT','GPT_ERROR','TIMEOUT_BYPASS','LOCAL_BLOCKED','ORDER_SUBMITTED','ORDER_PENDING','FILLED','ORDER_FAILED'])
def test_each_result_has_one_durable_notification(tmp_path,monkeypatch,status):
    e=events();c=cfg(tmp_path)
    sent=[]
    monkeypatch.setattr(telegram_notify,'send',lambda cfg,text:sent.append(text) or {'ok':True,'message_id':321})
    assert e.record(c,event(status))
    assert not e.record(c,event(status))
    e.deliver_pending(c)
    importlib.reload(e)
    e.deliver_pending(c)
    assert len(sent)==1
    assert e.recent(c.user_dir)[0]['notification_status']=='SENT'


def test_followup_status_is_connected_to_same_decision(tmp_path,monkeypatch):
    e=events();c=cfg(tmp_path);sent=[]
    monkeypatch.setattr(telegram_notify,'send',lambda cfg,text:sent.append(text) or {'ok':True,'message_id':1})
    for status in ['GPT_APPROVED','ORDER_SUBMITTED','FILLED']:
        e.record(c,event(status))
    e.deliver_pending(c)
    assert len(sent)==3 and all('d-1' in text for text in sent)


def test_notification_timeout_does_not_escape_or_duplicate_on_restart(tmp_path,monkeypatch):
    import requests
    e=events();c=cfg(tmp_path);sent=[]
    def failure(cfg,text):
        sent.append(text)
        raise requests.Timeout('secret-like request URL must not be logged')
    monkeypatch.setattr(telegram_notify,'send',failure)
    e.record(c,event())
    e.deliver_pending(c)
    importlib.reload(e)
    e.deliver_pending(c)
    assert len(sent)==1
    assert e.recent(c.user_dir)[0]['notification_status']=='DELIVERY_UNKNOWN'


def test_telegram_http_200_ok_false_is_a_failure(tmp_path,monkeypatch):
    c=cfg(tmp_path)
    response=SimpleNamespace(raise_for_status=lambda:None,json=lambda:{'ok':False,'error_code':400})
    monkeypatch.setattr(telegram_notify.requests,'post',lambda *a,**k:response)
    with pytest.raises(Exception):
        telegram_notify.send(c,'offline')


def test_confirmed_429_retries_at_most_once_and_stays_bounded(tmp_path,monkeypatch):
    e=events();c=cfg(tmp_path);sent=[];delays=[]
    monkeypatch.setattr(e.time,'sleep',lambda seconds:delays.append(seconds))
    def limited(cfg,text):
        sent.append(text)
        if len(sent)==1:
            raise telegram_notify.TelegramDeliveryError('offline',definite_rejection=True,retry_after=2)
        return {'ok':True,'message_id':1}
    monkeypatch.setattr(telegram_notify,'send',limited)
    e.record(c,event());e.deliver_pending(c);e.deliver_pending(c)
    assert len(sent)==2 and delays==[2]
    assert e.recent(c.user_dir)[0]['notification_status']=='SENT'


def test_uncertain_send_on_process_death_is_not_resent(tmp_path,monkeypatch):
    e=events();c=cfg(tmp_path);sent=[]
    e.record(c,event())
    with e._db(c.user_dir) as db:
        db.execute("UPDATE events SET delivery='SENDING'")
    monkeypatch.setattr(telegram_notify,'send',lambda *a:sent.append(a))
    importlib.reload(e);e.deliver_pending(c)
    assert not sent
