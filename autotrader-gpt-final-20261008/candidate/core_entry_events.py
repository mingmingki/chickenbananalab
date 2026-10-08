"""Durable CORE entry outcomes and a bounded asynchronous Telegram outbox.

Telegram has no idempotency key. A send timeout/crash after dispatch therefore
remains DELIVERY_UNKNOWN and is never blindly resent. Confirmed 429 rejection
alone can retry (once, no more than five seconds apart).
"""
import datetime
from contextlib import contextmanager
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import sqlite3
import threading
import time

import telegram_notify

_LOCK=threading.Lock()
_WORKERS={}
KST=datetime.timezone(datetime.timedelta(hours=9))


@contextmanager
def _db(user_dir):
    Path(user_dir).mkdir(parents=True,exist_ok=True)
    path=Path(user_dir)/'core_entry_events.sqlite3'
    db=sqlite3.connect(path,timeout=1)
    os.chmod(path,0o600)
    db.row_factory=sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('PRAGMA synchronous=FULL')
    db.executescript('''
        CREATE TABLE IF NOT EXISTS events (
            seq INTEGER PRIMARY KEY, event_key TEXT UNIQUE NOT NULL,
            payload TEXT NOT NULL, delivery TEXT NOT NULL, attempts INTEGER DEFAULT 0,
            updated REAL NOT NULL, message_id TEXT);
        CREATE TABLE IF NOT EXISTS entry_orders (
            decision_id TEXT PRIMARY KEY, symbol TEXT NOT NULL, client_order_id TEXT UNIQUE NOT NULL,
            payload TEXT NOT NULL, status TEXT NOT NULL, updated REAL NOT NULL);
    ''')
    try:
        with db:
            yield db
    finally:
        db.close()


def _diagnostic_json(value):
    """Invalid numeric evidence stays unknown; it must not erase a blocked event."""
    if isinstance(value,dict):return {str(k):_diagnostic_json(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)):return [_diagnostic_json(v) for v in value]
    if isinstance(value,float):return value if math.isfinite(value) else None
    if value is None or isinstance(value,(str,int,bool)):return value
    return None


def record(cfg,event):
    """Persist exactly one event per decision and phase; failure never affects trading."""
    try:
        row=_diagnostic_json(dict(event))
        if not row.get('decision_id') or not row.get('status'): return False
        row.setdefault('time',datetime.datetime.now(KST).isoformat(timespec='seconds'))
        phase_reason=str(row.get('reason') or '') if row['status'] in ('LOCAL_BLOCKED','ORDER_PENDING') else ''
        key=hashlib.sha256((str(row.get('engine') or 'CORE')+'|'+str(row['decision_id'])+'|'+row['status']+'|'+phase_reason).encode()).hexdigest()
        delivery=('QUEUED' if getattr(cfg,'TELEGRAM_BOT_TOKEN','') and getattr(cfg,'TELEGRAM_CHAT_ID','') else 'NOT_CONFIGURED')
        with _db(cfg.user_dir) as db:
            db.execute('BEGIN IMMEDIATE')
            if (row.get('engine') or 'CORE')=='CORE':
                legacy_key=hashlib.sha256((str(row['decision_id'])+'|'+row['status']).encode()).hexdigest()
                legacy=db.execute('SELECT payload FROM events WHERE event_key=?',(legacy_key,)).fetchone()
                if legacy and (not phase_reason or json.loads(legacy['payload']).get('reason')==row.get('reason')):
                    return False
            if row['status'] in ('FILLED','ORDER_FAILED'):
                receipt=db.execute('SELECT payload FROM entry_orders WHERE decision_id=?',(row['decision_id'],)).fetchone()
                if receipt:
                    payload=json.loads(receipt['payload'])
                    payload.update({k:row[k] for k in ('order_id','reason','lifecycle_id','exchange_code') if k in row})
                    db.execute('UPDATE entry_orders SET status=?,payload=?,updated=? WHERE decision_id=?',
                        (row['status'],json.dumps(payload,allow_nan=False),time.time(),row['decision_id']))
            return db.execute('INSERT OR IGNORE INTO events(event_key,payload,delivery,updated) VALUES (?,?,?,?)',
                (key,json.dumps(row,ensure_ascii=False,allow_nan=False),delivery,time.time())).rowcount == 1
    except Exception as exc:
        (cfg.logger or logging.getLogger(__name__)).warning('CORE_ENTRY_EVENT_FAILED error_type=%s',type(exc).__name__)
        return False


def recent(user_dir,limit=100):
    with _db(user_dir) as db:
        rows=db.execute('SELECT payload,delivery,message_id FROM events ORDER BY seq DESC LIMIT ?',
                        (max(1,min(int(limit),1000)),)).fetchall()
    return [dict(json.loads(r['payload']),notification_status=r['delivery'],telegram_message_id=r['message_id']) for r in rows]


