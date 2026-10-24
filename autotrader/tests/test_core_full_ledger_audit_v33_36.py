"""v33-36: strict full historical CORE journal coverage regression."""
import json
from datetime import datetime, timezone
from pathlib import Path
import pytest
import core_full_ledger_audit_v33_36 as a

def row(typ="close",symbol="ETH/USDT:USDT",group="core",ident="idA"):
    r=dict(type=typ,symbol=symbol,strategy_group=group,
        side="short",time="2026-10-17T15:00:00",dry_run=False)
    if ident is not None:r["execution_id"]=ident
    if typ in ("close","reduce"):
        r.update(pnl="2.0",fee="0.2",funding_fee="0",
            okx_net_pnl="1.8",amount=.5)
    else:r.update(amount=.5,price=2400)
    return r

def fake_collect(monkeypatch,rows,proven=None):
    proven=proven or []
    positions=[dict(bound_rows=[dict(index=i,sha256=a.digest(rows[i])) for i in proven])]
    monkeypatch.setattr(a,"source_proofs",
        lambda *args:(positions if proven else [],[],[],[]))
    return a.collect(rows,Path("/unused"),observed=datetime(2026,10,18,tzinfo=timezone.utc))

def test_explicit_core_and_legacy_core_are_distinct():
    assert a.scope(row(group=None))=="LEGACY_CORE_COMPATIBLE"
    assert a.scope(row(group="core",symbol="DOGE/USDT:USDT"))=="EXPLICIT_CORE"
    assert a.scope(row(group="candidate_c",symbol="DOGE/USDT:USDT")) is None
    assert a.scope(row(group=None,symbol="DOGE/USDT:USDT")) is None
    assert a.scope(dict(row(),dry_run=True)) is None

def test_local_arithmetic_cannot_be_promoted_to_exchange_verified(monkeypatch):
    sample=[row()]
    result=fake_collect(monkeypatch,sample)
    assert result["status_counts"]=={"UNVERIFIED":1}
    assert result["numeric_accounting_counts"]=={"MATCH":1}

def test_immutable_independent_position_proof_enables_verified(monkeypatch):
    sample=[row("open",ident="open1"),row("reduce",ident="reduce1"),
            row("close",ident="close1")]
    result=fake_collect(monkeypatch,sample,proven=[0,1,2])
    assert result["status_counts"]=={"VERIFIED":3}
    assert result["core_realization_rows"]==2

def test_duplicate_execution_id_is_disputed_even_if_proof_exists(monkeypatch):
    sample=[row(ident="same"),row(ident="same")]
    result=fake_collect(monkeypatch,sample,proven=[0])
    assert result["status_counts"]=={"MISMATCH":2}
    assert all(x["reason"]=="DUPLICATE_EXECUTION_ID" for x in result["events"])
def test_local_pnl_arithmetic_mismatch_forces_dispute(monkeypatch):
    sample=row()
    sample["okx_net_pnl"]="1.70"
    result=fake_collect(monkeypatch,[sample])
    assert result["status_counts"]=={"MISMATCH":1}
    assert result["arithmetic_mismatches"][0]["numeric_invariant"]=="MISMATCH"

def test_missing_okx_net_is_incomplete_not_proven_error(monkeypatch):
    sample=row()
    sample["okx_net_pnl"]=None
    result=fake_collect(monkeypatch,[sample])
    assert result["numeric_accounting_counts"]=={"INCOMPLETE":1}
    assert result["status_counts"]=={"UNVERIFIED":1}
    assert result["ledger_net_missing_rows"]==1

def test_same_trade_id_in_two_journal_rows_is_detected(monkeypatch):
    first=row(typ="reduce",ident="r1")
    second=row(typ="close",ident="c2")
    for r in (first,second):
        r["close_execution_evidence"]=dict(trade_ids=["exchange_t1"])
    result=fake_collect(monkeypatch,[first,second])
    assert result["status_counts"]=={"MISMATCH":2}
    assert result["unmatched_crossrow_fill_id_keys"]==1

def test_different_symbols_can_share_trade_id_without_collision(monkeypatch):
    first=row(symbol="ETH/USDT:USDT",ident="r1")
    second=row(symbol="BTC/USDT:USDT",ident="c2")
    for r in (first,second):
        r["close_execution_evidence"]=dict(trade_ids=["t1"])
    result=fake_collect(monkeypatch,[first,second])
    assert result["status_counts"]=={"UNVERIFIED":2}

def test_bound_row_content_must_match_hash():
    sample=[row()]
    with pytest.raises(ValueError,match="BOUND_ROW_CONTENT_CHANGED"):
        a.row_claims(sample,[dict(bound_rows=[dict(
            index=0,sha256="bad")])],[])

def test_persistent_report_becomes_stale_for_new_core_event(tmp_path):
    source=tmp_path/"trades_log.jsonl"
    first=row()
    source.write_text(json.dumps(first)+"\n")
    data=dict(version="v33-36",journal_rows_total=1,
        original_journal_sha256="old",
        events=[dict(row_index=0,row_sha256=a.digest(first))])
    a.save_atomic(tmp_path/a.FILE,data)
    assert a.read_status(tmp_path)["source_valid"] is True
    source.write_text(source.read_text()+json.dumps(row(ident="new"))+"\n")
    assert a.read_status(tmp_path)["status"]=="STALE"
    assert a.read_status(tmp_path)["new_core_rows_since_audit"]==1

def test_audit_never_calls_order_write_methods():
    content=Path("core_full_ledger_audit_v33_36.py").read_text()
    for forbidden in ("create_order(","cancel_order(","reduce_position(",
                      "close_position(","amend_protective_stop("):
        assert forbidden not in content
    assert "may_execute=False" in content


def test_new_report_endpoint_is_login_protected_and_readonly():
    source=Path("web_app.py").read_text()
    route=source.split("@app.route('/api/ops/core-full-ledger-audit-v33-36'",1)[1]
    route=route.split("@app.route(",1)[0]
    assert "@login_required" in route
    assert "get_context(session['username'])" in route
    assert "fullaudit.read_status(ctx.dir)" in route
    assert "request.args.get('details') != '1'" in route
    assert "create_order(" not in route
