"""v33-31 read-only owned OCO exchange evidence tests."""
from collections import Counter
from types import SimpleNamespace
from pathlib import Path
import json
import core_external_close_audit_v33_31 as a

S="XRP/USDT:USDT"
OID="4016640133598793728"
ALGO="4016176257579196417"
LIFE="3853548498531897345:1792193779594:short"

def row():
    return dict(type="close",symbol=S,side="short",
        time="2026-10-17T12:26:58",reason="stop_loss",
        amount=6.52,close_price=1.308,dry_run=False,
        strategy_group="core",okx_net_pnl=-1.2,
        execution_id="core-external-close:"+LIFE,
        close_reason_evidence=dict(
            source="owned_algo_order_fills",reason="stop_loss",
            order_id=OID,algo_id=ALGO,lifecycle_id=LIFE,
            trade_ids=["935748166"],exit_price=1.308),
        close_execution_evidence=dict(
            price_basis="verified_owned_protection_fills",
            filled_contracts=6.52,vwap=1.308,
            algo_id=ALGO,order_id=OID,lifecycle_id=LIFE,
            trade_ids=["935748166"]))

def make_client(order_override=None,trades_override=None):
    order=dict(id=ALGO,status="closed",side="buy",
        filled=6.52,remaining=0,symbol=S,
        info=dict(ordId=OID,algoId=ALGO,reduceOnly="true"))
    trade=dict(order=OID,id="935748166",side="buy",symbol=S,
        amount=6.52,price=1.308,
        info=dict(ordId=OID,tradeId="935748166"))
    if order_override:order.update(order_override)
    if trades_override is not None:trades=trades_override
    else:trades=[trade]
    return SimpleNamespace(exchange=SimpleNamespace(
        fetch_order=lambda *a:order),
        fetch_trades_for_order=lambda *a,**k:trades)

def test_actual_okx_oco_ccxt_algo_id_and_raw_child_order_id():
    r=row()
    result=a.classification(r,make_client(),Counter({r["execution_id"]:1}))
    assert result[0]=="VERIFIED" and result[2]["fills"]==1

def test_proof_without_source_is_not_reconciled():
    r=row();r["close_execution_evidence"]=None
    assert a.classification(r,None,Counter())[0]=="UNVERIFIED"

def test_exact_algo_id_required_for_real_owned_oco():
    r=row()
    outcome=a.classification(r,make_client(
        order_override=dict(info=dict(ordId=OID,algoId="BAD",reduceOnly="true"))),
        Counter())
    assert outcome[0]=="ALERT"

def test_quantity_or_fill_id_mismatch_blocks_verification():
    r=row();r["close_execution_evidence"]["trade_ids"]=["other"]
    assert a.classification(r,make_client(),Counter())[0]=="ALERT"
    r=row()
    bad=make_client(trades_override=[dict(
        order=OID,id="935748166",side="buy",symbol=S,
        amount=6.51,price=1.308,
        info=dict(ordId=OID,tradeId="935748166"))])
    assert a.classification(r,bad,Counter())[0]=="ALERT"
def test_duplicate_lifecycle_id_alert():
    r=row()
    assert a.classification(r,None,Counter({r["execution_id"]:2}))[0]=="ALERT"

def test_incomplete_exchange_lookup_stays_pending():
    r=row()
    client=make_client(trades_override=[])
    assert a.classification(r,client,Counter())[0]=="PENDING"

def test_no_exchange_calls_after_verified_cache():
    r=row()
    first=a.evaluate([r],lambda sym:make_client())
    assert first["status_counts"]=={"VERIFIED":1}
    again=a.evaluate([r],lambda sym:1/0,previous=first)
    assert again["status_counts"]=={"VERIFIED":1}
    assert again["may_execute"] is False

def test_verification_does_not_write_journal(tmp_path):
    r=row()
    original=json.dumps(r)+"\n"
    (tmp_path/"trades_log.jsonl").write_text(original)
    report=a.run(tmp_path,lambda sym:make_client())
    assert report["status_counts"]=={"VERIFIED":1}
    assert (tmp_path/"trades_log.jsonl").read_text()==original
    assert a.read_status(tmp_path)["may_execute"] is False

def test_unverified_remains_unverified_and_never_promotes():
    r=row()
    r["close_reason_evidence"]=None
    result=a.evaluate([r],lambda symbol:1/0)
    assert result["status_counts"]=={"UNVERIFIED":1}

def test_code_contains_no_order_writes():
    src=Path("core_external_close_audit_v33_31.py").read_text()
    for forbidden in ("create_order(", "cancel_order(", "amend_protective_stop(",
                      "gemini_analyzer.","openai_analyzer."):
        assert forbidden not in src
