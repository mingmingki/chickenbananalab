"""v33-40: read-only provenance gate and split-fill investigation.

207 legacy full-closes: distinguish exchange settlement from journal ownership.
59 unmatched legacy reductions: inspect order-level multi-fill candidates.
No historical journal or financial ledger mutation; no order execution.
"""
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path
import hashlib
import json
import os
import tempfile

import core_legacy_gap_audit_v33_37 as legacy
import core_legacy_full_close_v33_39 as fullclose
import core_legacy_bill_correlation_v33_38 as partial80
import core_exact_bill_audit_v33_34 as bill_api
import core_full_ledger_audit_v33_36 as ledger

FILE = "core_legacy_provenance_v33_40.json"
VERSION = "v33-40"
EXPECTED_FULL = 207
EXPECTED_UNMATCHED_REDUCTIONS = 59
MAX_SYMBOL_DAYS = 20


def load_cohorts(folder, rows):
    root = Path(folder)
    upstream = json.loads((root / legacy.FILE).read_text())
    full = json.loads((root / fullclose.FILE).read_text())
    partial = json.loads((root / partial80.FILE).read_text())
    if upstream.get("version") != "v33-37" or full.get("version") != "v33-39" or partial.get("version") != "v33-38":
        raise ValueError("UPSTREAM_VERSION_INVALID")
    all_cases = upstream.get("cases", [])
    if len(all_cases) != 347 or len(full.get("records", [])) != EXPECTED_FULL or len(partial.get("cases", [])) != 80:
        raise ValueError("UPSTREAM_COHORT_INVALID")
    target = [c for c in all_cases if c.get("type") == "reduce"
        and c.get("candidate", {}).get("status") == "NO_CANDIDATE"
        and c.get("defect") == "LEGACY_ESTIMATED_REDUCE_MISSING_NET_AND_ORDER_ID"]
    if len(target) != EXPECTED_UNMATCHED_REDUCTIONS:
        raise ValueError("UNMATCHED_COHORT_CHANGED")
    tracked = all_cases + [
        dict(index=r.get("index"), row_sha256=r.get("row_sha256"))
        for r in full["records"]
    ]
    for case in tracked:
        idx = case.get("index")
        if type(idx) is not int or idx < 0 or idx >= len(rows) or ledger.digest(rows[idx]) != case.get("row_sha256"):
            raise ValueError("HISTORICAL_ROW_TAMPER_OR_STALE")
    for case in partial.get("cases", []):
        idx = case.get("row_index")
        if type(idx) is not int or idx < 0 or idx >= len(rows) or ledger.digest(rows[idx]) != case.get("row_sha256"):
            raise ValueError("PARTIAL80_STALE")
    if len({c["index"] for c in target}) != len(target):
        raise ValueError("DUPLICATE_REDUCTION_CASE")
    if len({c["index"] for c in full["records"]}) != EXPECTED_FULL:
        raise ValueError("DUPLICATE_FULL_CLOSE_CASE")
    return target, upstream, full, partial


def known_exchange_ids(full, partial):
    orders = set()
    bills = set()
    for r in full["records"]:
        if r["status"] == "EXCHANGE_SETTLEMENT_TEMPORAL_CANDIDATE":
            orders.add((r["symbol"], str(r["candidate_order"]["order_id"])))
            bills.update(str(x) for x in r["settlement"]["bill_ids"])
    for r in partial["cases"]:
        if r["status"] == "BILL_VERIFIED_FOR_FILL_ONLY":
            orders.add((r["symbol"], str(r["exchange_order_id"])))
            bills.add(str(r["exchange_bill_id"]))
    return orders, bills


def near_side_fills(row, trades, seconds=150):
    when = fullclose.date_ms(row["time"])
    side = "buy" if row.get("side") == "short" else "sell"
    return [t for t in trades
            if t.get("side") == side
            and t.get("timestamp") is not None
            and abs(int(t["timestamp"]) - when) <= 1000 * seconds]


def report_full_ownership(full, rows):
    evidence = []
    for c in full["records"]:
        row = rows[c["index"]]
        direct_ids = [
            k for k in ("order_id", "exchange_order_id", "trade_id", "close_execution_evidence")
            if row.get(k)
        ]
        candidate_id = c.get("candidate_order", {}).get("order_id")
        evidence.append(dict(
            row_index=c["index"], row_sha256=c["row_sha256"],
            symbol=c["symbol"], time=c["time"],
            exchange_status=c["status"],
            candidate_order_id=candidate_id,
            journal_direct_execution_fields=direct_ids,
            journal_row_ownership_proven=False,
            correction_eligible=False,
            proof_reason=("DIRECT_IDENTIFIER_REQUIRES_INDEPENDENT_RECONCILIATION"
                          if direct_ids else "NO_AUTHORITATIVE_ORDER_OR_FILL_ID_IN_JOURNAL")))
    if any(x.get("journal_row_ownership_proven") for x in full["records"]):
        raise ValueError("UPSTREAM_UNEXPECTED_OWNERSHIP")
    return evidence


