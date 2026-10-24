"""v33-36 full historical CORE trade-journal evidence coverage.

Read-only inspection. Verified means an immutable row is bound to an
independently reconciled OKX position (and for audited cohorts, exact fee bills).
Other rows are explicitly UNVERIFIED even if locally arithmetically consistent.
No exchange order APIs, strategy writes or journal edits.
"""
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
import hashlib, json, os, tempfile

import core_position_pnl_audit_v33_34 as position_audit
import core_missing_entry_recovery_v33_34 as missing_entry
import core_exact_fee_bills_v33_35 as late_fees
import core_exact_bill_audit_v33_34 as early_fees

FILE = "core_full_journal_audit_v33_36.json"
HISTORICAL_CORE = {
    "BTC/USDT:USDT", "ETH/USDT:USDT", "XRP/USDT:USDT",
    "ADA/USDT:USDT", "PI/USDT:USDT"}
EVENT_TYPES = {"open", "reduce", "close"}
KST = timezone(timedelta(hours=9))
EPS = Decimal("0.0001")


def scope(row):
    if (not isinstance(row, dict) or row.get("type") not in EVENT_TYPES
        or row.get("dry_run") is True):
        return None
    group, symbol = row.get("strategy_group"), row.get("symbol")
    if group == "core":
        return "EXPLICIT_CORE"
    if group is None and symbol in HISTORICAL_CORE:
        return "LEGACY_CORE_COMPATIBLE"
    return None


def digest(row):
    return hashlib.sha256(json.dumps(
        row, sort_keys=True, ensure_ascii=False, separators=(",",":"),
        allow_nan=False).encode("utf-8")).hexdigest()


def number(value):
    if value is None or isinstance(value, bool):
        raise ValueError("MISSING_NUMERIC")
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("NONFINITE_NUMERIC")
    return result


def local_accounting(row):
    if row.get("type") not in ("reduce", "close"):
        return "NOT_APPLICABLE", None
    try:
        gross = number(row.get("pnl"))
        fee = number(row.get("fee"))
        funding = number(row.get("funding_fee") or 0)
        net = number(row.get("okx_net_pnl"))
        delta = net - (gross - fee + funding)
        return ("MATCH" if abs(delta) <= EPS else "MISMATCH", str(delta))
    except (ValueError, TypeError, InvalidOperation):
        return "INCOMPLETE", None
def source_proofs(directory, rows):
    p = position_audit.read_report(directory)
    m = missing_entry.read_report(directory)
    f48 = late_fees.read_status(directory)
    f10 = early_fees.read_status(directory)
    required = (
        p.get("status") == "KNOWN" and p.get("invalidated_position_proofs") == 0
        and m.get("status") == "KNOWN" and m.get("invalidated") == 0
        and f48.get("status") == "KNOWN" and f48.get("source_valid") is True
        and f10.get("status") == "KNOWN" and f10.get("source_valid") is True)
    if not required:
        raise ValueError("PRIOR_VERIFICATION_EVIDENCE_STALE_OR_MISSING")
    position48 = [x for x in p["details"] if x.get("status") == "MATCH"]
    recovered10 = [x for x in m["records"] if x.get("status") == "MATCH"]
    fee48 = [x for x in f48["results"] if x.get("status") == "MATCH"]
    fee10 = [x for x in f10["records"] if x.get("status") == "MATCH"]
    if tuple(map(len,(position48,recovered10,fee48,fee10))) != (48,10,48,10):
        raise ValueError("EVIDENCE_COHORT_SIZE_CHANGED")
    indexes48 = {x["close_index"] for x in position48}
    indexes10 = {x["close_index"] for x in recovered10}
    if indexes48.intersection(indexes10):
        raise ValueError("OVERLAPPING_POSITION_PROOF")
    if indexes48 != {x["close_index"] for x in fee48}:
        raise ValueError("POSITION_48_EXACT_FEE_SCOPE_MISMATCH")
    if indexes10 != {x["close_index"] for x in fee10}:
        raise ValueError("POSITION_10_EXACT_FEE_SCOPE_MISMATCH")
    all_ids = [str(bid) for r in fee48+fee10 for bid in r["matched_bill_ids"]]
    if len(all_ids) != 374 or len(set(all_ids)) != 374:
        raise ValueError("FEE_BILL_COUNT_OR_IDENTITY_COLLISION")
    return position48, recovered10, fee48, fee10


def row_claims(rows, pos48, pos10):
    claims = defaultdict(set)
    def own(index, rowhash, kind):
        if type(index) != int or not 0 <= index < len(rows):
            raise ValueError("BOUND_INDEX_OUTSIDE_JOURNAL")
        if scope(rows[index]) is None:
            raise ValueError("PROOF_REFERENCES_NOT_CORE_EVENT")
        if digest(rows[index]) != rowhash:
            raise ValueError("BOUND_ROW_CONTENT_CHANGED")
        claims[index].add(kind)
    for x in pos48:
        for bound in x["bound_rows"]:
            own(bound["index"],bound["sha256"],"MATCHED_OKX_POSITION")
    for x in pos10:
        own(x["close_index"],x["close_sha256"],"MATCHED_OKX_POSITION")
        for bound in x["trade_rows"]:
            own(bound["index"],bound["sha256"],"MATCHED_OKX_POSITION")
        delayed=x.get("delayed_open")
        if delayed:
            own(delayed["index"],delayed["sha256"],"OWNED_ENTRY_ORDER")
    return claims
