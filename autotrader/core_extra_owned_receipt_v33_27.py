"""v33-27 additional owned-receipt close with missing journal execution ID/price.

This module reuses the original, exact owned-market-reduce order ID and proves
it via an EXCHANGE GET. No timestamp-only guessed associations or journal edits.
"""
import json, math
from pathlib import Path
from datetime import datetime, timezone, timedelta
import core_legacy_close_proof_v33_27 as historic

FILE="core_extra_owned_close_verified_v33_27.json"
KST=timezone(timedelta(hours=9))


def eligible(row):
    if (row.get("type")!="close" or row.get("strategy_group")!="core"
            or row.get("dry_run") is not False or row.get("symbol") not in historic.CORE
            or row.get("side") not in ("long","short") or row.get("execution_id")
            or row.get("close_execution_evidence") or row.get("close_price") is not None):
        return False
    r=row.get("close_order_reference")
    if not isinstance(r,dict) or r.get("basis")!="owned_market_reduce_only_order_receipt":
        return False
    if not r.get("order_id") or r.get("side")!=row["side"]:return False
    try:
        if historic.dec(r["expected_contracts"])!=historic.dec(row["amount"]):return False
        start=r["start_ms"]
        if type(start)!=int or start<1600000000000:return False
        at=datetime.fromisoformat(row["time"])
        if at.tzinfo is None:at=at.replace(tzinfo=KST)
        if not -10000<=at.timestamp()*1000-start<=600000:return False
    except (KeyError,ValueError,TypeError,OverflowError):return False
    return True


def verified_exchange_proof(client,row):
    from core_close_fill_evidence import verified_close_fills
    ref=row["close_order_reference"]
    result=verified_close_fills(client,{"side":row["side"],"contracts":row["amount"]},
        {"id":str(ref["order_id"]),"symbol":row["symbol"]},start_ms=ref["start_ms"])
    if not result or result.get("price_basis")!="verified_order_fills":return None
    if str(result.get("order_id"))!=str(ref["order_id"]):return None
    if historic.dec(result["filled_contracts"])!=historic.dec(row["amount"]):return None
    if not result.get("trade_ids"):return None
    return dict(result,proof_type="OWNED_ORDER_ID_EXCHANGE_FILL_VERIFIED")


def collect(rows,client_factory):
    clients={}
    records=[];failures=[]
    for index,row in enumerate(rows):
        if not eligible(row):continue
        try:
            client=clients.setdefault(row["symbol"],client_factory(row["symbol"]))
            result=verified_exchange_proof(client,row)
            if not result:
                failures.append(dict(row_index=index,reason="GET_NOT_VERIFIED"))
                continue
            records.append(dict(row_index=index,row_sha256=historic.row_hash(row),
                symbol=row["symbol"],side=row["side"],
                proof=result,derived_execution_key="verified-legacy-close:"+str(result["order_id"])))
        except Exception as e:
            failures.append(dict(row_index=index,reason=type(e).__name__))
    return dict(schema_version=1,mode="EXCHANGE_GET_ONLY",
                verified=records,unconfirmed=failures)


def load_valid_patch(rows,user_dir):
    path=Path(user_dir)/FILE
    if not path.exists():return {}
    try:state=json.loads(path.read_text())
    except (OSError,ValueError):return {}
    if state.get("schema_version")!=1 or not isinstance(state.get("verified"),list):return {}
    out={}
    for rec in state["verified"]:
        try:
            index=rec["row_index"]
            if type(index)!=int or index<0 or index>=len(rows) or index in out:continue
            row=rows[index]
            if not eligible(row) or historic.row_hash(row)!=rec["row_sha256"]:continue
            proof=rec["proof"]
            ref=row["close_order_reference"]
            if proof["proof_type"]!="OWNED_ORDER_ID_EXCHANGE_FILL_VERIFIED":continue
            if str(proof["order_id"])!=str(ref["order_id"]):continue
            if historic.dec(proof["filled_contracts"])!=historic.dec(row["amount"]):continue
            if not proof.get("trade_ids"):continue
            if rec["derived_execution_key"]!="verified-legacy-close:"+str(proof["order_id"]):continue
            price=float(proof["vwap"])
            if not math.isfinite(price) or price<=0:continue
            if abs(int(proof["first_fill_timestamp_ms"])-int(ref["start_ms"]))>600000:continue
            out[index]=dict(execution_id=rec["derived_execution_key"],
                close_price=price,close_execution_evidence=dict(
                    order_id=str(proof["order_id"]),trade_ids=proof["trade_ids"],
                    vwap=price,filled_contracts=float(proof["filled_contracts"]),
                    first_fill_timestamp_ms=int(proof["first_fill_timestamp_ms"]),
                    last_fill_timestamp_ms=int(proof["last_fill_timestamp_ms"]),
                    price_basis="verified_order_fills"))
        except (ValueError,KeyError,TypeError,OverflowError):continue
    return out


def apply_in_memory(rows,patch):
    return [dict(row,**patch[index]) if index in patch else row for index,row in enumerate(rows)]
