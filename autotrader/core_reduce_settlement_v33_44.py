"""v33-44: bounded, GET-only independent CORE reduction settlement audit.

Never submits orders or rewrites journals. A receipt alone does not establish
request quantity or OKX settlement bills. Unknown or conflicting evidence is
reported explicitly; fee scope excludes entry fees and funding.
"""
from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import hashlib
import json

from core_reduce_quantity_v33_43 import _dec as D
from core_reduce_receipts_v33_43 import evaluate as receipt_evaluate, save
from core_full_ledger_audit_v33_36 import digest
from core_exact_bill_audit_v33_34 import fetch_bills, precise_trades

FILE = "core_reduce_settlement_v33_44.json"
KST = timezone(timedelta(hours=9))
TOL_QTY = Decimal("0.00000001")
TOL_USDT = Decimal("0.000001")


def _equal(a, b, tolerance):
    return abs(D(a) - D(b)) <= tolerance


def evidence_row(row, index):
    base = receipt_evaluate(row)
    base.update(row_index=index, row_sha256=digest(row), may_execute=False)
    if base["status"] != "EXACT_EXIT_FILL_RECEIPT_PARITY":
        base["quantity_chain"] = "PRIOR_RECEIPT_UNVERIFIED"
        base["settlement"] = "NOT_CHECKED"
        return base
    ev = row["close_execution_evidence"]
    requested = ev.get("requested_contracts")
    exchange_amount = ev.get("exchange_order_contracts")
    if requested is None:
        base["quantity_chain"] = "REQUEST_SIZE_NOT_RECORDED"
    else:
        try:
            req, filled = D(requested), D(ev["filled_contracts"])
            if req <= 0 or filled <= 0 or filled - req > TOL_QTY:
                raise ValueError("REQUEST_LESS_THAN_FILL_OR_INVALID")
            if exchange_amount is None:
                base["quantity_chain"] = "EXCHANGE_ORDER_SIZE_UNRECORDED"
            elif not _equal(exchange_amount, req, TOL_QTY):
                raise ValueError("REQUEST_EXCHANGE_SIZE_MISMATCH")
            else:
                base["quantity_chain"] = "REQUEST_EXCHANGE_FILL_JOURNAL_PARITY"
        except (ValueError, TypeError, ArithmeticError) as exc:
            base["quantity_chain"] = "QUANTITY_MISMATCH"
            base["reason_code"] = str(exc)[:110]
    base["settlement"] = "NOT_CHECKED"
    return base


