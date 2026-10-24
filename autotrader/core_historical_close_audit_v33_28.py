"""v33-28: bounded GET-only historical close evidence survey.

No orders and no changes to trades_log.jsonl. Captures exchange fills in
pages, groups by *real* order ID, tests side/size/time/price and exact terminal
order status. Unowned temporal matches are RESEARCH CANDIDATES ONLY.
"""
from __future__ import annotations
from collections import Counter,defaultdict
from datetime import datetime,timedelta,timezone
from decimal import Decimal
from pathlib import Path
import hashlib,json,math

import core_exit_outcome_join_v33_25 as journal
import core_legacy_close_proof_v33_27 as original
import core_extra_owned_receipt_v33_27 as owned
import core_close_recovery_status_v33_27 as report

KST=timezone(timedelta(hours=9))
PAGE_LIMIT=100
MAX_PAGES=16
BEFORE_MS=120000
AFTER_MS=60000
ALLOWED_SECONDS=180
SWAPS=frozenset(("BTC/USDT:USDT","ETH/USDT:USDT",
                 "XRP/USDT:USDT","ADA/USDT:USDT"))


def stamp(row):
    at=journal.dt_kst(row.get("time"))
    return int(at.timestamp()*1000) if at else None


def unverified_closes(rows,root):
    embedded=original.load_valid_overlay(rows,root)
    appended=owned.load_valid_patch(rows,root)
    if set(embedded).intersection(appended):
        raise ValueError("proof_source_conflict")
    adjusted=owned.apply_in_memory(original.apply_in_memory(rows,embedded),appended)
    lives,_=journal.lifecycles(adjusted)
    needs=[l for l in lives if l["realized_status"]=="CLOSED_UNVERIFIED"]
    by_key=defaultdict(list)
    for i,row in enumerate(adjusted):
        if (row.get("type")=="close" and row.get("strategy_group")=="core"
                and row.get("dry_run") is False):
            by_key[(row.get("symbol"),row.get("side"),stamp(row))].append(i)
    lookup=[];problems=Counter()
    for l in needs:
        key=(l["symbol"],l["side"],int(l["close_time"].timestamp()*1000))
        positions=by_key.get(key) or []
        if len(positions)!=1:
            problems["missing_or_duplicate_exact_journal_close"]+=1;continue
        idx=positions[0]
        src=rows[idx]
        lookup.append(dict(index=idx,symbol=l["symbol"],side=l["side"],time_ms=key[2],
                           journal_time=src.get("time"),reason=src.get("reason"),
                           journal_close_price=src.get("close_price"),
                           amount=src.get("amount"),
                           explicit_order_id=(src.get("close_order_reference") or {}).get("order_id"),
                           row_sha256=original.row_hash(src)))
    return lookup,dict(problems),len(needs)


def normalize(raw):
    info=raw.get("info") or {}
    order=raw.get("order") or info.get("ordId")
    tid=raw.get("id") or info.get("tradeId")
    if not order or not tid:return None
    try:
        qty=Decimal(str(raw.get("amount")))
        price=Decimal(str(raw.get("price")))
        at=int(raw.get("timestamp"))
        if qty<=0 or price<=0 or at<=0 or not qty.is_finite() or not price.is_finite():return None
    except (ValueError,TypeError,OverflowError):return None
    if raw.get("side") not in ("buy","sell"):return None
    return dict(order_id=str(order),trade_id=str(tid),timestamp_ms=at,
                side=raw.get("side"),quantity=str(qty),price=str(price))


