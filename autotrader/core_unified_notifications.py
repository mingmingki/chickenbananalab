"""Durable confirmed-fill notifications, dispatched outside the trading thread.
Delivery is at-least-once: an ambiguous network timeout can repeat a fill ID.
"""
import json
import os
import logging
import threading
import time
from datetime import datetime
from core_unified_accounting import KST
from core_unified_policy import number

class Notifications:
    def __init__(self,store,symbol,send,clock):
        self.store,self.symbol,self.send,self.clock=store,symbol,send,clock
        with store.lock:
            store.db.executescript('''
              CREATE TABLE IF NOT EXISTS telegram_baseline(symbol TEXT PRIMARY KEY,row_id INTEGER NOT NULL);
              CREATE TABLE IF NOT EXISTS telegram_fills(row_id INTEGER PRIMARY KEY,status TEXT NOT NULL,
                next_ms INTEGER NOT NULL,attempts INTEGER NOT NULL DEFAULT 0);
            ''')
        with store.transaction():
            # Only first activation skips historical rows. Subsequent restarts recover unsent fills.
            store.db.execute('INSERT OR IGNORE INTO telegram_baseline VALUES(?,(SELECT COALESCE(MAX(rowid),0) FROM accounting))',(symbol,))
    def tick(self):
        now=self.clock();s=self.store
        with s.transaction():
            baseline=s.db.execute('SELECT row_id FROM telegram_baseline WHERE symbol=?',(self.symbol,)).fetchone()[0]
            rows=s.db.execute('''SELECT a.rowid,a.payload,COALESCE(n.attempts,0) FROM accounting a
              LEFT JOIN telegram_fills n ON n.row_id=a.rowid
              LEFT JOIN intents i ON i.id=a.intent_id
              WHERE a.rowid>? AND (i.id IS NULL OR i.status='complete')
              AND (n.row_id IS NULL OR (n.status!='sent' AND n.next_ms<=?)) ORDER BY a.rowid''',(baseline,now)).fetchall()
            chosen=next(((rid,json.loads(payload),attempts) for rid,payload,attempts in rows if json.loads(payload).get('symbol')==self.symbol),None)
            if not chosen:return
            rid,r,attempts=chosen
            # The lease survives worker restart and prevents concurrent duplicate delivery.
            s.db.execute('INSERT OR REPLACE INTO telegram_fills VALUES(?,?,?,?)',(rid,'sending',now+30000,attempts+1))
        try:
            kind={'ENTRY':'🟢 진입','ADD':'🔵 추가 진입','REDUCE':'🟡 부분 감축','EXIT':'🔴 청산'}[r['kind']]
            side={'long':'롱','short':'숏'}[r['side']]
            qty=number(r['qty'],positive=True);price=number(r['price'],positive=True)
            coin=qty*number(r['contract_size'],positive=True)
            stamp=datetime.fromtimestamp(r['filled_ms']/1000,KST).strftime('%m/%d %H:%M:%S')
            message=(f"[CORE] {kind} · {r['symbol']} · {side}\n"
                     f"확인된 체결분: {qty}계약 ({coin}코인)\n체결가: {price} USDT\n"
                     f"체결 시각: {stamp} KST\n체결 ID: {r['trade_id']}")
            self.send(message)
        except Exception as exc:
            with s.transaction():
                s.db.execute("UPDATE telegram_fills SET status='retry',next_ms=? WHERE row_id=?",(self.clock()+min(900000,60000*2**min(attempts,4)),rid))
            # Never log exception messages/URLs: bot tokens can be embedded there.
            logging.getLogger(__name__).warning('[CORE telegram] 전송 실패 · 재시도 대기 · %s',type(exc).__name__)
        else:
            with s.transaction():s.db.execute("UPDATE telegram_fills SET status='sent' WHERE row_id=?",(rid,))
            logging.getLogger(__name__).info('[CORE telegram] 체결 알림 전송 완료 · %s · %s',self.symbol,r['kind'])

_workers={}
_workers_lock=threading.Lock()

def start_notifications(cfg,store,symbol):
    with _workers_lock:
        key=(os.path.realpath(cfg.user_dir),symbol)
        if key in _workers:return _workers[key]
        worker=_start_notifications(cfg,store,symbol)
        if worker is not None:_workers[key]=worker
        return worker

def _start_notifications(cfg,store,symbol):
    if not getattr(cfg,'TELEGRAM_BOT_TOKEN',None) or not getattr(cfg,'TELEGRAM_CHAT_ID',None):return None
    import telegram_notify
    stopped=threading.Event()
    n=Notifications(store,symbol,lambda text:telegram_notify.send(cfg,text),lambda:int(time.time()*1000))
    def run():
        while not stopped.is_set():
            try:n.tick()
            except Exception as exc:
                logging.getLogger(__name__).warning('[CORE telegram] 알림 처리 대기 · %s',type(exc).__name__)
            stopped.wait(2)
    threading.Thread(target=run,name='core-telegram-'+symbol.split('/')[0],daemon=True).start()
    return stopped
