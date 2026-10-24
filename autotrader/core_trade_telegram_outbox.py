"""Durable, journal-sourced CORE trade-fill Telegram outbox.

- Baseline at first launch: do not flood the chat with historical fills.
- Scan appended, confirmed journal events independently of in-memory callbacks.
- SQLite atomic cursor+enqueue; retry with one consistent event ID.
- Telegram has no idempotency key: a timeout can produce a duplicate. We
  explicitly label the reference so the operator can recognize a replay.
- No exchange/strategy calls. Notification failures never affect orders.
"""
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone

LOG=logging.getLogger(__name__)
KST=timezone(timedelta(hours=9))
_WORKERS={}
_LOCK=threading.Lock()
TICK_SECONDS=3

def _db(user_dir):
    p=Path(user_dir)/'core_trade_telegram_outbox.sqlite3'
    db=sqlite3.connect(p,timeout=8)
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('PRAGMA busy_timeout=8000')
    db.execute('''CREATE TABLE IF NOT EXISTS meta
      (id INTEGER PRIMARY KEY CHECK(id=1), next_line INTEGER NOT NULL)''')
    db.execute('''CREATE TABLE IF NOT EXISTS items(
      identity TEXT PRIMARY KEY, line_index INTEGER NOT NULL,
      payload TEXT NOT NULL, status TEXT NOT NULL,
      attempts INTEGER NOT NULL DEFAULT 0, next_at REAL NOT NULL DEFAULT 0,
      message_id TEXT, updated REAL NOT NULL DEFAULT 0)''')
    db.commit()
    return db

def _number(x):
    if isinstance(x,bool):return None
    try:
        n=float(x)
        return n if math.isfinite(n) else None
    except (ValueError,TypeError,OverflowError):return None

def _candidate_core_row(row):
    return (isinstance(row,dict) and row.get('strategy_group')=='core' and
            row.get('type') in ('open','reduce','close') and
            row.get('dry_run') is not True and
            row.get('symbol') and row.get('side') in ('long','short'))

def _fmt(row,identity):
    typ=row['type']
    action={'open':'🟢 진입 완료','reduce':'🟡 부분청산 체결','close':'🔴 전량청산 완료'}[typ]
    direction={'long':'LONG · 롱','short':'SHORT · 숏'}[row['side']]
    when=row.get('time') or '체결시각 미확인'
    price=row.get('price') if typ=='open' else row.get('close_price')
    px=_number(price)
    amt=_number(row.get('amount'))
    def n(x):
        y=_number(x)
        return f'{y:.8g}' if y is not None else '확인 필요'
    msg=[f'{action} | CORE {row["symbol"].split("/")[0]}',
         f'방향: {direction}',
         f'체결 시각(KST): {when}',
         f'체결가: {n(px)} USDT',
         (f'진입 코인 수량: {n(amt)} coin' if typ=='open' else f'청산 계약 수량: {n(amt)} contracts')]
    if typ=='open':
        msg.extend([f'SL: {n(row.get("sl_price"))} · TP: {n(row.get("tp_price"))}'])
    else:
        net=_number(row.get('okx_net_pnl'))
        if net is None:
            gross=_number(row.get('pnl'))
            fee=_number(row.get('fee'))
            if gross is not None and fee is not None:
                net=gross-fee
        msg += [f'실현 순손익: {net:+.3f} USDT' if net is not None else '실현 순손익: 정산 확인 중',
                f'사유: {row.get("reason") or "미분류"}']
        if typ=='reduce':
            msg += ['잔여 수량: 다음 포지션 갱신에서 확인']
    msg.append(f'체결알림 ID: {identity[:16]}')
    return '\n'.join(msg)