def paged_trades(exchange,symbol,since_ms,until_ms,*,max_pages=MAX_PAGES):
    """Bounded date-keyset pagination. Fail closed on full non-advancing pages."""
    records={}
    since=since_ms
    diagnostics=dict(page_count=0,trades_seen=0,duplicate_fill_ids=0,
                     earliest_ms=None,latest_ms=None,truncated=False,
                     status="OK")
    for page in range(max_pages):
        payload=exchange.fetch_my_trades(symbol,since=since,limit=PAGE_LIMIT)
        diagnostics["page_count"]+=1
        if not isinstance(payload,list):
            diagnostics["status"]="INVALID_EXCHANGE_RESPONSE";break
        if not payload:break
        actual_ts=[]
        for item in payload:
            normal=normalize(item)
            if not normal:continue
            actual_ts.append(normal["timestamp_ms"])
            if normal["timestamp_ms"]<since_ms or normal["timestamp_ms"]>until_ms:continue
            key=(normal["order_id"],normal["trade_id"])
            if key in records:
                if records[key]!=normal:
                    diagnostics["status"]="CONFLICTING_FILL_ID";break
                diagnostics["duplicate_fill_ids"]+=1
            else:records[key]=normal
        if diagnostics["status"]!="OK":break
        diagnostics["trades_seen"]+=len(payload)
        if not actual_ts:
            diagnostics["status"]="NO_VALID_TIMESTAMP";break
        last=max(actual_ts)
        if diagnostics["earliest_ms"] is None:diagnostics["earliest_ms"]=min(actual_ts)
        diagnostics["latest_ms"]=last
        if last>until_ms:break
        if len(payload)<PAGE_LIMIT:break
        if last<since:
            diagnostics["status"]="NON_ADVANCING_PAGE";break
        # If 100 fills share last ms, keyset pagination might drop records.
        if sum(x==last for x in actual_ts)>=PAGE_LIMIT:
            diagnostics["status"]="AMBIGUOUS_TIMESTAMP_PAGE_BOUNDARY";break
        since=last+1
    else:
        diagnostics["truncated"]=True
        diagnostics["status"]="PAGE_BUDGET_EXCEEDED"
    if diagnostics["status"]!="OK" or diagnostics["truncated"]:
        return [],diagnostics
    return sorted(records.values(),key=lambda x:(x["timestamp_ms"],x["trade_id"])),diagnostics



def date_scoped_trades(exchange,symbol,since_ms,until_ms,*,max_calls=96):
    """GET-only date-window retrieval; recursively bisect at 100-fill cap.

    Critical: the OKX fills-history endpoint returns *latest* 100 hits when
    asked for a wide time range. Never interpret absent older fills as absence.
    """
    records={}
    stats=dict(status="OK",window_queries=0,split_windows=0,
               truncated=False,earliest_ms=None,latest_ms=None,
               first_requested_ms=since_ms,last_requested_ms=until_ms,
               duplicates=0)
    if until_ms<since_ms:return [],dict(stats,status="INVALID_RANGE")
    next_day=datetime.fromtimestamp(since_ms/1000,KST)
    start_day=datetime(next_day.year,next_day.month,next_day.day,tzinfo=KST)
    boundaries=[]
    while int(start_day.timestamp()*1000)<=until_ms:
        begin=max(since_ms,int(start_day.timestamp()*1000))
        end=min(until_ms,int(start_day.timestamp()*1000)+86400000-1)
        boundaries.append((begin,end,0))
        start_day+=timedelta(days=1)
    while boundaries:
        begin,end,depth=boundaries.pop(0)
        if stats["window_queries"]>=max_calls:
            stats["status"]="QUERY_BUDGET_EXCEEDED"
            stats["truncated"]=True
            break
        response=exchange.fetch_my_trades(symbol,since=begin,limit=PAGE_LIMIT,
                                          params={"until":end})
        stats["window_queries"]+=1
        if not isinstance(response,list):
            stats["status"]="INVALID_RESPONSE";break
        if len(response)>=PAGE_LIMIT:
            if depth>=12 or end-begin<1000:
                stats["status"]="FULL_WINDOW_CANNOT_SPLIT"
                stats["truncated"]=True
                break
            midpoint=begin+(end-begin)//2
            boundaries[0:0]=[(begin,midpoint,depth+1),(midpoint+1,end,depth+1)]
            stats["split_windows"]+=1
            continue
        for raw in response:
            item=normalize(raw)
            if item is None:continue
            at=item["timestamp_ms"]
            if at<begin or at>end:
                stats["status"]="OUT_OF_RANGE_FILL_FROM_EXCHANGE"
                break
            key=(item["order_id"],item["trade_id"])
            if key in records and records[key]!=item:
                stats["status"]="CONFLICTING_FILL_ID";break
            if key in records:stats["duplicates"]+=1
            records[key]=item
            if stats["earliest_ms"] is None or at<stats["earliest_ms"]:
                stats["earliest_ms"]=at
            if stats["latest_ms"] is None or at>stats["latest_ms"]:
                stats["latest_ms"]=at
        if stats["status"]!="OK":break
    if stats["status"]!="OK" or stats["truncated"]:
        return [],stats
    return sorted(records.values(),key=lambda x:(x["timestamp_ms"],x["trade_id"])),stats


