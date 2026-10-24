"""v33-27 strictly GET-only historical CORE close receipt verification.

Original trades_log.jsonl is NEVER edited; sidecar proof is bound to exact
journal row hash, exchange order-id, per-fill trade IDs and quantity.
No guesswork: absent exchange proof is excluded.
"""
from __future__ import annotations
from decimal import Decimal,InvalidOperation
from pathlib import Path
import datetime as dt
import hashlib,json,math,os,tempfile,time

FILE="core_exit_verified_receipts_v33_27.json"
KST=dt.timezone(dt.timedelta(hours=9))
CORE={"BTC/USDT:USDT","ETH/USDT:USDT","XRP/USDT:USDT","ADA/USDT:USDT"}
ALLOWED={"verified_order_fills","verified_owned_protection_fills"}
SCHEMA=1


def row_hash(row):
    return hashlib.sha256(json.dumps(row,ensure_ascii=False,sort_keys=True,
        separators=(",",":"),allow_nan=False).encode()).hexdigest()


def dec(x):
    if x is None or isinstance(x,bool):raise ValueError("missing_numeric")
    d=Decimal(str(x))
    if not d.is_finite() or d<=0:raise ValueError("invalid_numeric")
    return d


def valid_original(row):
    if not (isinstance(row,dict) and row.get("type")=="close"
            and row.get("dry_run") is False and row.get("strategy_group")=="core"
            and row.get("symbol") in CORE and row.get("side") in ("long","short")
            and not row.get("execution_id")):return False
    p=row.get("close_execution_evidence")
    if not isinstance(p,dict) or p.get("price_basis") not in ALLOWED:return False
    if not p.get("order_id") or not p.get("trade_ids"):return False
    if not isinstance(p["trade_ids"],list) or len(set(map(str,p["trade_ids"])))!=len(p["trade_ids"]):return False
    try:
        if dec(p["filled_contracts"])!=dec(row["amount"]):return False
        if dec(p["vwap"])!=dec(row["close_price"]):return False
        if int(p["first_fill_timestamp_ms"])>int(p["last_fill_timestamp_ms"]):return False
        stamp=dt.datetime.fromisoformat(row["time"])
        if stamp.tzinfo is None:stamp=stamp.replace(tzinfo=KST)
        if not 0<stamp.timestamp()*1000-int(p["last_fill_timestamp_ms"])<300000:return False
    except (ValueError,TypeError,KeyError,InvalidOperation,OverflowError):return False
    return True


def verify_against_exchange(client,row):
    """Read-only exact order-ID and fill set. No timeline fuzzy matching."""
    import core_close_fill_evidence
    proof=row["close_execution_evidence"]
    order_id=str(proof["order_id"])
    receipt={"id":order_id,"symbol":row["symbol"]}
    position={"side":row["side"],"contracts":row["amount"]}
    got=core_close_fill_evidence.verified_close_fills(
        client,position,receipt,start_ms=int(proof["first_fill_timestamp_ms"]))
    if not isinstance(got,dict):return None
    if got.get("price_basis")!="verified_order_fills":return None
    if str(got.get("order_id"))!=order_id:return None
    if dec(got["filled_contracts"])!=dec(proof["filled_contracts"]):return None
    if dec(got["vwap"])!=dec(proof["vwap"]):return None
    if set(map(str,got["trade_ids"]))!=set(map(str,proof["trade_ids"])):return None
    if abs(int(got["first_fill_timestamp_ms"])-int(proof["first_fill_timestamp_ms"]))>1000:return None
    if abs(int(got["last_fill_timestamp_ms"])-int(proof["last_fill_timestamp_ms"]))>1000:return None
    return {"order_id":order_id,
            "trade_ids":sorted(map(str,got["trade_ids"])),
            "filled_contracts":float(got["filled_contracts"]),
            "vwap":float(got["vwap"]),
            "first_fill_timestamp_ms":got["first_fill_timestamp_ms"],
            "last_fill_timestamp_ms":got["last_fill_timestamp_ms"],
            "proof_type":"EXCHANGE_ORDER_ID_AND_FILLS_EXACT_MATCH"}


