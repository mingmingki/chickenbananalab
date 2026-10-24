"""v33-23 research replay: candidate early defensive FULL EXIT after confirmed 5m adverse evidence.

Offline only; no private API, no order endpoint, no model calls. Contra-factual
fills are assumed at 5m close plus adverse slippage. Explicitly not live PnL.
"""
import argparse
import collections
import datetime as dt
import json
import math
from pathlib import Path

import ccxt
import pandas as pd

KST=dt.timezone(dt.timedelta(hours=9))
FIVE=300000
LOOKBACK_MS=24*60*60*1000

def to_ms(x):
    t=dt.datetime.fromisoformat(str(x).replace("Z","+00:00"))
    if t.tzinfo is None:
        t=t.replace(tzinfo=KST)
    return int(t.timestamp()*1000)

def load_lifecycles(user_dir, start_at, end_at, symbols):
    active={}
    done=[]
    for line in (Path(user_dir)/"trades_log.jsonl").open(encoding="utf-8"):
        try:r=json.loads(line)
        except (ValueError,TypeError):continue
        if r.get("dry_run") or r.get("strategy_group","core") != "core":continue
        sym=r.get("symbol")
        if sym not in symbols:continue
        ty=r.get("type")
        ms=to_ms(r.get("time")) if r.get("time") else None
        if ms is None:continue
        if ty=="open":
            entry=r.get("price");stop=r.get("sl_price")
            if r.get("side") not in ("long","short") or not entry or not stop:continue
            active[sym]=dict(symbol=sym,side=r["side"],entry=float(entry),
                             stop=float(stop),entry_ms=ms,coin_amount=float(r.get("amount") or 0),
                             events=[])
        elif ty in ("reduce","close") and sym in active:
            item=active[sym]
            if ms < item["entry_ms"]:continue
            item["events"].append(r)
            if ty=="close":
                item["exit_ms"]=ms
                if start_at <= item["entry_ms"] < end_at and item["entry_ms"] < item["exit_ms"]:
                    item["actual_net"]=sum(
                        float(e["okx_net_pnl"]) if e.get("okx_net_pnl") is not None
                        else float(e.get("pnl") or 0)-float(e.get("fee") or 0)
                        for e in item["events"]
                    )
                    item["first_reduce_ms"]=min(
                        (to_ms(e["time"]) for e in item["events"] if e["type"]=="reduce"), default=item["exit_ms"])
                    done.append(item)
                del active[sym]
    return done

def fetch_candles(exchange,symbol,start_ms,end_ms):
    cur=start_ms
    rows={}
    while cur<end_ms:
        result=exchange.fetch_ohlcv(symbol,"5m",since=cur,limit=100)
        if not result:break
        for b in result:
            if start_ms<=b[0] <end_ms:rows[int(b[0])]=b
        next_ms=int(result[-1][0])+FIVE
        if next_ms<=cur:break
        cur=next_ms
    df=pd.DataFrame([rows[k] for k in sorted(rows)],columns=["time_ms","open","high","low","close","volume"])
    df["ema20"]=df["close"].ewm(span=20,adjust=False).mean()
    fast=df["close"].ewm(span=12,adjust=False).mean()
    slow=df["close"].ewm(span=26,adjust=False).mean()
    df["macd"]=fast-slow
    delta=df["close"].diff()
    gains=delta.clip(lower=0).ewm(alpha=1/14,adjust=False).mean()
    losses=(-delta.clip(upper=0)).ewm(alpha=1/14,adjust=False).mean()
    df["rsi"]=100-100/(1+gains/losses.replace(0,float("nan")))
    return df