def verify_settlement(row, out, client):
    """Independent OKX order + fills + exact one-to-one trade bills, or unknown.

    No claim of lifetime PnL. A bill matched on time alone is never accepted.
    """
    proof = row["close_execution_evidence"]
    symbol = row["symbol"]
    oid = str(proof["order_id"])
    try:
        order = client.exchange.fetch_order(oid, symbol)
        if not isinstance(order, dict) or str(order.get("id")) != oid:
            raise ValueError("ORDER_ID_MISMATCH")
        if str((order.get("info") or {}).get("ordId") or oid) != oid:
            raise ValueError("RAW_ORDER_ID_MISMATCH")
        if order.get("status") not in ("closed", "canceled"):
            raise ValueError("ORDER_NOT_TERMINAL")
        if order.get("amount") is None:
            raise ValueError("ORDER_QUANTITY_UNKNOWN")
        expected = D(proof["filled_contracts"])
        ordered = D(order["amount"])
        if ordered <= 0 or expected - ordered > TOL_QTY:
            raise ValueError("ORDER_AMOUNT_LESS_THAN_FILLED")
        if not _equal(order.get("filled"), expected, TOL_QTY):
            raise ValueError("ORDER_FILL_QUANTITY_MISMATCH")
        if proof.get("requested_contracts") is not None and not _equal(
                ordered, proof["requested_contracts"], TOL_QTY):
            raise ValueError("ORIGINAL_REQUEST_ORDER_MISMATCH")
        if proof.get("exchange_order_contracts") is not None and not _equal(
                ordered, proof["exchange_order_contracts"], TOL_QTY):
            raise ValueError("RECORDED_ORDER_QUANTITY_MISMATCH")

        trades = precise_trades(client, oid, proof["first_fill_timestamp_ms"],
                                expected_ids=proof["trade_ids"])
        if not trades:
            raise ValueError("NO_MATCHING_FILLS")
        quantities = [D(t["amount"]) for t in trades.values()]
        prices = [D(t["price"]) for t in trades.values()]
        if any(q <= 0 or p <= 0 for q,p in zip(quantities,prices)):
            raise ValueError("BAD_FILL_NUMERIC")
        total_qty = sum(quantities, Decimal(0))
        if not _equal(total_qty, expected, TOL_QTY):
            raise ValueError("EXACT_TRADES_QUANTITY_MISMATCH")
        vwap = sum((q*p for q,p in zip(quantities,prices)),Decimal(0))/total_qty
        if not _equal(vwap, proof["vwap"], TOL_QTY):
            raise ValueError("EXACT_TRADES_VWAP_MISMATCH")
        fee_fills = Decimal(0)
        for t in trades.values():
            fee=t.get("fee") or {}
            if fee.get("currency") != "USDT":
                raise ValueError("FILL_FEE_CURRENCY_UNKNOWN")
            fee_fills += D(fee["cost"])
        if not _equal(fee_fills, row["fee"], TOL_USDT):
            raise ValueError("FILL_JOURNAL_FEE_MISMATCH")
        start = int(proof["first_fill_timestamp_ms"])
        end = int(proof["last_fill_timestamp_ms"])
        if start <= 0 or end < start or end-start > 86400000:
            raise ValueError("FILL_TIME_RANGE_INVALID")
        bills, calls = fetch_bills(client.exchange, symbol, max(0,start-60000),
                                  end+60000, kind="2", max_calls=16)
        lookup = {}
        for bill_id, b in bills.items():
            if str(b.get("type")) != "2" or b.get("ccy") != "USDT":
                continue
            key = (str(b.get("ordId") or ""),str(b.get("tradeId") or ""))
            lookup.setdefault(key, []).append((str(bill_id),b))
        matched=[]
        billed_fee=Decimal(0)
        for tid,trade in trades.items():
            candidates = lookup.get((oid,str(tid)),[])
            if len(candidates) != 1:
                raise ValueError("FILL_BILL_MISSING_OR_DUPLICATED")
            bid,bill = candidates[0]
            if bid in {p[0] for p in matched}:
                raise ValueError("BILL_REUSED")
            inst = symbol.split("/")[0] + "-USDT-SWAP"
            if str(bill.get("instId") or "") != inst:
                raise ValueError("BILL_INSTRUMENT_MISMATCH")
            if bill.get("sz") not in (None, "") and not _equal(
                    bill["sz"],trade["amount"],TOL_QTY):
                raise ValueError("BILL_FILL_QUANTITY_MISMATCH")
            matched.append((bid,bill))
            billed_fee -= D(bill["fee"])
        extra=[bid for bid,b in bills.items() if str(b.get("type"))=="2"
               and str(b.get("ordId") or "")==oid
               and str(bid) not in {m[0] for m in matched}]
        if extra:
            raise ValueError("UNATTRIBUTED_BILL_FOR_ORDER")
        if not _equal(billed_fee, row["fee"], TOL_USDT):
            raise ValueError("BILL_JOURNAL_FEE_MISMATCH")
        out["exchange_order_contracts_verified"] = str(ordered)
        out["verified_fill_contracts"] = str(total_qty)
        out["verified_bill_fee_usdt"] = str(billed_fee)
        out["verified_bill_ids"] = sorted(bid for bid,_ in matched)
        out["bill_count"] = len(matched)
        out["bill_api_calls"] = calls
        out["settlement"] = "ORDER_FILLS_AND_EXACT_FEE_BILLS_VERIFIED"
    except Exception as exc:
        # GET-only exchange/history failures and partial proof fail closed.
        out["settlement"] = "INDEPENDENT_PROOF_UNCONFIRMED"
        out["settlement_reason"] = (str(exc)[:130] if isinstance(
            exc,(ValueError,KeyError,TypeError,ArithmeticError)) else type(exc).__name__)
    return out