def collect(rows, directory, *, observed=None):
    p48,p10,f48,f10 = source_proofs(directory,rows)
    claims = row_claims(rows,p48,p10)
    candidates = [(i,r,scope(r)) for i,r in enumerate(rows) if scope(r)]
    identifiers = Counter(r.get("execution_id") for i,r,g in candidates
                          if r.get("execution_id"))
    fill_owners=defaultdict(set)
    for i,r,g in candidates:
        if r.get("type") not in ("reduce","close"):continue
        evidence=r.get("close_execution_evidence") or {}
        if not isinstance(evidence,dict):continue
        for tid in evidence.get("trade_ids") or []:
            if tid:
                fill_owners[(r.get("symbol"),str(tid))].add(i)
    reused={key:sorted(indices) for key,indices in fill_owners.items()
            if len(indices)>1}
    repeated_indices={i for indices in reused.values() for i in indices}

    outcomes=[]
    for i,r,group in candidates:
        typ=r["type"]
        numeric,delta=local_accounting(r)
        faults=[]
        if numeric=="MISMATCH":
            faults.append("NET_GROSS_FEE_FUNDING_INVARIANT")
        if r.get("execution_id") and identifiers[r["execution_id"]]>1:
            faults.append("DUPLICATE_EXECUTION_ID")
        if i in repeated_indices:
            faults.append("REUSED_EXCHANGE_TRADE_ID")
        if faults:
            status="MISMATCH"
        elif claims.get(i):
            status="VERIFIED"
        else:
            status="UNVERIFIED"
        if status=="VERIFIED":
            reason="INDEPENDENT_OKX_POSITION_AND_FEE_COHORT"
        elif faults:
            reason=",".join(faults)
        elif group=="LEGACY_CORE_COMPATIBLE":
            reason="LEGACY_ROW_WITHOUT_INDEPENDENT_OKX_LINK"
        elif typ=="open":
            reason="ENTRY_WITHOUT_INDEPENDENT_LIFECYCLE_PROOF"
        elif not r.get("execution_id"):
            reason="EXECUTION_ID_NOT_RECORDED"
        elif typ in ("reduce","close"):
            reason="PARTIAL_ORDER_OR_LOCAL_RECEIPT_NOT_WHOLE_POSITION_PROOF"
        else:
            reason="NO_INDEPENDENT_PROOF"
        outcome=dict(row_index=i,row_sha256=digest(r),type=typ,
            symbol=r.get("symbol"),time=r.get("time"),
            declared_group=r.get("strategy_group"),
            scope=group,status=status,reason=reason,
            evidence_tiers=sorted(claims.get(i,set())),
            numeric_invariant=numeric,numeric_residual_usdt=delta,
            has_execution_id=bool(r.get("execution_id")),
            has_fill_evidence=bool((r.get("close_execution_evidence") or {}).get("trade_ids")
                if isinstance(r.get("close_execution_evidence") or {},dict) else False),
            pnl_source=r.get("pnl_source"))
        outcomes.append(outcome)
    counts=Counter(x["status"] for x in outcomes)
    groups=Counter(x["scope"] for x in outcomes)
    kinds=Counter((x["type"],x["status"]) for x in outcomes)
    numeric=Counter(x["numeric_invariant"] for x in outcomes)
    missing_ids=Counter(x["type"] for x in outcomes
                        if not x["has_execution_id"])
    gross_rows=0;net_rows=0
    ledger_gross=Decimal(0);ledger_net=Decimal(0)
    for i,r,g in candidates:
        if r.get("type") not in ("reduce","close"):continue
        try:
            ledger_gross+=number(r.get("pnl"));gross_rows+=1
        except (ValueError,TypeError,InvalidOperation):pass
        try:
            ledger_net+=number(r.get("okx_net_pnl"));net_rows+=1
        except (ValueError,TypeError,InvalidOperation):pass
    mismatch=[x for x in outcomes if x["status"]=="MISMATCH"]
    unverified=[x for x in outcomes if x["status"]=="UNVERIFIED"]
    urgent=Counter((x["reason"],x["type"]) for x in unverified)
    cause_counts=Counter(x["reason"] for x in unverified)
    return dict(version="v33-36",mode="LOCAL_FULL_CORE_EVIDENCE_MATRIX",
        observed_at=(observed or datetime.now(KST)).isoformat(),
        may_execute=False,
        journal_rows_total=len(rows),
        covered_core_rows=len(outcomes),
        excluded_non_core_rows=len(rows)-len(outcomes),
        ambiguous_legacy_doge_rows=sum(
            x.get("type") in EVENT_TYPES and x.get("strategy_group") is None
            and x.get("symbol")=="DOGE/USDT:USDT" for x in rows),
        status_counts=dict(counts),scope_counts=dict(groups),
        type_status_counts=[
            dict(type=t,status=s,count=n)
            for (t,s),n in sorted(kinds.items())],
        core_realization_rows=sum(x["type"] in ("reduce","close")
                                  for x in outcomes),
        numeric_accounting_counts=dict(numeric),
        missing_execution_id_by_type=dict(missing_ids),
        unverified_reason_counts=dict(cause_counts),
        largest_unverified_categories=[
            dict(reason=why,type=kind,count=n)
            for (why,kind),n in urgent.most_common(12)],
        independent_position_and_fee_cohorts=dict(
            matched_positions=58,
            matched_position_with_local_entry=48,
            recovered_missing_or_delayed_entry=10,
            verified_fee_bills=374,
            prior_v33_34_fee_bills=71,
            new_v33_35_fee_bills=303),
        unmatched_crossrow_fill_id_keys=len(reused),
        crossrow_fill_id_reuse=[
            dict(symbol=key[0],trade_id=key[1],rows=indices)
            for key,indices in list(reused.items())[:30]],
        arithmetic_mismatches=mismatch,
        ledger_only_gross_sum_usdt=str(ledger_gross),
        ledger_only_gross_rows=gross_rows,
        ledger_only_net_sum_usdt=str(ledger_net),
        ledger_only_net_rows=net_rows,
        ledger_net_missing_rows=sum(x["type"] in ("reduce","close") for x in outcomes)-net_rows,
        events=outcomes,
        limitations=[
            "VERIFIED is confined to previous immutable OKX position and exact fee-bill cohorts.",
            "Local PnL arithmetic equality is NOT independent exchange verification.",
            "LEGACY_CORE_COMPATIBLE is inferred from historic core-only symbols, not an explicit group field.",
            "Historic explicit core DOGE is CORE; candidate_c DOGE/SOL remain excluded.",
            "The ledger-only net total excludes rows without OKX net PnL and is not account equity or aggregate trading contribution.",
            "Funding bill records are not independently mapped by posId, and no comprehensive cash-transfer wallet reconciliation was conducted.",
            "Unverified entries and reductions remain unresolved; no synthetic corrections or trading changes."])
