"""Regression suite for the v33-40 non-executing historical review."""
import json
import hashlib
from pathlib import Path
from types import SimpleNamespace
from datetime import datetime

import pytest
import core_legacy_gap_audit_v33_37 as older
import core_legacy_full_close_v33_39 as full
import core_legacy_provenance_v33_40 as mod
import core_full_ledger_audit_v33_36 as ledger


TIME = "2026-09-11T18:08:31"
SYMBOL = "ETH/USDT:USDT"


def row(qty=1):
    return dict(time=TIME,amount=qty,symbol=SYMBOL,side="long",
                type="reduce",pnl=0,fee=0,close_price=10,
                pnl_source="estimated",reason="position_ai_reduce_50")


def fills(qtys=(.25,.75),oid="ORD1"):
    ts=full.date_ms(TIME)
    return [dict(timestamp=ts+20+i,id=f"F{i}",order=oid,side="sell",
                 amount=q,price=10+i,symbol=SYMBOL)
            for i,q in enumerate(qtys)]


class FakeExchange:
    def __init__(self, oid="ORD1"):
        self.oid=oid
    def fetch_order(self, order_id, symbol):
        return dict(status="closed",info=dict(ordId=order_id,reduceOnly=True),
                    symbol=symbol,side="sell",filled=1)


def proof_bills(oid="ORD1"):
    return {f"B{i}":dict(type="2",ccy="USDT",ordId=oid,tradeId=f"F{i}",
                        pnl="0",fee="-.01",balChg="-.01")
            for i in range(2)}


def fake_factory(symbol):
    assert symbol == SYMBOL
    return SimpleNamespace(exchange=FakeExchange())


def case():
    return dict(index=0,row_sha256=ledger.digest(row()),type="reduce",
                symbol=SYMBOL,time=TIME)


def test_split_order_missing_from_old_single_fill_probe():
    r=row()
    trades=fills()
    old, reason=older.candidate_trade(r,trades)
    assert not old and reason=="NO_TIME_QTY_SIDE_CANDIDATE"
    group=full.order_candidates(r,trades)
    assert len(group)==1
    assert group[0]["n_fills"]==2
    assert group[0]["trade_ids"]==["F0","F1"]
    assert full.verify_order(fake_factory(SYMBOL),SYMBOL,r,group[0])
    net=full.settle_order(group[0],proof_bills())
    assert net["exchange_cash_change_usdt"]=="-0.02"


def test_split_exchange_bills_not_row_ownership(monkeypatch,tmp_path):
    monkeypatch.setattr(mod.legacy,"fetch_symbol_day",lambda *a:fills())
    monkeypatch.setattr(mod.bill_api,"fetch_bills",lambda *a,**k:(proof_bills(),2))
    rec,missing=mod.inspect_reductions(tmp_path,[row()],[case()],
                                          dict(records=[]),dict(cases=[]),fake_factory)
    assert not missing
    assert rec[0]["status"]=="EXCHANGE_SETTLED_SPLIT_ORDER_TEMPORAL_CANDIDATE"
    assert rec[0]["journal_row_ownership_proven"] is False
    assert rec[0]["correction_eligible"] is False
    assert rec[0]["exchange_vwap"]=="10.75"
    assert len(rec[0]["settlement"]["bill_ids"])==2


def test_nearby_wrong_qty_not_called_execution_absence(monkeypatch,tmp_path):
    monkeypatch.setattr(mod.legacy,"fetch_symbol_day",lambda *a:fills((.2,.3)))
    rec,_=mod.inspect_reductions(tmp_path,[row()],[case()],
                                 dict(records=[]),dict(cases=[]),fake_factory)
    assert rec[0]["status"]=="NEARBY_SIDE_FILLS_NO_ORDER_QTY_MATCH"
    assert rec[0]["correction_eligible"] is False


def test_cross_report_order_collision_blocks_candidate(monkeypatch,tmp_path):
    monkeypatch.setattr(mod.legacy,"fetch_symbol_day",lambda *a:fills())
    other=dict(records=[dict(status="EXCHANGE_SETTLEMENT_TEMPORAL_CANDIDATE",
                             symbol=SYMBOL,candidate_order=dict(order_id="ORD1"),
                             settlement=dict(bill_ids=["B1"]))])
    rec,_=mod.inspect_reductions(tmp_path,[row()],[case()],
                                 other,dict(cases=[]),fake_factory)
    assert rec[0]["status"]=="CROSS_COHORT_ORDER_COLLISION"


def test_row_cannot_be_owned_from_time_qty():
    r=row()
    proof=dict(records=[dict(index=0,row_sha256=ledger.digest(r),symbol=SYMBOL,
                             time=TIME,status="EXCHANGE_SETTLEMENT_TEMPORAL_CANDIDATE",
                             candidate_order=dict(order_id="ORD1"),
                             journal_row_ownership_proven=False)])
    details=mod.report_full_ownership(proof,[r])
    assert details[0]["proof_reason"]=="NO_AUTHORITATIVE_ORDER_OR_FILL_ID_IN_JOURNAL"
    assert details[0]["correction_eligible"] is False


def test_authenticated_api_is_read_only():
    text=Path("web_app.py").read_text()
    anchor="@app.route('/api/ops/core-legacy-provenance-v33-40', methods=['GET'])"
    assert text.count(anchor)==1
    route=text.split(anchor,1)[1].split("@app.route(",1)[0]
    for expected in ("@login_required","get_context(session['username'])",
                     "mod.read_status(ctx.dir)","request.args.get('details')"):
        assert expected in route
    assert "create_order(" not in route and "cancel_order(" not in route
    code=Path("core_legacy_provenance_v33_40.py").read_text()
    assert "create_order(" not in code and "cancel_order(" not in code
    assert "trades_log.jsonl" in code and "may_correct_ledger=False" in code


def test_parent_checksum_prevents_unnoticed_report_replacement(tmp_path):
    original=row()
    folder=tmp_path
    (folder/"trades_log.jsonl").write_text(json.dumps(original)+"\n")
    for f in (mod.legacy.FILE,mod.fullclose.FILE,mod.partial80.FILE):
        (folder/f).write_text("{}")
    checks={f:hashlib.sha256((folder/f).read_bytes()).hexdigest()
            for f in (mod.legacy.FILE,mod.fullclose.FILE,mod.partial80.FILE)}
    metadata=dict(version=mod.VERSION,parent_reports_sha256=checks,
                  full_close_evidence=[dict(row_index=0,row_sha256=ledger.digest(original))],
                  unmatched_reductions=[])
    (folder/mod.FILE).write_text(json.dumps(metadata))
    assert mod.read_status(folder)["status"]=="KNOWN"
    (folder/mod.legacy.FILE).write_text('{"changed":true}')
    assert mod.read_status(folder)["status"]=="STALE_PARENT"
