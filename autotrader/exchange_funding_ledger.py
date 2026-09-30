"""Durable read-only OKX funding ledger (account bill type=8)."""
from __future__ import annotations
import json, math, os, time
import ccxt
import process_lock
import exchange_fee_ledger

STATE_FILE="exchange_funding_state.json"
LOCK_KEY="exchange_funding_state"
SCHEMA_VERSION=1
PAGE_LIMIT=100
MAX_PAGES=20
PAGE_DELAY_SECONDS=0.35


def _path(user_dir): return os.path.join(user_dir,STATE_FILE)

def _finite(v):
    try:x=float(v)
    except (TypeError,ValueError): return None
    return x if math.isfinite(x) else None

def _read(user_dir):
    try:
        with open(_path(user_dir),encoding="utf-8") as f:data=json.load(f)
    except FileNotFoundError:return {}
    if not isinstance(data,dict): raise ValueError("funding state must be an object")
    return data

def _save(user_dir,state): process_lock.save_json_atomic(_path(user_dir),state)

def normalize_bill(raw):
    if str(raw.get("type"))!="8": return None
    bill_id=str(raw.get("billId") or "")
    ts_ms=int(raw.get("ts") or 0)
    ccy=str(raw.get("ccy") or "")
    if not bill_id or ts_ms<=0 or not ccy: return None
    value=_finite(raw.get("balChg"))
    error=None
    if ccy!="USDT": value=None; error="non_usdt_funding_ccy"
    elif value is None: error="invalid_funding_balance_change"
    return {"bill_id":bill_id,"ts_ms":ts_ms,"inst_type":str(raw.get("instType") or "").upper(),
            "inst_id":str(raw.get("instId") or ""),"ccy":ccy,"sub_type":str(raw.get("subType") or ""),
            "funding_pnl_usdt":value,"valuation_error":error}

def compute_summary(events,*,start_ms,last_refresh_ms,complete=True,last_error=None):
    rows=[e for e in events if int(e.get("ts_ms") or 0)>=int(start_ms)]
    valued=[e for e in rows if _finite(e.get("funding_pnl_usdt")) is not None]
    unvalued=[e for e in rows if _finite(e.get("funding_pnl_usdt")) is None]
    vals=[float(e["funding_pnl_usdt"]) for e in valued]
    paid=-sum(v for v in vals if v<0)
    received=sum(v for v in vals if v>0)
    return {"schema_version":SCHEMA_VERSION,"complete":bool(complete and not unvalued),
            "last_error":last_error,"start_ms":int(start_ms),"last_refresh_ms":last_refresh_ms,
            "event_count":len(valued),"unvalued_count":len(unvalued),
            "funding_paid_usdt":paid,"funding_received_usdt":received,
            "funding_pnl_usdt":sum(vals)}

def _call(method,params):
    last=None
    for delay in (0.0,0.75,1.5,3.0):
        if delay: time.sleep(delay)
        try:return method(params)
        except ccxt.RateLimitExceeded as exc:last=exc
    raise last

def _fetch_pages(method,*,start_ms,stop_on_known=None):
    out=[]; seen=set(); cursor=None
    for _ in range(MAX_PAGES):
        params={"type":"8","limit":str(PAGE_LIMIT)}
        if cursor: params["after"]=cursor
        response=_call(method,params) or {}; rows=response.get("data") or []
        if not rows: break
        hit=False
        for raw in rows:
            bid=str(raw.get("billId") or ""); ts=int(raw.get("ts") or 0)
            if stop_on_known and bid in stop_on_known: hit=True
            if bid and bid not in seen and ts>=int(start_ms): seen.add(bid); out.append(raw)
        oldest=min(int(r.get("ts") or 0) for r in rows)
        if hit or oldest<int(start_ms) or len(rows)<PAGE_LIMIT: break
        nxt=str(rows[-1].get("billId") or "")
        if not nxt or nxt==cursor: break
        cursor=nxt; time.sleep(PAGE_DELAY_SECONDS)
    return out

def refresh(user_dir,exchange,*,start_ms=None,now_ms=None):
    now_ms=int(time.time()*1000) if now_ms is None else int(now_ms)
    if start_ms is None: start_ms=exchange_fee_ledger.derive_start_ms(user_dir)
    if start_ms is None:
        return compute_summary([],start_ms=0,last_refresh_ms=None,complete=False,last_error="funding_coverage_start_unknown")
    with process_lock.locked(user_dir,LOCK_KEY):
        state=_read(user_dir)
        if int(state.get("start_ms") or -1)!=int(start_ms):
            state={"schema_version":SCHEMA_VERSION,"start_ms":int(start_ms),"events":[],"backfill_complete":False,"last_refresh_ms":None,"last_error":None}
        events={str(e.get("bill_id")):dict(e) for e in state.get("events") or [] if e.get("bill_id")}
        error=None; complete=True
        try:
            method=exchange.private_get_account_bills_archive if not state.get("backfill_complete") else exchange.private_get_account_bills
            raw=_fetch_pages(method,start_ms=int(start_ms),stop_on_known=(set(events) if state.get("backfill_complete") else None))
            for r in raw:
                e=normalize_bill(r)
                if e: events[e["bill_id"]]=e
            state["backfill_complete"]=True
        except Exception as exc:
            complete=False; error=f"{type(exc).__name__}: {exc}"[:500]
        state["events"]=sorted(events.values(),key=lambda e:(int(e["ts_ms"]),e["bill_id"]))
        state["last_refresh_ms"]=now_ms; state["last_error"]=error; _save(user_dir,state)
        return compute_summary(state["events"],start_ms=int(start_ms),last_refresh_ms=now_ms,complete=complete,last_error=error)

def cached_summary(user_dir,*,start_ms=None):
    with process_lock.locked(user_dir,LOCK_KEY):
        state=_read(user_dir)
        if start_ms is None: start_ms=state.get("start_ms") or exchange_fee_ledger.derive_start_ms(user_dir)
        if start_ms is None:
            return compute_summary([],start_ms=0,last_refresh_ms=None,complete=False,last_error="funding_coverage_start_unknown")
        stored=state.get("start_ms")
        if stored is None or int(start_ms)<int(stored):
            return compute_summary([],start_ms=int(start_ms),last_refresh_ms=state.get("last_refresh_ms"),complete=False,last_error="funding_ledger_not_initialized")
        return compute_summary(state.get("events") or [],start_ms=int(start_ms),last_refresh_ms=state.get("last_refresh_ms"),
                               complete=not bool(state.get("last_error")) and bool(state.get("backfill_complete")),last_error=state.get("last_error"))
