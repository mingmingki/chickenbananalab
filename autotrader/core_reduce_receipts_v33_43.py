"""v33-43: read-only audit of CORE reduction journal vs exact order-fill proof.

No OKX mutations or journal corrections. Funding and entry fees are outside
the verified exit-fill-only net scope. Past unverified rows remain unverified.
"""
from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import hashlib
import json
import os
import tempfile

import core_full_ledger_audit_v33_36 as ledger
from core_reduce_quantity_v33_43 import _dec as D

FILE="core_reduce_receipt_parity_v33_43.json"
KST=timezone(timedelta(hours=9))

def evaluate(row):
    base=dict(symbol=row.get("symbol"),time=row.get("time"),
              execution_id=row.get("execution_id"))
    ev=row.get("close_execution_evidence")
    if row.get("pnl_source")!="order_fills" or not isinstance(ev,dict):
        return dict(base,status="LEGACY_OR_UNVERIFIED_RECEIPT")
    try:
        oid=ev["order_id"]
        ids=ev["trade_ids"]
        if not isinstance(oid,str) or not oid.strip() or not isinstance(ids,list) or not ids:
            raise ValueError("MISSING_IDENTIFIERS")
        if any(not str(x).strip() for x in ids) or len(ids)!=len(set(str(x) for x in ids)):
            raise ValueError("DUPLICATED_FILL_IDS")
        if not row.get("execution_id"):
            raise ValueError("MISSING_CLIENT_EXECUTION_ID")
        filled=D(ev["filled_contracts"])
        amount=D(row["amount"])
        price=D(row["close_price"])
        vwap=D(ev["vwap"])
        gross=D(row["pnl"])
        fee=D(row["fee"])
        net=D(row["okx_net_pnl"])
        if filled<=0 or amount<=0 or price<=0 or fee<0:
            raise ValueError("BAD_NUMERIC")
        if abs(filled-amount)>Decimal("0.00000001"):
            raise ValueError("FILLED_JOURNAL_QTY_MISMATCH")
        if abs(price-vwap)>Decimal("0.00000001"):
            raise ValueError("EXIT_VWAP_MISMATCH")
        if abs((gross-fee)-net)>Decimal("0.000001"):
            raise ValueError("NET_PNL_INVARIANT_MISMATCH")
        return dict(base,status="EXACT_EXIT_FILL_RECEIPT_PARITY",
                    order_id=oid,fill_count=len(ids),
                    filled_contracts=str(filled),gross_usdt=str(gross),
                    fee_usdt=str(fee),exit_only_net_usdt=str(net))
    except (ValueError,KeyError,TypeError,ArithmeticError) as exc:
        return dict(base,status="RECEIPT_PARITY_INVALID",reason_code=str(exc)[:110])

def audit(user_dir):
    root=Path(user_dir)
    raw=(root/"trades_log.jsonl").read_bytes()
    rows=[json.loads(line) for line in raw.splitlines() if line.strip()]
    events=[]
    for i,row in enumerate(rows):
        if row.get("type")!="reduce" or row.get("strategy_group")!="core":
            continue
        ev=evaluate(row)
        ev.update(row_index=i,row_sha256=ledger.digest(row))
        events.append(ev)
    ids={}
    for ev in events:
        if ev["status"]=="EXACT_EXIT_FILL_RECEIPT_PARITY":
            oid=(ev["symbol"],ev["order_id"])
            if oid in ids:
                ev["status"]="DUPLICATE_EXCHANGE_ORDER"
                for other in events:
                    if (other.get("symbol"),other.get("order_id"))==oid:
                        other["status"]="DUPLICATE_EXCHANGE_ORDER"
            ids[oid]=ev["row_index"]
    counts=dict(Counter(e["status"] for e in events))
    return dict(version="v33-43",mode="READ_ONLY_CORE_REDUCE_EXACT_RECEIPT_PARITY",
        observed_at=datetime.now(KST).isoformat(),may_execute=False,
        may_correct_ledger=False,journal_sha256=hashlib.sha256(raw).hexdigest(),
        total_core_reduce_rows=len(events),status_counts=counts,records=events,
        limitations=[
            "Exit-only verified fill net excludes funding and entry fees.",
            "Historical reductions without IDs are not zero-PnL and remain unverified.",
            "An exact exit-fill receipt is not automatically complete lifecycle PnL.",
            "This snapshot does not prove future order outcomes or profitability.",
            "No journal, position, or trading-engine mutation."])

def save(path,result):
    path=Path(path)
    fd,tmp=tempfile.mkstemp(dir=path.parent,prefix=".v33_43_receipts_")
    try:
        os.fchmod(fd,0o600)
        with os.fdopen(fd,"w",encoding="utf-8") as f:
            json.dump(result,f,ensure_ascii=False,indent=2,allow_nan=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)

def read_status(user_dir):
    root=Path(user_dir)
    try:
        result=json.loads((root/FILE).read_text())
        if result.get("version")!="v33-43":
            return dict(status="INVALID_VERSION",may_execute=False)
        rows=[json.loads(s) for s in (root/"trades_log.jsonl").read_bytes().splitlines() if s.strip()]
        invalid=sum(type(e.get("row_index"))!=int
            or e["row_index"]<0 or e["row_index"]>=len(rows)
            or ledger.digest(rows[e["row_index"]])!=e["row_sha256"]
            for e in result["records"])
        return dict(result,status="STALE" if invalid else "KNOWN",
                    source_valid=(invalid==0),invalidated_cases=invalid,may_execute=False)
    except (OSError,ValueError,TypeError,KeyError,IndexError):
        return dict(status="UNAVAILABLE",may_execute=False)

def main():
    import argparse
    p=argparse.ArgumentParser()
    p.add_argument("--user-dir",required=True)
    opts=p.parse_args()
    result=audit(opts.user_dir)
    save(Path(opts.user_dir)/FILE,result)
    print(json.dumps(dict(version=result["version"],rows=result["total_core_reduce_rows"],
        counts=result["status_counts"])))
if __name__=="__main__":
    raise SystemExit(main())