def grouped_orders(fills):
    by=defaultdict(list)
    for r in fills:by[r["order_id"]].append(r)
    out={}
    for order_id,items in by.items():
        sides={x["side"] for x in items}
        if len(sides)!=1:continue
        total=sum((Decimal(x["quantity"]) for x in items),Decimal(0))
        price=sum((Decimal(x["quantity"])*Decimal(x["price"]) for x in items),Decimal(0))/total
        out[order_id]=dict(order_id=order_id,side=next(iter(sides)),
                           filled_contracts=str(total),vwap=str(price),
                           first_ms=min(x["timestamp_ms"] for x in items),
                           last_ms=max(x["timestamp_ms"] for x in items),
                           trade_ids=sorted(x["trade_id"] for x in items))
    return out


def candidates(target,orders):
    result=[]
    expected_side="buy" if target["side"]=="short" else "sell"
    try:amount=Decimal(str(target["amount"]))
    except (ValueError,TypeError):return []
    if amount<=0:return []
    for o in orders.values():
        if o["side"]!=expected_side:continue
        if Decimal(o["filled_contracts"])!=amount:continue
        time_delta=o["last_ms"]-target["time_ms"]
        if time_delta< -BEFORE_MS or time_delta>AFTER_MS:continue
        if o["first_ms"]<o["last_ms"]-600000:continue
        px=target.get("journal_close_price")
        if px is not None:
            try:
                ref=Decimal(str(px))
                if ref<=0 or abs(Decimal(o["vwap"])-ref)/ref>Decimal("0.002"):
                    continue
            except (ValueError,TypeError):continue
        result.append(dict(o,close_time_gap_seconds=round(time_delta/1000,2),
                           journal_amount=str(amount),match_kind="TEMPORAL_ONLY_NOT_PROVEN"))
    return sorted(result,key=lambda x:abs(x["close_time_gap_seconds"]))


def assess_order(exchange,symbol,probable):
    """Read-only exact order status; does not confer ownership identity."""
    try:
        row=exchange.fetch_order(probable["order_id"],symbol)
        if (str(row.get("id"))!=probable["order_id"] or row.get("status")!="closed"
                or row.get("side")!=probable["side"]
                or row.get("symbol") not in (None,symbol)
                or Decimal(str(row.get("filled")))!=Decimal(probable["filled_contracts"])
                or Decimal(str(row.get("remaining") or 0))!=0):
            return dict(order_status="UNVERIFIED")
        info=row.get("info") or {}
        return dict(order_status="EXACT_ORDER_CLOSED",
                    reduce_only=str(info.get("reduceOnly") or row.get("reduceOnly") or "").lower()=="true")
    except Exception as e:
        return dict(order_status="LOOKUP_UNAVAILABLE",error_type=type(e).__name__)


