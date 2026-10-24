"""v33-46: GET-only SOL candidate protected-entry identity proof (no mutation)."""
import json,hashlib,math
from pathlib import Path
from decimal import Decimal
import config,okx_client
P=Path("users/chickenbananalab")
SYMBOL="SOL/USDT:USDT"
IDENT="55ce109ea8f15324c06f90dac091de24"
OCO_ID="4015499724766158848"

def d(n): return Decimal(str(n))

def load_evidence():
    raw=(P/"candidate_c_intent_ledger_SOL_USDT_USDT.jsonl").read_bytes()
    rows=[json.loads(x) for x in raw.splitlines() if x]
    created=[x for x in rows if x.get("event")=="created" and x.get("intent_id")==IDENT]
    assert len(created)==1,"ENTRY_NOT_UNIQUE"
    row=created[0]
    events=[x for x in rows if x.get("intent_id")==IDENT and x.get("event")=="transition"]
    assert events and events[-1]["state"]=="protection_pending","UNEXPECTED_INTENT_STATE"
    assert any(e.get("state")=="protected" and OCO_ID in e.get("protective_algo_ids",[]) for e in events),"NO_PRIOR_ALGO_OWNERSHIP"
    assert any(e.get("remote_submission_finalized") is True for e in events),"NO_FINALIZED_SUBMISSION"
    assert not any(e.get("state")=="terminal" for e in events),"ENTRY_ALREADY_TERMINAL"
    unresolved=[x for x in rows if x.get("event")=="created" and x.get("kind")=="EntryIntent" and x.get("intent_id")!=IDENT]
    for x in unresolved:
        transitions=[e for e in rows if e.get("intent_id")==x["intent_id"] and e.get("event")=="transition"]
        if not transitions or transitions[-1].get("state")!="terminal":
            raise ValueError("OTHER_UNRESOLVED_ENTRY")
    erows=(P/"candidate_c_epoch_store.jsonl").read_bytes().splitlines()
    versions=[json.loads(x) for x in erows if b'"position_id": "55ce109ea8f15324c06f90dac091de24"' in x]
    if not versions:
        versions=[json.loads(x) for x in erows if IDENT.encode() in x and b'"position_id"' in x]
        versions=[x for x in versions if x.get("position_id")==IDENT]
    assert versions,"EPOCH_MISSING"
    epoch=versions[-1]
    assert epoch.get("symbol")==SYMBOL and epoch.get("side")=="short","WRONG_EPOCH"
    return row,events,epoch,hashlib.sha256(raw).hexdigest()

def probe():
    entry,events,epoch,sha=load_evidence()
    cfg=config.UserConfig(str(P))
    c=okx_client.OkxClient(SYMBOL,cfg)
    pos1=c.fetch_position()
    assert pos1 is not None and pos1["side"]=="short","FLAT_OR_SIDE_CHANGED"
    algos=c.fetch_pending_protection_orders()
    orders=c.exchange.fetch_open_orders(SYMBOL)
    assert orders==[],"UNRESOLVED_OPEN_ORDERS"
    assert len(algos)==1,"ALGO_COUNT_NOT_ONE"
    a=algos[0]
    assert str(a["algoId"])==OCO_ID and a.get("ordType")=="oco" and a.get("state")=="live","ALGO_ID_OR_STATE_MISMATCH"
    assert a.get("side")=="buy" and a.get("instId")=="SOL-USDT-SWAP","ALGO_SIDE_OR_INST_MISMATCH"
    assert str(a.get("algoClOrdId"))==entry["attach_algo_cl_ord_id"],"ALGO_CLIENT_ID_MISMATCH"
    order=c.fetch_order_status_by_client_id(IDENT)
    assert order and str(order["clientOrderId"])==IDENT,"ENTRY_ORDER_CLIENT_ID_MISSING"
    assert order["status"]=="closed" and order["side"]=="sell","ENTRY_ORDER_NOT_SHORT_FILLED"
    fills=c.fetch_entry_order_fills(order["id"])
    assert len(fills)>=1,"FILLS_MISSING"
    q=sum((d(t["amount"]) for t in fills),Decimal(0))
    weighted=sum((d(t["amount"])*d(t["price"]) for t in fills),Decimal(0))/q
    assert q==d(entry["requested_quantity"]),"ENTRY_FILL_QTY_MISMATCH"
    assert abs(weighted-d(epoch["raw_entry_price"]))<d("0.0000001"),"ENTRY_FILL_VWAP_MISMATCH"
    assert str(pos1.get("position_id"))==str(epoch.get("exchange_position_id")),"POSITION_ID_MISMATCH"
    assert str(pos1.get("entry_timestamp_ms"))==str(epoch.get("exchange_entry_timestamp_ms")),"POSITION_CREATION_TIME_MISMATCH"
    assert d(pos1["entry_price"])==d(epoch["raw_entry_price"]) and d(pos1["contracts"])==d(epoch["remaining_contracts"]),"LIVE_EPOCH_QTY_OR_PRICE_MISMATCH"
    assert d(pos1["contracts"])==d(a["sz"]),"ALGO_PROTECTION_QTY_MISMATCH"
    assert d(a["slTriggerPx"])==d(epoch["current_stop_price"]) and d(a["tpTriggerPx"])==d(epoch["target_price"]),"OCO_EPOCH_PRICE_MISMATCH"
    assert d(entry["reserved_risk_usdt"])>0 and d(epoch["remaining_reserved_risk_usdt"])>0,"RISK_RESERVATION_MISSING"
    assert str(entry["reservation_id"])==str(epoch["reservation_id"]),"RESERVATION_ID_MISMATCH"
    assert entry["config_version_id"]==epoch["config_version_id"] and entry["config_hash"]==epoch["config_hash"],"CONFIG_ID_MISMATCH"
    pos2=c.fetch_position()
    assert all(pos1.get(k)==pos2.get(k) for k in ("side","contracts","entry_price","position_id","entry_timestamp_ms")),"POSITION_CHANGED_WHILE_PROBING"
    return dict(verdict="EXACT_RECOVERY_ELIGIBLE",account="chickenbananalab",symbol=SYMBOL,entry_intent_id=IDENT,
        order_id=str(order["id"]),entry_filled_contracts=str(q),
        entry_fill_ids=sorted(str(x.get("id")) for x in fills),
        vwap=str(weighted),live_remaining_contracts=str(pos1["contracts"]),
        exchange_entry_timestamp_ms=pos1["entry_timestamp_ms"],
        exchange_position_id=pos1["position_id"],protective_algo_id=OCO_ID,
        sl=str(a["slTriggerPx"]),tp=str(a["tpTriggerPx"]),
        intent_current_state=events[-1]["state"],prior_protected_events=sum(e.get("state")=="protected" for e in events),
        ledger_sha256=sha,mutation=False)

if __name__=="__main__":
    report=probe()
    print(json.dumps(report,ensure_ascii=False,default=str))