def candidate(lifecycle,df,threshold,adverse_bars=2,slippage=.0005,fee_each_side=.0005):
    side=lifecycle["side"]
    sign=1 if side=="long" else -1
    entry=lifecycle["entry"]; stop=lifecycle["stop"]
    dist=abs(entry-stop)
    if sign*(entry-stop)<=0 or dist<=0:return None
    for i in range(52,len(df)):
        row=df.iloc[i]
        # strictly: only after a closed bar (never use active or future candle).
        fill_ms=int(row.time_ms)+FIVE
        if fill_ms<=lifecycle["entry_ms"]:continue
        if fill_ms>=lifecycle["exit_ms"] or fill_ms>=lifecycle["first_reduce_ms"]:break
        risk=sign*(entry-float(row.close))/dist
        if risk<threshold:continue
        slice_=df.iloc[i-adverse_bars+1:i+1]
        if len(slice_)!=adverse_bars:continue
        # Adverse close relative to EMA20 over two *confirmed* bars.
        if not all(sign*(float(r.close)-float(r.ema20))<0 for _,r in slice_.iterrows()):
            continue
        # Confirm momentum weakening, not an arbitrary contrary candle.
        if not sign*(float(df.iloc[i].macd)-float(df.iloc[i-1].macd))<0:
            continue
        price=float(row.close)
        fill_price=price*(1+slippage if side=="short" else 1-slippage)
        coin=lifecycle["coin_amount"]
        gross=coin*sign*(fill_price-entry)
        fee=fee_each_side*coin*(entry+fill_price)
        net=gross-fee
        return dict(signal_ms=fill_ms,signal_price=price,entry=entry,stop=stop,
                    loss_r=round(risk,3),assumed_fill=fill_price,
                    hypothetical_net=net,actual_net=lifecycle["actual_net"],
                    delta=net-lifecycle["actual_net"],contracts_coin=coin)
    return None

def compute(user_dir,output,*,start,end,symbols):
    a=to_ms(start);b=to_ms(end)
    completed=load_lifecycles(user_dir,a,b,symbols)
    public=ccxt.okx({"enableRateLimit":True,"timeout":12000})
    candles={}
    for sym in symbols:
        candles[sym]=fetch_candles(public,sym,a-2*LOOKBACK_MS,b)
    rows=[]
    for v in completed:
        record=dict(symbol=v["symbol"],side=v["side"],
                    entry_time=dt.datetime.fromtimestamp(v["entry_ms"]/1000,KST).isoformat(timespec="minutes"),
                    final_time=dt.datetime.fromtimestamp(v["exit_ms"]/1000,KST).isoformat(timespec="minutes"),
                    actual_net=round(v["actual_net"],3),
                    actual_reduce_count=sum(e["type"]=="reduce" for e in v["events"]))
        for threshold in (.3,.35,.4,.5):
            result=candidate(v,candles[v["symbol"]],threshold)
            record[f"threshold_{threshold}"]=result
        rows.append(record)
    totals={}
    for th in (.3,.35,.4,.5):
        matched=[r for r in rows if r[f"threshold_{th}"]]
        totals[str(th)]=dict(
            completed=len(rows), matched=len(matched), winners=sum(r[f"threshold_{th}"]["delta"]>0 for r in matched),
            losers=sum(r[f"threshold_{th}"]["delta"]<0 for r in matched),
            realized_baseline_net=round(sum(r["actual_net"] for r in matched),3),
            estimated_counterfactual_net=round(sum(r[f"threshold_{th}"]["hypothetical_net"] for r in matched),3),
            delta=round(sum(r[f"threshold_{th}"]["delta"] for r in matched),3))
    payload=dict(label="RESEARCH_ONLY_NOT_LIVE",date_range=[start,end],
                 design="5m confirmed price vs EMA20 x2 + adverse MACD + risk threshold; only before first reduce",
                 fill_model="5m close +/- 5bp adverse slippage + 5bp each side fee, no funding",
                 data_quality="Public OKX OHLCV; local CORE journal; no order book/intrabar bids; historical 5m bar close assumed",
                 totals=totals, lifecycles=rows,
                 candles={sym:len(df) for sym,df in candles.items()})
    out=Path(output);out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(payload,ensure_ascii=False,indent=2))
    print(json.dumps(dict(total_lifecycles=len(rows),totals=totals,candles=payload["candles"]),
                     ensure_ascii=False,indent=2))
    for row in rows:
        m=row["threshold_0.35"]
        if m:print(row["entry_time"],row["symbol"],row["side"],"actual",row["actual_net"],
                   "sim",round(m["hypothetical_net"],3),"delta",round(m["delta"],3),
                   "signal",dt.datetime.fromtimestamp(m["signal_ms"]/1000,KST).isoformat(timespec="minutes"))
    print("OUTPUT",out)
if __name__=="__main__":
    ap=argparse.ArgumentParser()
    ap.add_argument("--user-dir",required=True)
    ap.add_argument("--output",required=True)
    ap.add_argument("--start",default="2026-10-16T00:00:00+09:00")
    ap.add_argument("--end",default="2026-10-17T10:00:00+09:00")
    ap.add_argument("--symbols",nargs="+",default=["ETH/USDT:USDT","ADA/USDT:USDT","BTC/USDT:USDT","XRP/USDT:USDT"])
    v=ap.parse_args();compute(v.user_dir,v.output,start=v.start,end=v.end,symbols=v.symbols)