def examine(user_dir,client_factory,*,max_pages=MAX_PAGES,max_order_checks=80):
    folder=Path(user_dir)
    src=(folder/"trades_log.jsonl").read_bytes()
    rows=[json.loads(line) for line in src.decode().splitlines() if line.strip()]
    missing,quality,total=unverified_closes(rows,folder)
    per=defaultdict(list)
    for r in missing:per[r["symbol"]].append(r)
    symbol_stats={}
    collected={}
    for sym,requests in per.items():
        earliest=min(r["time_ms"] for r in requests)-BEFORE_MS
        latest=max(r["time_ms"] for r in requests)+AFTER_MS
        try:
            client=client_factory(sym)
            result,diagnostics=date_scoped_trades(
                client.exchange,sym,earliest,latest,max_calls=max(12,max_pages*6))
        except Exception as exc:
            result=[];diagnostics=dict(status="QUERY_UNAVAILABLE",error_type=type(exc).__name__)
        symbol_stats[sym]=dict(diagnostics,normal_fills=len(result),
                               requested_missing_closes=len(requests))
        collected[sym]=grouped_orders(result) if diagnostics.get("status")=="OK" else {}
    rows_out=[];checked=0;unique_candidate_rows=0;ambiguous=0
    for r in missing:
        matches=candidates(r,collected.get(r["symbol"]) or {})
        evidence="NO_CANDIDATE"
        if len(matches)>1:
            evidence="AMBIGUOUS_MULTIPLE_ORDERS";ambiguous+=1
        elif len(matches)==1:
            unique_candidate_rows+=1
            evidence="UNIQUE_TIME_SIDE_QUANTITY_PRICE_CANDIDATE_NOT_OWNED"
            if checked<max_order_checks:
                try:
                    client=client_factory(r["symbol"])
                    matches[0].update(assess_order(client.exchange,r["symbol"],matches[0]))
                    checked+=1
                except Exception as exc:matches[0]["order_status"]="LOOKUP_UNAVAILABLE"
        rows_out.append(dict(row_index=r["index"],row_sha256=r["row_sha256"],
                             symbol=r["symbol"],side=r["side"],journal_time=r["journal_time"],
                             reason=r["reason"],candidate_count=len(matches),
                             evidence=evidence,candidates=matches[:3],
                             verified_owned=False,may_promote_to_reconciled=False))
    return dict(version="v33-28",mode="GET_ONLY_DIAGNOSTIC",
        original_journal_sha256=hashlib.sha256(src).hexdigest(),
        closed_unverified_total=total,unresolved_mapped=len(missing),
        unresolved_alignment_issues=quality,symbol_stats=symbol_stats,
        unique_temporal_candidates=unique_candidate_rows,
        ambiguous_candidates=ambiguous,
        exchange_order_queries=checked,results=rows_out,
        interpretation="Even unique matches are NOT owned order proof; do not modify journal or increase verified lifecycle count.",
        date_window_history_search=True,
        may_execute=False)



def read_snapshot(user_dir,*,limit=100):
    """Local report only; no HTTP/OKX calls; caller must authenticate."""
    folder=Path(user_dir)
    report_path=folder/"core_historical_close_audit_v33_28.json"
    if not report_path.is_file():
        return dict(status="NOT_YET_COLLECTED",may_execute=False)
    try:
        report=json.loads(report_path.read_text())
        if report.get("version")!="v33-28" or report.get("mode")!="GET_ONLY_DIAGNOSTIC":
            return dict(status="INVALID_REPORT_VERSION",may_execute=False)
        digest=hashlib.sha256((folder/"trades_log.jsonl").read_bytes()).hexdigest()
        original=report.get("original_journal_sha256")
        if not isinstance(report.get("results"),list):
            return dict(status="INVALID_REPORT_ROWS",may_execute=False)
        return dict(status="KNOWN",may_execute=False,
            snapshot_stale=original!=digest,
            unverified_at_collection=report.get("closed_unverified_total"),
            unique_temporal_candidates=report.get("unique_temporal_candidates"),
            ambiguous_candidates=report.get("ambiguous_candidates"),
            symbol_stats=report.get("symbol_stats"),
            evidence_note=report.get("interpretation"),
            results=report["results"][-max(0,min(int(limit),150)):])
    except (OSError,TypeError,ValueError):
        return dict(status="READ_FAILED",may_execute=False)


def main():
    import argparse,config,okx_client
    p=argparse.ArgumentParser()
    p.add_argument("--user-dir",required=True)
    p.add_argument("--output",required=True)
    p.add_argument("--max-pages",type=int,default=MAX_PAGES)
    options=p.parse_args()
    cfg=config.UserConfig(options.user_dir)
    result=examine(options.user_dir,lambda sym:okx_client.OkxClient(sym,cfg),max_pages=options.max_pages)
    Path(options.output).write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps(dict(unverified=result["closed_unverified_total"],
        matched_candidates=result["unique_temporal_candidates"],
        ambiguous=result["ambiguous_candidates"],
        exchange_queries=result["exchange_order_queries"],symbol_stats=result["symbol_stats"]),ensure_ascii=False,indent=2))
    print("OUTPUT",options.output)


if __name__=="__main__":main()