def save_atomic(path, report):
    path=Path(path)
    fd,temp=tempfile.mkstemp(dir=path.parent,prefix=".v33_36_")
    try:
        os.fchmod(fd,0o600)
        with os.fdopen(fd,"w",encoding="utf-8") as file:
            json.dump(report,file,ensure_ascii=False,indent=2,allow_nan=False)
            file.flush();os.fsync(file.fileno())
        os.replace(temp,path)
    finally:
        if os.path.exists(temp):os.unlink(temp)


def run(user_dir):
    folder=Path(user_dir)
    raw=(folder/"trades_log.jsonl").read_bytes()
    rows=[json.loads(x) for x in raw.decode("utf-8").splitlines() if x.strip()]
    report=collect(rows,folder)
    report["original_journal_sha256"]=hashlib.sha256(raw).hexdigest()
    save_atomic(folder/FILE,report)
    return report


def read_status(user_dir):
    folder=Path(user_dir)
    try:
        report=json.loads((folder/FILE).read_text())
    except (OSError,ValueError):
        return dict(status="NOT_COLLECTED",may_execute=False)
    if report.get("version")!="v33-36":
        return dict(status="INVALID_VERSION",may_execute=False)
    try:
        raw=(folder/"trades_log.jsonl").read_bytes()
        rows=[json.loads(x) for x in raw.decode("utf-8").splitlines() if x.strip()]
        events=report.get("events",[])
        invalid=[x["row_index"] for x in events
            if x["row_index"]>=len(rows)
            or digest(rows[x["row_index"]])!=x["row_sha256"]
            or scope(rows[x["row_index"]]) is None]
        new_core=sum(scope(rows[i]) is not None
                     for i in range(report.get("journal_rows_total",0),len(rows)))
        status="KNOWN" if not invalid and not new_core else "STALE"
        return dict(report,status=status,may_execute=False,
                    invalidated_evidence_rows=len(invalid),
                    new_core_rows_since_audit=new_core,
                    source_valid=(not invalid and not new_core),
                    journal_appended_since_audit=(hashlib.sha256(raw).hexdigest()
                        != report.get("original_journal_sha256")))
    except (ValueError,TypeError,KeyError,IndexError):
        return dict(status="UNAVAILABLE",may_execute=False)


def main():
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument("--user-dir",required=True)
    args=parser.parse_args()
    result=run(args.user_dir)
    print(json.dumps(dict(version=result["version"],
        core_rows=result["covered_core_rows"],
        statuses=result["status_counts"],
        numeric=result["numeric_accounting_counts"],
        fee_bill_proofs=result["independent_position_and_fee_cohorts"],
        mismatch_count=len(result["arithmetic_mismatches"])),
        ensure_ascii=False))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
