"""v33-42: exact CCXT precision replay of 26 legacy quantity discrepancies.

Non-executing historical analysis only. A match demonstrates that the current
exchange amount precision rule explains the observed 0.01-contract difference.
It does NOT prove historical request payloads or journal-to-order ownership.
"""
from collections import Counter
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
import hashlib
import json
import os
import tempfile

import core_legacy_action_correlation_v33_41 as prior
import core_full_ledger_audit_v33_36 as ledger
import core_legacy_full_close_v33_39 as close39

FILE="core_legacy_precision_replay_v33_42.json"
VERSION="v33-42"
EXPECTED=26

def money(v):
    if isinstance(v,bool) or v is None:
        raise ValueError("INVALID_NUMBER")
    d=Decimal(str(v))
    if not d.is_finite():
        raise ValueError("INVALID_NUMBER")
    return d

def classify_replay(row, candidate, market, normalized):
    original=money(row["amount"])
    observed=money(candidate["observed_qty"])
    processed=money(normalized)
    if original<=0 or observed<=0 or processed<=0:
        raise ValueError("NONPOSITIVE_QUANTITY")
    if processed > original:
        return "PRECISION_ROUNDING_INCREASES_ORDER_QTY_UNEXPECTEDLY"
    if processed==observed and original>processed:
        return "CURRENT_CCXT_PRECISION_REPRODUCES_EXCHANGE_QTY"
    if processed==observed:
        return "NO_PRECISION_DIFFERENCE"
    return "PRECISION_REPLAY_NOT_MATCHED"

def audit(user_dir, client_factory):
    root=Path(user_dir)
    upstream=prior.read_status(root)
    if upstream.get("version")!="v33-41" or upstream.get("status")!="KNOWN" or not upstream.get("source_valid"):
        raise ValueError("V33_41_SOURCE_STALE")
    rows=[json.loads(s) for s in (root/"trades_log.jsonl").read_bytes().splitlines() if s.strip()]
    mismatches=[r for r in upstream["records"]
                if r["status"]=="QUANTITY_DISCREPANCY_DIAGNOSTIC"]
    if len(mismatches)!=EXPECTED or len(upstream["records"])!=59:
        raise ValueError("UNEXPECTED_COHORT")
    clients={}
    markets={}
    cases=[]
    for r in mismatches:
        index=r["row_index"]
        if type(index)!=int or index<0 or index>=len(rows) or ledger.digest(rows[index])!=r["row_sha256"]:
            raise ValueError("ROW_HASH_STALE")
        symbol=r["symbol"]
        if symbol not in clients:
            client=client_factory(symbol)
            client.ensure_markets_loaded()
            clients[symbol]=client
            market=client.exchange.market(symbol)
            markets[symbol]=dict(
                contract_size=str(market.get("contractSize")),
                precision_amount=str((market.get("precision") or {}).get("amount")),
                lot_step=str((market.get("info") or {}).get("lotSz")),
                market_id=str(market.get("id") or ""),
                metadata_at_audit_not_historic=True)
        original=rows[index]["amount"]
        normalized=clients[symbol].exchange.amount_to_precision(symbol,original)
        nearest=r.get("nearest_order")
        if not isinstance(nearest,dict):
            raise ValueError("MISSING_NEAREST_ORDER")
        status=classify_replay(rows[index],nearest,markets[symbol],normalized)
        observed=money(nearest["observed_qty"])
        cases.append(dict(row_index=index,row_sha256=r["row_sha256"],
            time=r["time"],symbol=symbol,
            journal_amount_repr=repr(original),
            journal_amount_decimal=str(money(original)),
            normalized_ccxt_contracts=str(normalized),
            nearest_temporal_order_id=nearest["order_id"],
            nearest_order_filled_contracts=str(observed),
            quantity_difference_contracts=str(money(original)-observed),
            classification=status,
            historical_submission_payload_proven=False,
            journal_row_ownership_proven=False,
            correction_eligible=False))
    counts=dict(Counter(r["classification"] for r in cases))
    return dict(version=VERSION,mode="READ_ONLY_HISTORIC_CCXT_PRECISION_REPLAY",
        observed_at=datetime.now(close39.KST).isoformat(),
        may_execute=False,may_correct_ledger=False,
        source_v33_41_sha256=hashlib.sha256((root/prior.FILE).read_bytes()).hexdigest(),
        journal_sha256=hashlib.sha256((root/"trades_log.jsonl").read_bytes()).hexdigest(),
        unmatched_cohort=EXPECTED,
        precision_reproduction_counts=counts,
        precision_reproduced_count=counts.get("CURRENT_CCXT_PRECISION_REPRODUCES_EXCHANGE_QTY",0),
        legacy_journal_order_ownership_proven=0,
        ledger_corrections_authorized=0,
        market_metadata=markets,
        cases=cases,
        limitations=[
            "CCXT amount_to_precision applied TODAY reproduces the observed quantity; historical payloads are not available.",
            "Current instrument metadata may differ from September historic metadata.",
            "Exchange order IDs are nearest temporal candidates, not proven historic journal ownership.",
            "No change to original journal or trading configuration, no trade or order mutations.",
            "A quantity difference is not an independently verified PnL error or recovered return."])

def save(path, data):
    path=Path(path)
    fd,tmp=tempfile.mkstemp(prefix=".v33_42_",dir=path.parent)
    try:
        os.fchmod(fd,0o600)
        with os.fdopen(fd,"w",encoding="utf-8") as f:
            json.dump(data,f,ensure_ascii=False,indent=2,allow_nan=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

def read_status(user_dir):
    root=Path(user_dir)
    try:
        d=json.loads((root/FILE).read_text())
        if d.get("version")!=VERSION:
            return dict(status="INVALID_VERSION",may_execute=False)
        if hashlib.sha256((root/prior.FILE).read_bytes()).hexdigest()!=d["source_v33_41_sha256"]:
            return dict(status="STALE_PARENT",may_execute=False)
        if prior.read_status(root).get("status")!="KNOWN":
            return dict(status="STALE_PARENT",may_execute=False)
        rows=[json.loads(s) for s in (root/"trades_log.jsonl").read_bytes().splitlines() if s.strip()]
        invalid=sum(type(c.get("row_index"))!=int
            or c["row_index"]<0 or c["row_index"]>=len(rows)
            or ledger.digest(rows[c["row_index"]])!=c["row_sha256"]
            for c in d["cases"])
        return dict(d,status="KNOWN" if invalid==0 else "STALE",
            source_valid=invalid==0,invalidated_cases=invalid,
            may_execute=False,may_correct_ledger=False)
    except (OSError,ValueError,KeyError,IndexError,TypeError):
        return dict(status="UNAVAILABLE",may_execute=False)

def main():
    import argparse
    import config
    import okx_client
    p=argparse.ArgumentParser()
    p.add_argument("--user-dir",required=True)
    arg=p.parse_args()
    cfg=config.UserConfig(arg.user_dir)
    result=audit(arg.user_dir,lambda symbol:okx_client.OkxClient(symbol,cfg))
    save(Path(arg.user_dir)/FILE,result)
    print(json.dumps(dict(version=result["version"],rows=result["unmatched_cohort"],
        classifications=result["precision_reproduction_counts"]),ensure_ascii=False))

if __name__=="__main__":
    raise SystemExit(main())
