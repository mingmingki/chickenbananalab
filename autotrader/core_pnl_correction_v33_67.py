"""v33-67 read-time exact-fill corrections for canonical PnL consumers.

Original trades_log.jsonl NEVER changed. A pinned attested six-row correction
manifest is applied only if the original row's entire source fingerprint and
exact close order/fill identities are still identical. Load once per process,
fail closed on missing, conflicting or invalid manifest. Reader only.
"""
import hashlib,json,math
from functools import lru_cache
from pathlib import Path
from decimal import Decimal,InvalidOperation

MANIFEST="core_exact_close_corrections_v33_67.json"
ORIGIN="core_bill_fill_attribution_v33_66.json"

def canonical_row(row):
    return hashlib.sha256(json.dumps(row,sort_keys=True,ensure_ascii=False,
        separators=(",",":"),allow_nan=False).encode()).hexdigest()

def dec(value):
    if value is None or isinstance(value,bool):raise ValueError("not_numeric")
    x=Decimal(str(value))
    if not x.is_finite():raise ValueError("nonfinite")
    return x

def make_manifest(user_dir):
    root=Path(user_dir)
    proof=json.loads((root/ORIGIN).read_text())
    if proof.get("version")!="v33-66" or not proof["completeness"]["complete"]:
        raise ValueError("BILL_PROOF_NOT_COMPLETE")
    mismatches=proof.get("exact_gross_close_mismatches",{}).get("rows",[])
    if len(mismatches)!=6:raise ValueError("EXPECTED_SIX_UNIQUE_PROOFS")
    raw=[json.loads(line) for line in (root/"trades_log.jsonl").read_text().splitlines() if line.strip()]
    corrections={}
    for v in mismatches:
        idx=v["journal_index"];item=raw[idx]
        if (item.get("type")!="close" or item.get("strategy_group")!="core"
            or item.get("reason")!="position_ai_close_all"
            or item.get("pnl_source")!="okx_realized"
            or item.get("dry_run") is not False
            or item.get("symbol")!=v["symbol"] or item.get("time")!=v["time"]):
            raise ValueError("ORIGINAL_CLOSE_NOT_PROVEN")
        eid=item.get("execution_id")
        ev=item.get("close_execution_evidence") or {}
        if not isinstance(eid,str) or eid!=f"core-close-order:{item['symbol']}:{v['close_order_id']}":
            raise ValueError("OWNED_CLOSE_ORDER_ID_MISMATCH")
        if (str(ev.get("order_id"))!=v["close_order_id"] or
            set(map(str,ev.get("trade_ids") or []))!=set(map(str,v["trade_ids"]))):
            raise ValueError("OWNED_FILL_IDS_MISMATCH")
        oldgross=dec(item["pnl"]);oldnet=dec(item["okx_net_pnl"])
        newgross=dec(v["exchange_exact_fill_gross"])
        if abs(oldgross)>Decimal(".0000001") or newgross>=0:
            raise ValueError("NOT_ZERO_GROSS_NEGATIVE_EXACT_BILL")
        delta=newgross-oldgross
        if abs(delta-dec(v["unrecorded_gross_difference"]))>Decimal(".000001"):
            raise ValueError("EXACT_BILL_DELTA_CONFLICT")
        if eid in corrections:raise ValueError("DUPLICATE_OWNED_CLOSE")
        corrections[eid]=dict(source_row_sha256=canonical_row(item),
            order_id=v["close_order_id"],trade_ids=sorted(map(str,v["trade_ids"])),
            symbol=item["symbol"],time=item["time"],
            original_gross=str(oldgross),corrected_gross=str(newgross),
            original_net=str(oldnet),corrected_net=str(oldnet+delta),
            gross_delta=str(delta),
            evidence="EXACT_ORDER_AND_ALL_TRADE_IDS_MATCHED_OKX_ACCOUNT_BILLS")
    if round(sum(float(v["gross_delta"]) for v in corrections.values()),5)!=-15.78321:
        raise ValueError("EXPECTED_CONFIRMED_MISSING_GROSS_CHANGED")
    return dict(version="v33-67",authority="READ_TIME_PNL_ONLY",
        target_date="2026-10-19",original_journal_unchanged=True,
        source_v33_66_report_sha256=hashlib.sha256((root/ORIGIN).read_bytes()).hexdigest(),
        total_gross_delta_usdt=-15.78321,
        corrected_close_count=len(corrections),corrections=corrections)

@lru_cache(maxsize=16)
def load_manifest(user_dir):
    path=Path(user_dir).resolve()/MANIFEST
    if not path.exists():return {}
    try:
        obj=json.loads(path.read_text())
    except (ValueError,OSError,TypeError):
        return {}
    if (obj.get("version")!="v33-67" or
        obj.get("authority")!="READ_TIME_PNL_ONLY" or
        obj.get("corrected_close_count")!=6 or
        abs(float(obj.get("total_gross_delta_usdt",0))+15.78321)>.000001 or
        len(obj.get("corrections",{}))!=6):
        return {}
    return obj["corrections"]

def adjusted_row(user_dir,row):
    if (not isinstance(row,dict) or row.get("type")!="close"
        or row.get("strategy_group")!="core"
        or row.get("pnl_source")!="okx_realized"
        or row.get("dry_run") is not False):
        return row
    eid=row.get("execution_id")
    if not isinstance(eid,str):return row
    info=load_manifest(str(user_dir)).get(eid)
    if not info:return row
    if canonical_row(row)!=info["source_row_sha256"]:
        return row
    e=row.get("close_execution_evidence") or {}
    if (e.get("order_id")!=info["order_id"] or
        sorted(map(str,e.get("trade_ids") or []))!=info["trade_ids"]):
        return row
    try:
        oldgross=dec(row.get("pnl"));oldnet=dec(row.get("okx_net_pnl"))
        if oldgross!=dec(info["original_gross"]) or oldnet!=dec(info["original_net"]):
            return row
        result=dict(row)
        result["pnl"]=float(dec(info["corrected_gross"]))
        result["okx_net_pnl"]=float(dec(info["corrected_net"]))
        result["_pnl_correction"]="v33-67_exact_bill_attested_readtime"
        return result
    except (ValueError,TypeError,InvalidOperation):return row
