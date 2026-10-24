"""v33-24 offline counterfactual profit-protection replay.

Compares existing v33-23 observed actual CORE realized lifecycle net against
ONE hypothetical 25%-of-initial-quantity earlier take-profit at the first 5m
confirmed-bar MFE/giveback trigger. No live orders, no user credentials, no
backtest optimization. Computes only on completed lifecycles with reconciled
close prices; incomplete lifecycles explicitly excluded.
"""
import json, sys, datetime as dt, math
from pathlib import Path
import argparse
import core_exit_replay_v33_23 as old
KST=dt.timezone(dt.timedelta(hours=9))
BAR_MS=300000

def hypothetic_early_reduce(trade, df, arm, giveback, *, fraction=.25, friction=.001):
    side=trade["side"]; sign=1 if side=="long" else -1
    entry=float(trade["entry"]); sl=float(trade["stop"])
    coin=float(trade["coin_amount"])
    if not (coin>0 and entry>0 and sign*(entry-sl)>0):return None,"invalid_entry_or_stop"
    events=trade["events"]
    exits=[]
    for ev in events:
        px=ev.get("close_price")
        qty=ev.get("amount")
        if px is None or qty is None:
            return None,"missing_realized_exit_price_or_quantity"
        try:px,qty=float(px),float(qty)
        except (TypeError,ValueError):return None,"invalid_realized_exit"
        if not all(math.isfinite(v) and v>0 for v in (px,qty)):
            return None,"invalid_realized_exit"
        exits.append((px,qty))
    total_qty=sum(q for px,q in exits)
    if not total_qty:return None,"missing_realized_exit_quantity"
    actual_vwap=sum(px*q for px,q in exits)/total_qty
    # Only compare earlier than the actual first reduction. Thus a simulated
    # fraction replaces part of the initial exit distribution, not a second
    # reduction after already reduced quantity.
    peak=0.
    seen_entry=False
    for i in range(len(df)):
        bar=df.iloc[i]
        closed=int(bar.time_ms)+BAR_MS
        if closed<=trade["entry_ms"]:continue
        if closed>=min(trade["first_reduce_ms"],trade["exit_ms"]):break
        seen_entry=True
        favorable=float(bar.high if side=="long" else bar.low)
        peak=max(peak, sign*(favorable-entry)/abs(entry-sl))
        close=float(bar.close)
        current=sign*(close-entry)/abs(entry-sl)
        returned=peak-current
        # Require net positive beyond assumed friction to avoid turning
        # "profit protection" into an additional loss.
        net_positive=sign*(close-entry)/entry > friction
        if peak+1e-9<arm or returned+1e-9<giveback or not net_positive:
            continue
        adverse_px=close*(1-.0005 if side=="long" else 1+.0005)
        # Existing realized EXIT fees are already in actual_net. The 10-bp
        # incremental friction is conservatively charged on diverted slice.
        delta=coin*fraction*sign*(adverse_px-actual_vwap)-coin*fraction*entry*friction
        return dict(signal_ms=closed,arm=arm,giveback=giveback,peak_r=round(peak,4),
                    current_r=round(current,4),original_entry_coin=coin,
                    actual_lifecycle_exit_vwap=actual_vwap,
                    assumed_early_fill=adverse_px,delta_usdt=round(delta,5),
                    actual_net=round(trade["actual_net"],5),
                    hypothetical_net=round(trade["actual_net"]+delta,5)),None
    return None,"no_signal_before_first_reduce" if seen_entry else "not_observed"

def main(args):
    a=old.to_ms(args.start);b=old.to_ms(args.end)
    symbols=args.symbols
    trades=old.load_lifecycles(args.user_dir,a,b,symbols)
    exchange=__import__("ccxt").okx({"enableRateLimit":True,"timeout":12000})
    candles={sym:old.fetch_candles(exchange,sym,a-2*old.LOOKBACK_MS,b) for sym in symbols}
    policies=((.4,.2),(.5,.25),(.75,.25))
    records=[]
    for item in trades:
        rec=dict(symbol=item["symbol"],side=item["side"],
                 start=dt.datetime.fromtimestamp(item["entry_ms"]/1000,KST).isoformat(timespec="minutes"),
                 finish=dt.datetime.fromtimestamp(item["exit_ms"]/1000,KST).isoformat(timespec="minutes"),
                 actual_net=round(item["actual_net"],5),
                 count_reduces=sum(e["type"]=="reduce" for e in item["events"]))
        for arm,give in policies:
            key=f"{arm:.2f}_{give:.2f}"
            simulated,reason=hypothetic_early_reduce(item,candles[item["symbol"]],arm,give)
            rec[key]={"simulation":simulated,"reason":reason}
        records.append(rec)
    totals={}
    for arm,give in policies:
        key=f"{arm:.2f}_{give:.2f}"
        matched=[rec[key]["simulation"] for rec in records if rec[key]["simulation"]]
        reason={}
        for rec in records:
            rs=rec[key]["reason"]
            if rs:reason[rs]=reason.get(rs,0)+1
        totals[key]=dict(completed=len(records),triggered=len(matched),
                         delta_usdt=round(sum(p["delta_usdt"] for p in matched),4),
                         wins=sum(p["delta_usdt"]>0 for p in matched),
                         losses=sum(p["delta_usdt"]<0 for p in matched),
                         actual_net=round(sum(p["actual_net"] for p in matched),4),
                         simulated_net=round(sum(p["hypothetical_net"] for p in matched),4),
                         excluded_or_untriggered=reason)
    output=dict(kind="v33-24 offline-only, no order authority",period=[args.start,args.end],
                cost_model="5m close with adverse 5bp slippage + extra 10bp notional friction on diverted 25%",
                limits="No intrabar executable proof, excludes live/incomplete/unpriced closes, no funding adjustment, first reduce only",
                total_completed=len(records),candles={s:len(v) for s,v in candles.items()},
                policies=totals,records=records)
    out=Path(args.output);out.write_text(json.dumps(output,indent=2,ensure_ascii=False))
    print(json.dumps(totals,ensure_ascii=False,indent=2))
    for rec in records:
        event=rec["0.50_0.25"]["simulation"]
        if event:
            print("TRIGGER",rec["start"],rec["symbol"],rec["side"],"actual",rec["actual_net"],
                  "delta",event["delta_usdt"],"hyp",event["hypothetical_net"],
                  "peak",event["peak_r"],"current",event["current_r"])
    print("EVIDENCE",out)

if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--user-dir",required=True)
    parser.add_argument("--output",required=True)
    parser.add_argument("--start",default="2026-10-16T00:00:00+09:00")
    parser.add_argument("--end",default="2026-10-17T10:00:00+09:00")
    parser.add_argument("--symbols",nargs="+",default=["ETH/USDT:USDT","ADA/USDT:USDT","BTC/USDT:USDT","XRP/USDT:USDT"])
    main(parser.parse_args())
