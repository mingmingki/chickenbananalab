"""v33-32 strict historical chain and tamper-proof sidecar tests."""
from types import SimpleNamespace
from pathlib import Path
import json
import core_external_close_recovery_v33_32 as r

def source_row():
    return dict(type="close",strategy_group="core",dry_run=False,
        symbol="XRP/USDT:USDT",side="short",
        time="2026-10-13T10:17:25",reason="external_close_unknown",
        amount=6.36,close_price=None,okx_net_pnl=-10.868411,
        execution_id="core-external-close:pos:1791853337461:short")

def oco():
    return dict(algoId="a1",algoClOrdId="ca1",
        cTime="1791853337461",triggerTime="1791854239337",
        side="buy",state="effective",actualSide="sl",
        ordId="close1",ordIdList=["close1"])

def position():
    return dict(info=dict(posId="pos",cTime="1791853337461",
                          uTime="1791854239338"))

def test_identity_exact():
    assert r.identity(source_row())==("pos",1791853337461,"short")
    bad=dict(source_row(),side="long")
    import pytest
    with pytest.raises(ValueError):r.identity(bad)

def test_get_entry_order_must_own_attached_algo():
    created=1791853337461
    trade=dict(order="entry1",side="sell",timestamp=created)
    good=dict(info=dict(ordId="entry1",state="filled",
        reduceOnly="false",attachAlgoOrds=[dict(attachAlgoClOrdId="ca1")]))
    fake=SimpleNamespace(exchange=SimpleNamespace(
        fetch_my_trades=lambda *a,**kw:[trade],
        fetch_order=lambda oid,sym:good))
    pair,why=r.find_entry(fake,"XRP/USDT:USDT",created,"short",oco())
    assert pair[0]=="entry1" and why is None

def test_entry_without_attachment_not_assumed():
    created=1791853337461
    trade=dict(order="entry1",side="sell",timestamp=created)
    unrelated=dict(info=dict(ordId="entry1",state="filled",
        reduceOnly="false",attachAlgoOrds=[]))
    fake=SimpleNamespace(exchange=SimpleNamespace(
        fetch_my_trades=lambda *a,**kw:[trade],
        fetch_order=lambda oid,sym:unrelated))
    pair,why=r.find_entry(fake,"XRP/USDT:USDT",created,"short",oco())
    assert pair is None and why=="ENTRY_ATTACHMENT_NOT_UNIQUE"

def test_position_and_oco_end_must_match_before_network_fill_lookup():
    row=source_row()
    wrong=position()
    wrong["info"]["uTime"]="1791850000000"
    proof,reason=r.prove_one(row,None,[wrong],[oco()])
    assert proof is None
    assert reason=="POSITION_END_AND_OCO_TRIGGER_DISAGREE"

def test_missing_oco_refuses_reconstruction():
    proof,reason=r.prove_one(source_row(),None,[position()],[])
    assert proof is None and reason=="OCO_NOT_UNIQUE"

def test_historical_proof_rejects_multiple_oco():
    proof,reason=r.prove_one(source_row(),None,[position()],[oco(),oco()])
    assert proof is None and reason=="OCO_NOT_UNIQUE"

def test_sidecar_row_hash_invalidates_tampered_original(tmp_path):
    p=tmp_path/"trades_log.jsonl"
    row=source_row();p.write_text(json.dumps(row)+"\n")
    entry=dict(status="RECONSTRUCTED_EXCHANGE_PROOF",
        row_index=0,sha256=r.row_hash(row),proof=dict(chain="EXACT_POSITION_ENTRY_ATTACHED_OCO_CHILD_ORDER_AND_FILLS"))
    r.save(tmp_path/r.FILE,dict(version="v33-32",
        original_journal_sha256="ignored",results=[entry]))
    assert r.read_report(tmp_path)["currently_bound_rows"]==1
    p.write_text(json.dumps(dict(row,amount=6.4))+"\n")
    assert r.read_report(tmp_path)["currently_bound_rows"]==0

def test_audit_is_readonly_and_never_promotes_without_proof():
    src=Path("core_external_close_recovery_v33_32.py").read_text()
    assert "match_owned_protection_close" in src
    assert "def save(" in src
    for forbidden in ("create_order(","cancel_order(","amend_protective_stop(",
                      "openai_analyzer.","gemini_analyzer."):
        assert forbidden not in src

def test_authenticated_report_route_cannot_call_exchange():
    src=Path("web_app.py").read_text()
    section=src.split("@app.route('/api/ops/external-close-recovery-v33-32'",1)[1]
    section=section.split("@app.route(",1)[0]
    assert "@login_required" in section
    assert "get_context(session['username'])" in section
    assert "recovery.read_report" in section
    assert "fetch_order(" not in section
    assert "create_order(" not in section
