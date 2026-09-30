"""Read-only entry momentum alignment vs subsequent follow-through shadow."""
from __future__ import annotations

import datetime as dt
import json
import math
import os
from collections import Counter

import pandas as pd

import entry_counterfactual_shadow
import indicators
import jsonl_cache

JOURNAL = "low_follow_through_shadow.jsonl"
TFS = ("3m", "5m", "1h", "4h")
TF_MINUTES = {"3m": 3, "5m": 5, "1h": 60, "4h": 240}
KST = dt.timezone(dt.timedelta(hours=9))


def _time(value):
    if isinstance(value, dt.datetime):
        return value
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00")) if value else None
    except (TypeError, ValueError):
        return None


def _wall(value):
    parsed = _time(value)
    if parsed is None:
        return None
    return parsed if parsed.tzinfo is None else parsed.astimezone(KST).replace(tzinfo=None)

def causal_indicator_frame(rows: list[dict], entry_time: dt.datetime) -> pd.DataFrame:
    entry = _wall(entry_time)
    normalized=[]
    for raw in rows or []:
        open_time=_wall(raw.get("time") or raw.get("timestamp"))
        close_time=_wall(raw.get("close_time"))
        if open_time is None or close_time is None or entry is None or close_time > entry:
            continue
        try:
            normalized.append({
                "timestamp":open_time,"open":float(raw["open"]),"high":float(raw["high"]),
                "low":float(raw["low"]),"close":float(raw["close"]),"volume":float(raw.get("volume") or 0.0),
            })
        except (KeyError,TypeError,ValueError):
            continue
    if not normalized:
        return pd.DataFrame(columns=["timestamp","open","high","low","close","volume"])
    frame=pd.DataFrame(normalized).sort_values("timestamp").drop_duplicates("timestamp", keep="last")
    frame=indicators.add_indicators(frame)
    return frame.dropna(subset=["ema_20","ema_50","macd","macd_signal","rsi_14"])


def classify_state(frame: pd.DataFrame) -> str:
    if frame is None or frame.empty:
        return "unknown"
    row=frame.iloc[-1]
    try:
        close=float(row["close"]); ema20=float(row["ema_20"]); ema50=float(row["ema_50"])
    except (TypeError,ValueError,KeyError):
        return "unknown"
    if not all(math.isfinite(x) for x in (close,ema20,ema50)):
        return "unknown"
    if close > ema20 > ema50: return "bullish"
    if close < ema20 < ema50: return "bearish"
    return "mixed"


def alignment_score(side: str, states: dict) -> dict:
    target="bullish" if side=="long" else "bearish" if side=="short" else None
    available=sum(1 for tf in TFS if states.get(tf) in ("bullish","bearish","mixed"))
    score=sum(1 for tf in TFS if target and states.get(tf)==target)
    return {"score":score,"available":available}

def evaluate_trade(life: dict, states: dict, entry_shadow: dict | None) -> dict:
    trade_id=life.get("trade_id")
    score=alignment_score(str(life.get("side") or "").lower(), states)
    base={"trade_id":trade_id,"symbol":life.get("symbol"),"side":life.get("side"),
          "entry_time":life.get("entry_time"),"states":dict(states or {}),
          "alignment_score":score["score"],"available_tf":score["available"],
          "actual_net":float(life.get("lifecycle_net", life.get("net_pnl")) or 0.0),
          "mode":"shadow_only","live_authority":False,
          "gate_inputs":{"states":dict(states or {}),"alignment_score":score["score"],"available_tf":score["available"]}}
    if score["available"] != len(TFS):
        return {**base,"resolved":False,"reason":"entry_tf_incomplete"}
    h60=((entry_shadow or {}).get("horizons") or {}).get("60") or {}
    try: mfe=float(h60["mfe_r"]); mae=float(h60.get("mae_r") or 0.0)
    except (KeyError,TypeError,ValueError):
        return {**base,"resolved":False,"reason":"entry_follow_through_label_missing"}
    if not (entry_shadow or {}).get("resolved") or not math.isfinite(mfe):
        return {**base,"resolved":False,"reason":"entry_follow_through_label_missing"}
    return {**base,"resolved":True,"reason":"resolved","mfe60_r":mfe,"mae60_r":mae,
            "follow_through_025r":mfe>=0.25,"follow_through_050r":mfe>=0.50}


def _pf(rows):
    nets=[float(r.get("actual_net") or 0.0) for r in rows]
    wins=sum(x for x in nets if x>0); losses=sum(x for x in nets if x<0)
    return wins/abs(losses) if wins>0 and losses<0 else None


def _rate(rows,key):
    return (sum(bool(r.get(key)) for r in rows)/len(rows)*100.0) if rows else None


