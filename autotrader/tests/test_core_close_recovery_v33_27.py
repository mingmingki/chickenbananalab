import json
from pathlib import Path
from datetime import datetime, timedelta, timezone

import pytest
import core_legacy_close_proof_v33_27 as p
import core_close_recovery_status_v33_27 as status

KST=timezone(timedelta(hours=9))

def verified_row():
    closed=datetime(2026,10,16,12,30,10,tzinfo=KST)
    stamp=int((closed-timedelta(seconds=2)).timestamp()*1000)
    return dict(type="close",symbol="ETH/USDT:USDT",side="short",
                dry_run=False,strategy_group="core",time=closed.isoformat(timespec="seconds"),
                amount=2.0,entry_price=2450.,close_price=2430.,pnl_source="okx_realized",
                okx_net_pnl=3.,reason="position_ai_close_all",
                close_execution_evidence=dict(
                    order_id="ord1",trade_ids=["fill-1","fill-2"],
                    price_basis="verified_order_fills",filled_contracts=2.,
                    vwap=2430.,first_fill_timestamp_ms=stamp,
                    last_fill_timestamp_ms=stamp))


def test_recognizes_only_evidenced_close():
    row=verified_row()
    assert p.valid_original(row)
    assert not p.valid_original(dict(row,execution_id="existing"))
    assert not p.valid_original(dict(row,close_execution_evidence=None))
    assert not p.valid_original(dict(row,close_price=2490))


def test_get_only_fill_verification_identity(monkeypatch):
    row=verified_row()
    proof=dict(row["close_execution_evidence"])
    captured={}
    def fake_verify(client,position,receipt,*,start_ms):
        captured.update(dict(side=position["side"],amount=position["contracts"],
                            receipt_id=receipt["id"],start=start_ms))
        return dict(proof,price_basis="verified_order_fills")
    import core_close_fill_evidence
    monkeypatch.setattr(core_close_fill_evidence,"verified_close_fills",fake_verify)
    result=p.verify_against_exchange(object(),row)
    assert result["proof_type"]=="EXCHANGE_ORDER_ID_AND_FILLS_EXACT_MATCH"
    assert captured["receipt_id"]=="ord1" and captured["amount"]==2
    monkeypatch.setattr(core_close_fill_evidence,"verified_close_fills",
        lambda *a,**kw: dict(proof,trade_ids=["different"],price_basis="verified_order_fills"))
    assert p.verify_against_exchange(object(),row) is None


def test_sidecar_validates_row_sha_and_detects_tampering(tmp_path):
    row=verified_row()
    proof=dict(row["close_execution_evidence"],proof_type="EXCHANGE_ORDER_ID_AND_FILLS_EXACT_MATCH")
    item=dict(row_index=0,row_sha256=p.row_hash(row),symbol=row["symbol"],side=row["side"],
              derived_execution_key="verified-legacy-close:ord1",proof=proof)
    body=dict(schema_version=1,verified=[item],unconfirmed=[])
    p.save_atomic(tmp_path/p.FILE,body)
    assert p.load_valid_overlay([row],tmp_path)=={0:"verified-legacy-close:ord1"}
    assert p.load_valid_overlay([dict(row,close_price=2440.)],tmp_path)=={}
    assert p.load_valid_overlay([dict(row,execution_id="exists")],tmp_path)=={}
    assert not (tmp_path/"trades_log.jsonl").exists()


def test_overlay_is_in_memory_only(tmp_path):
    row=verified_row()
    modified=p.apply_in_memory([row],{0:"verified-legacy-close:ord1"})
    assert modified[0]["execution_id"]=="verified-legacy-close:ord1"
    assert row.get("execution_id") is None


def test_collect_duplicate_order_id_fail_closed(monkeypatch):
    row=verified_row()
    monkeypatch.setattr(p,"verify_against_exchange",
        lambda client,entry:dict(order_id="ord1",trade_ids=["fill-1"],
                                 filled_contracts=2.,vwap=2430.,proof_type="EXCHANGE_ORDER_ID_AND_FILLS_EXACT_MATCH"))
    result=p.collect([row,dict(row,time=row["time"])],lambda symbol:object())
    assert len(result["verified"])==1
    assert result["unconfirmed"][0]["reason"]=="duplicate_order_id"


def test_summary_overlay_changes_only_verified_pnl(tmp_path):
    row=verified_row()
    opened=dict(type="open",symbol=row["symbol"],side="short",
                dry_run=False,strategy_group="core",price=2450.,amount=.2,
                time="2026-10-16T11:30:00",execution_id="open-id")
    # Receipt external proof exists; the unchanged journal close still lacks ID.
    (tmp_path/"trades_log.jsonl").write_text(json.dumps(opened)+"\n"+json.dumps(row)+"\n")
    (tmp_path/"mfe_profit_shadow_log.jsonl").write_text("")
    proof=dict(row["close_execution_evidence"],proof_type="EXCHANGE_ORDER_ID_AND_FILLS_EXACT_MATCH")
    report=dict(schema_version=1,verified=[dict(
        row_index=1,row_sha256=p.row_hash(row),symbol=row["symbol"],
        side=row["side"],derived_execution_key="verified-legacy-close:ord1",proof=proof)])
    p.save_atomic(tmp_path/p.FILE,report)
    result=status.from_user_dir(tmp_path)
    assert result["verified_lifecycles_before"]==0
    assert result["verified_lifecycles_after"]==1
    assert result["newly_validated_closed_lifecycles"]==1
    assert result["may_execute"] is False
    assert json.loads((tmp_path/"trades_log.jsonl").read_text().splitlines()[1]).get("execution_id") is None


def test_no_proof_does_not_promote_unknown(tmp_path):
    row=verified_row()
    row["close_execution_evidence"]=None
    opened=dict(type="open",symbol=row["symbol"],side="short",dry_run=False,
                strategy_group="core",price=2450.,amount=.2,
                time="2026-10-16T11:30:00",execution_id="open")
    (tmp_path/"trades_log.jsonl").write_text(json.dumps(opened)+"\n"+json.dumps(row)+"\n")
    (tmp_path/"mfe_profit_shadow_log.jsonl").write_text("")
    result=status.from_user_dir(tmp_path)
    assert result["verified_lifecycles_before"]==0 and result["verified_lifecycles_after"]==0
    assert result["still_unverified_closed_lifecycles"]==1


def test_no_broker_write_paths():
    source=Path("core_legacy_close_proof_v33_27.py").read_text()
    report=Path("core_close_recovery_status_v33_27.py").read_text()
    for txt in (source,report):
        for forbidden in ("create_order(","cancel_order(","amend_protective_stop(","openai_analyzer.","gemini_analyzer."):
            assert forbidden not in txt



def test_report_has_no_live_strategy_changes():
    source=Path("core_close_recovery_status_v33_27.py").read_text()
    assert "may_execute=False" in source
    assert "apply_in_memory" in source
    assert "save_atomic(" not in source