def collect(rows,client_factory,*,max_verify=50):
    """All rows are supplied from immutable read; never modifies them."""
    matched=[];failure=[];clients={}
    seen_orders=set();seen_fills=set()
    for index,row in enumerate(rows):
        if not valid_original(row):continue
        p=row["close_execution_evidence"]
        identity=(row["symbol"],str(p["order_id"]))
        if identity in seen_orders:
            failure.append({"row_index":index,"reason":"duplicate_order_id"})
            continue
        seen_orders.add(identity)
        if len(matched)+len(failure)>=max_verify:break
        try:
            client=clients.setdefault(row["symbol"],client_factory(row["symbol"]))
            proof=verify_against_exchange(client,row)
            if not proof:
                failure.append({"row_index":index,"reason":"exchange_verification_failed"})
                continue
            ids={(row["symbol"],proof["order_id"],tid) for tid in proof["trade_ids"]}
            if seen_fills.intersection(ids):
                failure.append({"row_index":index,"reason":"duplicate_fill_id"})
                continue
            seen_fills.update(ids)
            matched.append({
                "row_index":index,"row_sha256":row_hash(row),
                "symbol":row["symbol"],"side":row["side"],
                "journal_time":row["time"],
                # This is a local provenance token, NEVER an alleged OKX ID.
                "derived_execution_key":"verified-legacy-close:"+proof["order_id"],
                "proof":proof,
            })
        except Exception as e:
            failure.append({"row_index":index,"reason":"lookup_unavailable",
                            "error_type":type(e).__name__})
    return {"schema_version":SCHEMA,"mode":"EXCHANGE_GET_VERIFIED_ONLY",
            "recorded_at":dt.datetime.now(KST).isoformat(),
            "original_journal_sha256":None,
            "verified":matched,"unconfirmed":failure,
            "principle":"No original journal mutations; close proof is exact exchange order + fill set."}


def save_atomic(path,body):
    target=Path(path)
    target.parent.mkdir(parents=True,exist_ok=True)
    fd,temp=tempfile.mkstemp(dir=str(target.parent),prefix=".v33_27_proof_")
    try:
        os.fchmod(fd,0o600)
        with os.fdopen(fd,"w",encoding="utf-8") as f:
            json.dump(body,f,ensure_ascii=False,allow_nan=False,indent=2)
            f.flush();os.fsync(f.fileno())
        os.replace(temp,target)
    finally:
        if os.path.exists(temp):os.unlink(temp)


def load_valid_overlay(rows,user_dir,*,path=None):
    """Fail-closed in-memory lookup; no file write, no trading authority."""
    p=Path(path) if path else Path(user_dir)/FILE
    if not p.is_file():return {}
    try:body=json.loads(p.read_text())
    except (ValueError,OSError):return {}
    if body.get("schema_version")!=SCHEMA or not isinstance(body.get("verified"),list):return {}
    seen_orders=set();seen_rows=set();out={}
    for item in body["verified"]:
        try:
            index=item["row_index"]
            if type(index)!=int or index>=len(rows) or index<0 or index in seen_rows:continue
            row=rows[index]
            if row_hash(row)!=item["row_sha256"] or not valid_original(row):continue
            proof=item["proof"]
            if proof.get("proof_type")!="EXCHANGE_ORDER_ID_AND_FILLS_EXACT_MATCH":continue
            if str(proof["order_id"])!=str(row["close_execution_evidence"]["order_id"]):continue
            if item["derived_execution_key"]!="verified-legacy-close:"+str(proof["order_id"]):continue
            if dec(proof["vwap"])!=dec(row["close_price"]) or dec(proof["filled_contracts"])!=dec(row["amount"]):continue
            if set(map(str,proof["trade_ids"]))!=set(map(str,row["close_execution_evidence"]["trade_ids"])):continue
            order_ident=(row["symbol"],str(proof["order_id"]))
            if order_ident in seen_orders:continue
            seen_rows.add(index);seen_orders.add(order_ident)
            out[index]=item["derived_execution_key"]
        except (KeyError,ValueError,TypeError,InvalidOperation):continue
    return out


def apply_in_memory(rows,overlay):
    return [dict(row,execution_id=overlay[i]) if i in overlay else row
            for i,row in enumerate(rows)]


def main():
    import argparse,config,okx_client
    parser=argparse.ArgumentParser()
    parser.add_argument("--user-dir",required=True)
    parser.add_argument("--max-verify",type=int,default=50)
    parser.add_argument("--output",required=True)
    args=parser.parse_args()
    p=Path(args.user_dir)/"trades_log.jsonl"
    raw=p.read_bytes()
    rows=[json.loads(x) for x in raw.decode().splitlines() if x.strip()]
    user_cfg=config.UserConfig(str(args.user_dir))
    report=collect(rows,lambda sym:okx_client.OkxClient(sym,user_cfg),max_verify=args.max_verify)
    report["original_journal_sha256"]=hashlib.sha256(raw).hexdigest()
    save_atomic(args.output,report)
    print("VERIFIED",len(report["verified"]),"UNCONFIRMED",len(report["unconfirmed"]))
    print("OUTPUT",args.output)


if __name__=="__main__":main()