def summarize_rows(rows: list[dict]) -> dict:
    rows=[r for r in (rows or []) if isinstance(r,dict)]
    resolved=[r for r in rows if r.get("resolved")]
    baseline=sum(float(r.get("actual_net") or 0.0) for r in resolved)
    thresholds={}
    for n in range(1,5):
        passed=[r for r in resolved if int(r.get("alignment_score") or 0)>=n]
        blocked=[r for r in resolved if int(r.get("alignment_score") or 0)<n]
        kept=sum(float(r.get("actual_net") or 0.0) for r in passed)
        thresholds[f"score>={n}"]={"passed_count":len(passed),"blocked_count":len(blocked),
            "baseline_actual_net":baseline,"filtered_actual_net":kept,
            "blocked_actual_net":sum(float(r.get("actual_net") or 0.0) for r in blocked),
            "net_improvement_if_blocked":kept-baseline,"profit_factor":_pf(passed),
            "follow_through_025_rate":_rate(passed,"follow_through_025r"),
            "follow_through_050_rate":_rate(passed,"follow_through_050r")}
    score_counts=Counter(int(r.get("alignment_score") or 0) for r in resolved)
    score_buckets={}
    for score in range(5):
        bucket=[r for r in resolved if int(r.get("alignment_score") or 0)==score]
        score_buckets[str(score)]={"count":len(bucket),
            "actual_net":sum(float(r.get("actual_net") or 0.0) for r in bucket),
            "profit_factor":_pf(bucket),
            "follow_through_025_rate":_rate(bucket,"follow_through_025r"),
            "follow_through_050_rate":_rate(bucket,"follow_through_050r")}
    return {"mode":"shadow_only","live_authority":False,"sample_count":len(rows),
            "resolved_count":len(resolved),"unresolved_count":len(rows)-len(resolved),
            "unresolved_reasons":dict(Counter(r.get("reason") for r in rows if not r.get("resolved"))),
            "baseline_actual_net":baseline,"score_counts":dict(sorted(score_counts.items())),
            "score_buckets":score_buckets,"thresholds":thresholds}


def _path(user_dir):
    return os.path.join(user_dir,JOURNAL)


def _read_latest(user_dir):
    latest={}; path=_path(user_dir)
    if not os.path.exists(path): return latest
    with open(path,encoding="utf-8") as f:
        for line in f:
            try: row=json.loads(line)
            except json.JSONDecodeError: continue
            if isinstance(row,dict) and row.get("trade_id"): latest[str(row["trade_id"])]=row
    return latest


def _append(user_dir,row):
    path=_path(user_dir); os.makedirs(user_dir,exist_ok=True)
    with jsonl_cache.get_path_lock(path):
        with open(path,"a",encoding="utf-8") as f:
            f.write(json.dumps(row,ensure_ascii=False,allow_nan=False,separators=(",",":"))+"\n")
            f.flush(); os.fsync(f.fileno())


def refresh(user_dir: str, lifecycles: list[dict], feature_fetcher, limit: int=150) -> int:
    existing=_read_latest(user_dir)
    entry_by_trade={str(r.get("trade_id")):r for r in entry_counterfactual_shadow._read_latest(user_dir).values() if r.get("trade_id")}
    rows=sorted(list(lifecycles or []),key=lambda r:_wall(r.get("entry_time")) or dt.datetime.min)[-max(0,int(limit)):]
    created=0
    for life in rows:
        tid=str(life.get("trade_id") or "")
        if not tid or tid in existing: continue
        entry=_wall(life.get("entry_time"))
        if entry is None or not life.get("symbol"): continue
        try: states=feature_fetcher(life["symbol"], entry)
        except Exception: continue
        out=evaluate_trade(life,states,entry_by_trade.get(tid))
        out["observed_at"]=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        _append(user_dir,out); existing[tid]=out; created+=1
    return created


def summary(user_dir: str, limit: int=150) -> dict:
    rows=list(_read_latest(user_dir).values())
    rows.sort(key=lambda r:_wall(r.get("entry_time")) or dt.datetime.min, reverse=True)
    return summarize_rows(rows[:max(0,int(limit))])

def make_okx_feature_fetcher(cfg):
    import okx_client
    clients={}; cache={}

    def _bars(symbol,tf,entry):
        minutes=TF_MINUTES[tf]
        key=(symbol,tf)
        batches=cache.setdefault(key,[])
        warmup=minutes*70
        for batch in batches:
            if batch['start'] <= entry-dt.timedelta(minutes=warmup) and batch['end'] >= entry:
                return batch['rows']
        client=clients.setdefault(symbol,okx_client.OkxClient(symbol,cfg))
        since_time=entry-dt.timedelta(minutes=minutes*250)
        since=int(since_time.replace(tzinfo=KST).timestamp()*1000)
        raw=client.exchange.fetch_ohlcv(symbol,tf,since=since,limit=300)
        rows=[]
        for item in raw or []:
            if not isinstance(item,(list,tuple)) or len(item)<6: continue
            opened=dt.datetime.fromtimestamp(float(item[0])/1000.0,tz=dt.timezone.utc).astimezone(KST).replace(tzinfo=None)
            rows.append({'time':opened.isoformat(),'close_time':(opened+dt.timedelta(minutes=minutes)).isoformat(),
                         'open':float(item[1]),'high':float(item[2]),'low':float(item[3]),'close':float(item[4]),'volume':float(item[5])})
        if rows:
            batches.append({'start':_wall(rows[0]['time']),'end':_wall(rows[-1]['close_time']),'rows':rows})
        return rows

    def fetch(symbol,entry_time):
        entry=_wall(entry_time); states={}
        for tf in TFS:
            frame=causal_indicator_frame(_bars(symbol,tf,entry),entry)
            states[tf]=classify_state(frame)
        return states
    return fetch