def deliver_pending(cfg):
    """Called by a daemon worker; no network operation runs on a trading thread."""
    if not getattr(cfg,'TELEGRAM_BOT_TOKEN','') or not getattr(cfg,'TELEGRAM_CHAT_ID',''): return
    while True:
        with _db(cfg.user_dir) as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute("SELECT * FROM events WHERE delivery='QUEUED' ORDER BY seq LIMIT 1").fetchone()
            if row is None: return
            db.execute("UPDATE events SET delivery='SENDING',attempts=attempts+1,updated=? WHERE seq=?",
                       (time.time(),row['seq']))
        try:
            reply=telegram_notify.send(cfg,telegram_notify.format_core_entry_event(json.loads(row['payload'])))
            if not isinstance(reply,dict) or not reply.get('ok') or not reply.get('message_id'):
                raise RuntimeError('telegram_ack_missing')
            delivery='SENT';message_id=str(reply.get('message_id') or '')
        except Exception as exc:
            # An indeterminate acknowledgement must never be retried: duplicate risk.
            definite=getattr(exc,'definite_rejection',False)
            retry=getattr(exc,'retry_after',None)
            if definite and retry is not None and row['attempts'] < 1 and 0<=retry<=5:
                time.sleep(retry)
                delivery='QUEUED'
            else:
                delivery='FAILED' if definite else 'DELIVERY_UNKNOWN'
            message_id=None
            (cfg.logger or logging.getLogger(__name__)).warning(
                'CORE_ENTRY_TELEGRAM_FAILED event=%s status=%s error_type=%s',
                row['event_key'],delivery,type(exc).__name__)
        with _db(cfg.user_dir) as db:
            db.execute('UPDATE events SET delivery=?,updated=?,message_id=? WHERE seq=?',
                       (delivery,time.time(),message_id,row['seq']))


def kick(cfg):
    if not getattr(cfg,'TELEGRAM_BOT_TOKEN','') or not getattr(cfg,'TELEGRAM_CHAT_ID',''): return
    user_dir=os.path.realpath(cfg.user_dir)
    with _LOCK:
        current=_WORKERS.get(user_dir)
        if current and current.is_alive(): return
        def work():
            try:
                with _db(user_dir) as db:
                    # A previous worker may have dispatched before process death.
                    db.execute("UPDATE events SET delivery='DELIVERY_UNKNOWN' WHERE delivery='SENDING'")
                while True:
                    try:
                        deliver_pending(cfg)
                    except Exception as exc:
                        (cfg.logger or logging.getLogger(__name__)).warning('CORE_ENTRY_OUTBOX_FAILED error_type=%s',type(exc).__name__)
                    threading.Event().wait(2)
            except Exception as exc:
                (cfg.logger or logging.getLogger(__name__)).warning('CORE_ENTRY_OUTBOX_FAILED error_type=%s',type(exc).__name__)
        worker=threading.Thread(target=work,name='core-entry-telegram',daemon=True)
        _WORKERS[user_dir]=worker
        worker.start()


def reserve_order(user_dir,decision_id,symbol,payload):
    """CAS before an exchange write. Any existing or unresolved receipt blocks resend."""
    client_id='cg'+hashlib.sha256(str(decision_id).encode()).hexdigest()[:28]
    with _db(user_dir) as db:
        db.execute('BEGIN IMMEDIATE')
        old=db.execute('SELECT * FROM entry_orders WHERE decision_id=?',(decision_id,)).fetchone()
        if old: return False,dict(old,payload=json.loads(old['payload']))
        unresolved=db.execute("SELECT * FROM entry_orders WHERE symbol=? AND status IN ('RESERVED','ORDER_SUBMITTED','ORDER_PENDING','FILLED_UNJOURNALED') ORDER BY updated LIMIT 1",(symbol,)).fetchone()
        if unresolved: return False,dict(unresolved,payload=json.loads(unresolved['payload']))
        db.execute('INSERT INTO entry_orders VALUES (?,?,?,?,?,?)',
                   (decision_id,symbol,client_id,json.dumps(payload,allow_nan=False),'RESERVED',time.time()))
    return True,dict(decision_id=decision_id,symbol=symbol,client_order_id=client_id,payload=payload,status='RESERVED')


def update_order(user_dir,decision_id,status,**details):
    with _db(user_dir) as db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT payload FROM entry_orders WHERE decision_id=?',(decision_id,)).fetchone()
        if row is None: raise ValueError('missing_order_reservation')
        payload=dict(json.loads(row['payload']),**details)
        db.execute('UPDATE entry_orders SET status=?,payload=?,updated=? WHERE decision_id=?',
                   (status,json.dumps(payload,allow_nan=False),time.time(),decision_id))


def order_receipt(user_dir,decision_id):
    with _db(user_dir) as db:
        row=db.execute('SELECT * FROM entry_orders WHERE decision_id=?',(decision_id,)).fetchone()
    return dict(row,payload=json.loads(row['payload'])) if row else None


def pending_order(user_dir,symbol):
    with _db(user_dir) as db:
        row=db.execute("SELECT * FROM entry_orders WHERE symbol=? AND status IN ('RESERVED','ORDER_SUBMITTED','ORDER_PENDING','FILLED_UNJOURNALED') ORDER BY updated LIMIT 1",(symbol,)).fetchone()
    return dict(row,payload=json.loads(row['payload'])) if row else None