def ingest(user_dir):
    file=Path(user_dir)/'trades_log.jsonl'
    if not file.exists():return 0
    # Do not consume a concurrently incomplete last JSON line.
    raw=file.read_text(encoding='utf-8')
    lines=raw.splitlines()
    complete=len(lines) if raw.endswith('\n') else len(lines)-1
    db=_db(user_dir)
    try:
        with db:
            cursor=db.execute('SELECT next_line FROM meta WHERE id=1').fetchone()
            if cursor is None:
                # Existing trade history predates this notification rollout.
                db.execute('INSERT INTO meta(id,next_line) VALUES(1,?)',(complete,))
                return 0
            pos=cursor[0]
            if pos>complete:
                LOG.error('CORE_TRADE_ALERT_JOURNAL_TRUNCATED cursor=%s lines=%s',pos,complete)
                return 0
            count=0
            for index in range(pos,complete):
                try:
                    row=json.loads(lines[index])
                except (ValueError,TypeError):
                    break
                if _candidate_core_row(row):
                    identity=hashlib.sha256(
                        f'{index}|{row.get("time")}|{row.get("type")}|{row.get("symbol")}|{row.get("execution_id")}'.encode()).hexdigest()
                    db.execute('INSERT OR IGNORE INTO items(identity,line_index,payload,status,updated) VALUES(?,?,?,?,?)',
                               (identity,index,json.dumps(row,ensure_ascii=False,allow_nan=False),'pending',time.time()))
                    count+=1
                db.execute('UPDATE meta SET next_line=? WHERE id=1',(index+1,))
            return count
    finally:
        db.close()

def dispatch(cfg):
    if not getattr(cfg,'TELEGRAM_BOT_TOKEN',None) or not getattr(cfg,'TELEGRAM_CHAT_ID',None):
        return
    import telegram_notify
    db=_db(cfg.user_dir)
    try:
        now=time.time()
        with db:
            # A process died during network I/O. At-least-once replay after lease.
            db.execute("UPDATE items SET status='retry',next_at=? WHERE status='sending' AND next_at<=?",
                       (now,now))
            row=db.execute("""SELECT identity,payload,attempts FROM items
              WHERE status IN ('pending','retry') AND next_at<=?
              ORDER BY line_index LIMIT 1""",(now,)).fetchone()
            if row is None:return
            identity,payload,attempts=row
            db.execute("""UPDATE items SET status='sending',attempts=?,next_at=?,updated=?
              WHERE identity=?""",(attempts+1,now+45,now,identity))
        trade=json.loads(payload)
        try:
            reply=telegram_notify.send(cfg,_fmt(trade,identity))
            if not isinstance(reply,dict) or reply.get('ok') is not True or not reply.get('message_id'):
                raise RuntimeError('telegram_ack_missing')
        except Exception as exc:
            definite=getattr(exc,'definite_rejection',False)
            # Never retry permanent credential/recipient rejection.
            # For ambiguous timeouts retry at most three times, with stable ID.
            if definite and getattr(exc,'retry_after',None) is None:
                status='failed'
            elif attempts+1>=4:
                status='unconfirmed'
            else:
                status='retry'
            delay=min(600,30*2**min(attempts,4))
            with db:
                db.execute('UPDATE items SET status=?,next_at=?,updated=? WHERE identity=?',
                           (status,time.time()+delay,time.time(),identity))
            LOG.warning('CORE_TRADE_ALERT_DELIVERY_FAIL status=%s error_type=%s id=%s',
                        status,type(exc).__name__,identity[:16])
        else:
            with db:
                db.execute('UPDATE items SET status=?,message_id=?,updated=? WHERE identity=?',
                           ('sent',str(reply['message_id']),time.time(),identity))
            LOG.info('CORE_TRADE_ALERT_SENT type=%s symbol=%s id=%s',
                     trade.get('type'),trade.get('symbol'),identity[:16])
    finally:
        db.close()

def tick(cfg):
    ingest(cfg.user_dir)
    dispatch(cfg)

def start(cfg):
    if not getattr(cfg,'TELEGRAM_BOT_TOKEN',None) or not getattr(cfg,'TELEGRAM_CHAT_ID',None):
        return
    key=os.path.realpath(cfg.user_dir)
    with _LOCK:
        old=_WORKERS.get(key)
        if old and old.is_alive():return
        def work():
            while True:
                try:tick(cfg)
                except Exception as exc:
                    LOG.warning('CORE_TRADE_ALERT_WORKER_ERROR type=%s',type(exc).__name__)
                time.sleep(TICK_SECONDS)
        worker=threading.Thread(target=work,name='core-trade-telegram',daemon=True)
        _WORKERS[key]=worker
        worker.start()