def inspect_reductions(folder, rows, target, full, partial, client_factory):
    grouped = defaultdict(list)
    for case in target:
        grouped[(case["symbol"], case["time"][:10])].append(case)
    if len(grouped) > MAX_SYMBOL_DAYS:
        raise ValueError("SYMBOL_DAY_LIMIT_EXCEEDED")
    clients, trades_cache, errors, bill_cache = {}, {}, {}, {}
    for key in sorted(grouped):
        sym, day = key
        try:
            if sym not in clients:
                clients[sym] = client_factory(sym)
            trades_cache[key] = legacy.fetch_symbol_day(clients[sym], sym, day)
        except Exception as exc:
            errors[key] = type(exc).__name__
    used_orders, used_bills = known_exchange_ids(full, partial)
    records = []
    for case in target:
        idx = case["index"]
        row = rows[idx]
        key = (case["symbol"], case["time"][:10])
        base = dict(row_index=idx, row_sha256=case["row_sha256"],
                    symbol=case["symbol"], time=case["time"],
                    original_gross_usdt=str(row.get("pnl")),
                    journal_row_ownership_proven=False, correction_eligible=False)
        if key in errors:
            records.append(dict(base, status="UNAVAILABLE",
                reason_code=errors[key]))
            continue
        trades = trades_cache[key]
        side_fills = near_side_fills(row, trades)
        choices = fullclose.order_candidates(row, trades)
        if not choices:
            records.append(dict(base, status=("NEARBY_SIDE_FILLS_NO_ORDER_QTY_MATCH"
                    if side_fills else "NO_NEARBY_SIDE_FILL"),
                reason_code="NO_COMPLETE_SINGLE_ORDER_MATCH",
                nearby_side_fill_count=len(side_fills),
                nearby_order_count=len({str(t.get("order")) for t in side_fills})))
            continue
        if len(choices) > 1:
            records.append(dict(base,status="AMBIGUOUS_MULTIPLE_ORDERS",
                candidate_order_count=len(choices)))
            continue
        c = choices[0]
        if c["n_fills"] < 2:
            records.append(dict(base,status="INCONSISTENT_WITH_V33_37_SINGLE_FILL_SEARCH"))
            continue
        oid = (case["symbol"], c["order_id"])
        if oid in used_orders:
            records.append(dict(base,status="CROSS_COHORT_ORDER_COLLISION",
                candidate_order_id=c["order_id"]))
            continue
        try:
            if not fullclose.verify_order(clients[case["symbol"]], case["symbol"], row, c):
                raise ValueError("ORDER_NOT_VERIFIED_REDUCE_ONLY_CLOSED")
            if key not in bill_cache:
                s = fullclose.date_ms(key[1])
                bill_cache[key], _ = bill_api.fetch_bills(
                    clients[case["symbol"]].exchange,case["symbol"],s,s+86400000-1,
                    kind="2",max_calls=100)
            settlement = fullclose.settle_order(c,bill_cache[key])
            if set(settlement["bill_ids"]) & used_bills:
                raise ValueError("CROSS_COHORT_BILL_ID_COLLISION")
            observed = sum((fullclose.decimal(t.get("price")) * fullclose.decimal(t.get("amount"))
                             for t in side_fills
                             if str(t.get("order") or "") == c["order_id"]), Decimal(0))
            quantity = fullclose.decimal(c["qty"])
            vwap = observed / quantity
            records.append(dict(base,status="EXCHANGE_SETTLED_SPLIT_ORDER_TEMPORAL_CANDIDATE",
                candidate_order=c, settlement=settlement,
                exchange_vwap=str(vwap),
                journal_close_price=str(row.get("close_price")),
                hypothetical_gross_delta_usdt=str(
                    fullclose.decimal(settlement["exchange_gross_usdt"]) -
                    fullclose.decimal(row["pnl"])),
                reason_code="MULTIFILL_ORDER_WITH_SETTLEMENT_BILLS_NOT_ROW_OWNERSHIP"))
        except Exception as exc:
            records.append(dict(base,status="UNVERIFIED_ORDER_OR_SETTLEMENT",
                reason_code=str(exc)[:110],
                candidate_order=c))
    # A single exchange order or bill cannot be assigned to two distinct journal rows.
    order_rows, bill_rows = defaultdict(set), defaultdict(set)
    for record in records:
        if record["status"] != "EXCHANGE_SETTLED_SPLIT_ORDER_TEMPORAL_CANDIDATE":
            continue
        order_rows[(record["symbol"],record["candidate_order"]["order_id"])].add(record["row_index"])
        for bid in record["settlement"]["bill_ids"]:
            bill_rows[str(bid)].add(record["row_index"])
    conflicts = set()
    for seen in list(order_rows.values()) + list(bill_rows.values()):
        if len(seen) > 1:
            conflicts.update(seen)
    for r in records:
        if r["row_index"] in conflicts:
            r["status"] = "CROSS_ROW_EXCHANGE_ID_COLLISION"
            r["reason_code"] = "ORDER_OR_BILL_REUSED"
    return records, [dict(symbol=k[0], day=k[1], error=v) for k,v in sorted(errors.items())]


