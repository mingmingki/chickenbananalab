"""v33-49: never release stale entry gate without two full flat reads & close."""
from pathlib import Path
from types import SimpleNamespace
import json
import pytest
import candidate_c_flat_gate_recheck_v33_49 as f


def scenario(tmp_path,monkeypatch):
    eid="managed_SOL_id"
    record=SimpleNamespace(
        intent_id=eid,kind="EntryIntent",state="terminal",
        remote_submission_finalized=True,exchange_order_id="OKX_ORDER",
        config_hash="cfg",config_version_id="v1",reservation_id="risk1",
        protective_algo_ids=["OCO_ID"])
    ledger=SimpleNamespace(records=lambda:[record],pending_intents=lambda:[],
        has_ambiguous_pending_intent=lambda:False)
    epoch=SimpleNamespace(entry_intent_id=None)
    estore=SimpleNamespace(get=lambda id:epoch,_states={})
    machine=SimpleNamespace(state=f.reverse.State.FLAT)
    rstore=SimpleNamespace(get=lambda sym:machine)
    state=SimpleNamespace(intent_ledger=ledger,epoch_store=estore,reversal_store=rstore)
    config=SimpleNamespace(user_dir=str(tmp_path))
    snap=SimpleNamespace(exchange_position=None,exchange_open_orders=[],
        exchange_protective_algo_ids=[],internal_trade_log_record=None,
        internal_risk_reservation=None,internal_state_ambiguous=False)
    calls=[]
    def snapshot(*args):
        calls.append(1)
        return snap
    (tmp_path/"trades_log.jsonl").write_text(json.dumps(dict(
        type="close",strategy_group="candidate_c",symbol="SOL/USDT:USDT",
        execution_id="external-close:"+eid,reason="EXTERNAL_SL_FILLED_EXACT",
        pnl_source="okx_realized",amount=0.95,pnl=0.152,fee=0.21))+"\n")
    monkeypatch.setattr(f.proof,"read_ledger",
        lambda *a:({eid:dict(kind="EntryIntent",state="terminal")},"sha123"))
    return config,state,snap,snapshot,calls


def run(case,reason=None):
    cfg,st,snap,build,reads=case
    outcome=f.decide(cfg,None,"SOL/USDT:USDT",st,build,reason or f.EXACT_REASON)
    assert outcome[2]["may_submit_orders"] is False
    assert outcome[2]["may_release_other_gates"] is False
    return outcome


def test_exact_managed_close_recovers_stale_startup_gate(tmp_path,monkeypatch):
    x=scenario(tmp_path,monkeypatch)
    allowed,reason,proof=run(x)
    assert allowed is True
    assert reason=="EXACT_FLAT_RECHECK_CONFIRMED"
    assert len(x[4])==2
    assert proof["close_execution_id"]=="external-close:managed_SOL_id"


@pytest.mark.parametrize("modify,expected",[
    (lambda x: x[2].__setattr__("exchange_position",{"side":"short"}),"NOT_EXACTLY_FLAT_AND_ORDER_FREE"),
    (lambda x: x[2].exchange_open_orders.append({"id":"pending"}),"NOT_EXACTLY_FLAT_AND_ORDER_FREE"),
    (lambda x: x[2].exchange_protective_algo_ids.append("algo"),"NOT_EXACTLY_FLAT_AND_ORDER_FREE"),
    (lambda x: x[2].__setattr__("internal_state_ambiguous",True),"NOT_EXACTLY_FLAT_AND_ORDER_FREE"),
    (lambda x: x[1].epoch_store._states.update({"managed_SOL_id":object()}),"EPOCH_STILL_ACTIVE"),
    (lambda x: setattr(x[1].reversal_store.get("SOL"),"state",f.reverse.State.SAFE_HALT),"REVERSAL_MACHINE_NOT_FLAT"),
    (lambda x: setattr(x[1].intent_ledger.records()[0],"state","protection_pending"),"UNFINISHED_OR_MISSING_OWNED_ENTRY"),
    (lambda x: setattr(x[1].intent_ledger.records()[0],"remote_submission_finalized",False),"ENTRY_OR_PROTECTION_IDENTITY_UNCONFIRMED"),
])
def test_unconfirmed_states_cannot_release(tmp_path,monkeypatch,modify,expected):
    x=scenario(tmp_path,monkeypatch)
    modify(x)
    ok,reason,_=run(x)
    assert not ok and reason==expected


def test_other_initial_block_never_reexamined(tmp_path,monkeypatch):
    x=scenario(tmp_path,monkeypatch)
    allowed,reason,_=run(x,reason="exchange_position_exists_without_internal_record")
    assert not allowed and reason=="INITIAL_BLOCK_NOT_ELIGIBLE" and not x[4]


@pytest.mark.parametrize("modify,reason",[
    (lambda row:row.update(reason="MANUAL_OR_EXTERNAL_CLOSE"),"EXACT_EXTERNAL_CLOSE_RECEIPT_NOT_UNIQUE"),
    (lambda row:row.update(pnl_source="estimated"),"EXACT_EXTERNAL_CLOSE_RECEIPT_NOT_UNIQUE"),
    (lambda row:row.update(amount=0),"INVALID_CLOSE_QUANTITY"),
    (lambda row:row.update(amount="NaN"),"INVALID_CLOSE_QUANTITY")
])
def test_nonexact_close_cannot_release(tmp_path,monkeypatch,modify,reason):
    x=scenario(tmp_path,monkeypatch)
    p=tmp_path/"trades_log.jsonl"
    row=json.loads(p.read_text())
    modify(row);p.write_text(json.dumps(row)+"\n")
    ok,why,_=run(x)
    assert not ok and why==reason


def test_second_exchange_snapshot_can_veto(tmp_path,monkeypatch):
    x=scenario(tmp_path,monkeypatch)
    def build(*args):
        x[4].append(1)
        snap=x[2]
        if len(x[4])==2:
            return SimpleNamespace(**{**vars(snap),"exchange_open_orders":[{"id":"late"}]})
        return snap
    x=list(x);x[3]=build
    ok,reason,_=run(x)
    assert not ok and reason=="SECOND_SNAPSHOT_CHANGED"


def test_disk_journal_change_vetoes(monkeypatch,tmp_path):
    x=scenario(tmp_path,monkeypatch)
    count=0
    def read(*a):
        nonlocal count
        count+=1
        return ({"managed_SOL_id":dict(kind="EntryIntent",state="terminal")},
                "sha1" if count==1 else "sha2")
    monkeypatch.setattr(f.proof,"read_ledger",read)
    assert run(x)[1]=="LEDGER_CHANGED_DURING_RECHECK"


def test_code_preserves_old_admission_controls():
    text=Path("candidate_c_trader_adapter.py").read_text()
    assert text.count("flat_recheck.decide(")==1
    assert "last_flat_recheck_at >= 60" in text
    assert "startup_result.get('reason') == flat_recheck.EXACT_REASON" in text
    assert "entries_allowed = new_entries_allowed_from_startup and transition_gate_ok" in text
    assert "loss_guard_allowed = account_daily_loss_guard.allow_new_entry(equity)" in text
    assert "not symbol_entry_control.is_paused(cfg.user_dir, symbol)" in text
    assert "not manual_block" in text


def test_recheck_only_reads_and_never_submits():
    src=Path(f.__file__).read_text()
    for x in ("create_order(","mark_protected(","mark_terminal(",
              "mark_residual_quantity(","cancel_protection(","amend_protective_stop("):
        assert x not in src