def audit(user_dir, client_factory=None, max_online=8, now=None):
    root=Path(user_dir)
    raw=(root/"trades_log.jsonl").read_bytes()
    rows=[json.loads(x) for x in raw.splitlines() if x.strip()]
    events=[evidence_row(r,i) for i,r in enumerate(rows)
            if r.get("type")=="reduce" and r.get("strategy_group")=="core"]
    # Only recent matched receipts receive bounded live GET verification.
    cutoff=(now or datetime.now(KST))-timedelta(days=2)
    eligible=[]
    for e in events:
        if e["status"]!="EXACT_EXIT_FILL_RECEIPT_PARITY":
            continue
        try:
            timestamp=datetime.fromisoformat(str(e["time"]))
            if timestamp.tzinfo is None: timestamp=timestamp.replace(tzinfo=KST)
            if timestamp>=cutoff: eligible.append(e)
        except (TypeError,ValueError):
            pass
    clients={}
    verified_ids=set()
    selected=eligible[-max_online:] if client_factory and max_online>0 else []
    for e in selected:
        row=rows[e["row_index"]]
        sym=row["symbol"]
        if sym not in clients:
            try: clients[sym]=client_factory(sym)
            except Exception: clients[sym]=None
        if clients[sym] is not None:
            verify_settlement(row,e,clients[sym])
        else:
            e["settlement"]="INDEPENDENT_PROOF_UNCONFIRMED"
            e["settlement_reason"]="CLIENT_UNAVAILABLE"
        if e["settlement"]=="ORDER_FILLS_AND_EXACT_FEE_BILLS_VERIFIED":
            ids={(sym,bid) for bid in e["verified_bill_ids"]}
            if ids & verified_ids:
                e["settlement"]="DUPLICATE_BILL_ACROSS_RECEIPTS"
            verified_ids.update(ids)
    return dict(version="v33-44",mode="GET_ONLY_INDEPENDENT_REDUCE_SETTLEMENT",
        observed_at=datetime.now(KST).isoformat(),may_execute=False,may_correct_ledger=False,
        journal_sha256=hashlib.sha256(raw).hexdigest(),total_core_reduce_rows=len(events),
        online_checked=len(selected),
        receipt_status_counts=dict(Counter(e["status"] for e in events)),
        quantity_status_counts=dict(Counter(e["quantity_chain"] for e in events)),
        settlement_status_counts=dict(Counter(e["settlement"] for e in events)),
        records=events,
        limitations=["No ledger corrections or additional orders.",
        "Historical request amounts not journaled remain unknown.",
        "Only a bounded recent sample receives independent exchange GET checks.",
        "Exit-only fee verification is not full lifecycle PnL or profitability."])


def read_status(user_dir):
    root=Path(user_dir)
    try:
        report=json.loads((root/FILE).read_text())
        if report.get("version")!="v33-44":
            return dict(status="INVALID_VERSION",may_execute=False)
        raw=(root/"trades_log.jsonl").read_bytes()
        rows=[json.loads(s) for s in raw.splitlines() if s.strip()]
        invalid=sum(not isinstance(e.get("row_index"),int) or
            not (0<=e["row_index"]<len(rows)) or
            digest(rows[e["row_index"]])!=e["row_sha256"]
            for e in report["records"])
        stale=(hashlib.sha256(raw).hexdigest()!=report["journal_sha256"])
        return dict(report,status="INVALIDATED" if invalid else
                    "STALE" if stale else "KNOWN",
                    source_valid=invalid==0 and not stale,
                    invalidated_cases=invalid,may_execute=False)
    except (OSError,ValueError,TypeError,KeyError,IndexError):
        return dict(status="UNAVAILABLE",may_execute=False)


def main():
    import argparse
    p=argparse.ArgumentParser()
    p.add_argument("--user-dir",required=True)
    p.add_argument("--online",action="store_true")
    p.add_argument("--max-online",type=int,default=8)
    args=p.parse_args()
    if args.max_online<0 or args.max_online>20:
        raise ValueError("ONLINE_QUERY_BUDGET_OUT_OF_RANGE")
    factory=None
    if args.online:
        import config,okx_client
        cfg=config.UserConfig(args.user_dir)
        factory=lambda sym:okx_client.OkxClient(sym,cfg)
    result=audit(args.user_dir,factory,args.max_online)
    save(Path(args.user_dir)/FILE,result)
    print(json.dumps({k:v for k,v in result.items() if k in
        ("version","online_checked","total_core_reduce_rows",
        "receipt_status_counts","quantity_status_counts",
        "settlement_status_counts")},ensure_ascii=False))
if __name__=="__main__":
    main()