def audit(user_dir, client_factory):
    root = Path(user_dir)
    raw = (root / "trades_log.jsonl").read_bytes()
    rows = [json.loads(s) for s in raw.splitlines() if s.strip()]
    target, upstream, full, partial = load_cohorts(root, rows)
    full_evidence = report_full_ownership(full, rows)
    reduced, unavailable = inspect_reductions(root, rows, target, full, partial,client_factory)
    counts = Counter(x["status"] for x in reduced)
    candidates = [x for x in reduced
        if x["status"] == "EXCHANGE_SETTLED_SPLIT_ORDER_TEMPORAL_CANDIDATE"]
    return dict(version=VERSION, mode="HISTORICAL_READ_ONLY_PROVENANCE_AND_SPLIT_FILLS",
        may_execute=False, may_correct_ledger=False,
        observed_at=datetime.now(fullclose.KST).isoformat(),
        journal_sha256=hashlib.sha256(raw).hexdigest(),
        parent_reports_sha256={
            f:hashlib.sha256((root/f).read_bytes()).hexdigest()
            for f in (legacy.FILE,fullclose.FILE,partial80.FILE)},
        full_close_count=len(full_evidence),
        full_close_exchange_counts=full["counts"],
        full_close_journal_ownership_proven=0,
        full_close_correction_eligible=0,
        unmatched_reduction_count=len(reduced),
        unmatched_reduction_status_counts=dict(counts),
        split_order_candidate_count=len(candidates),
        split_candidate_bill_count=sum(len(x["settlement"]["bill_ids"]) for x in candidates),
        split_candidate_hypothetical_gross_delta_usdt=str(sum(
            (fullclose.decimal(x["hypothetical_gross_delta_usdt"]) for x in candidates), Decimal(0))),
        unavailable_symbol_days=unavailable,
        full_close_evidence=full_evidence,
        unmatched_reductions=reduced,
        limitations=[
            "Legacy close rows have no stored authoritative execution order/fill IDs; matching exchange orders is temporal only.",
            "Split order candidates are never proof of journal row ownership.",
            "No historic PnL, trade journal, order, funding or trading policy is modified.",
            "No exchange history match does not prove no execution: historical availability is bounded.",
            "All monetary differences are conditional diagnostics, not recovered profit or loss."])


def save(path, body):
    path = Path(path)
    fd, tmp = tempfile.mkstemp(prefix=".v33_40_",dir=path.parent)
    try:
        os.fchmod(fd,0o600)
        with os.fdopen(fd,"w",encoding="utf-8") as stream:
            json.dump(body,stream,ensure_ascii=False,indent=2,allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def read_status(user_dir):
    root = Path(user_dir)
    try:
        data = json.loads((root/FILE).read_text())
        if data.get("version") != VERSION:
            return dict(status="INVALID_VERSION",may_execute=False)
        for fname, digest in data["parent_reports_sha256"].items():
            if hashlib.sha256((root/fname).read_bytes()).hexdigest() != digest:
                return dict(status="STALE_PARENT",may_execute=False)
        rows = [json.loads(s) for s in (root/"trades_log.jsonl").read_bytes().splitlines() if s.strip()]
        cases = data["full_close_evidence"] + data["unmatched_reductions"]
        invalid = sum(type(c.get("row_index")) is not int
            or c["row_index"] < 0 or c["row_index"] >= len(rows)
            or ledger.digest(rows[c["row_index"]]) != c.get("row_sha256")
            for c in cases)
        return dict(data,status="STALE" if invalid else "KNOWN",
            source_valid=not invalid,invalidated_cases=invalid,
            may_execute=False,may_correct_ledger=False)
    except (OSError,ValueError,KeyError,IndexError,TypeError):
        return dict(status="UNAVAILABLE",may_execute=False)


def main():
    import argparse
    import config
    import okx_client
    parser=argparse.ArgumentParser()
    parser.add_argument("--user-dir",required=True)
    args=parser.parse_args()
    conf=config.UserConfig(args.user_dir)
    result=audit(args.user_dir,lambda sym:okx_client.OkxClient(sym,conf))
    save(Path(args.user_dir)/FILE,result)
    print(json.dumps(dict(version=result["version"],
        full_closes=result["full_close_count"],
        missing_reductions=result["unmatched_reduction_count"],
        reduction_counts=result["unmatched_reduction_status_counts"],
        split_bill_ids=result["split_candidate_bill_count"]),ensure_ascii=False))


if __name__=="__main__":
    raise SystemExit(main())
