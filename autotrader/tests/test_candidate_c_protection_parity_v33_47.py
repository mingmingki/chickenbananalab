"""v33-47 historical STOP_UPDATE/reduce parity read-only tests."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import json
import pytest
import candidate_c_protection_parity_v33_47 as m

SYM="SOL/USDT:USDT"
ID="owned_intent_01"
OCO="4015499724766158848"


def fixtures():
    intent=dict(intent_id=ID,kind="EntryIntent",state="protected",symbol=SYM,
        requested_side="short",requested_quantity=3.14,filled_quantity=3.14,
        remaining_quantity=0.95,protective_algo_ids=[OCO],
        attach_algo_cl_ord_id="cl_oco_01",config_version_id="v1",
        config_hash="hashcfg",reservation_id="risk1",
        reserved_risk_usdt=6.5,remote_submission_finalized=True,
        protection_phase="complete")
    epoch=SimpleNamespace(entry_intent_id=ID,side="short",
        exchange_position_id="123",exchange_entry_timestamp_ms=10000,
        config_version_id="v1",config_hash="hashcfg",reservation_id="risk1",
        remaining_reserved_risk_usdt=1.96,remaining_contracts=0.95,
        raw_entry_price=103.61,current_stop_price=103.44,target_price=99.5)
    pos=dict(side="short",contracts=0.95,entry_price=103.61,
        position_id="123",entry_timestamp_ms=10000)
    algo=dict(algoId=OCO,algoClOrdId="cl_oco_01",instId="SOL-USDT-SWAP",
        ordType="oco",state="live",side="buy",reduceOnly="true",
        sz="0.95",slTriggerPx="103.44",tpTriggerPx="99.5")
    return {ID:intent},epoch,pos,algo


def decide(modifier=None):
    intents,epoch,pos,algo=fixtures()
    if modifier:modifier(intents,epoch,pos,algo)
    return m.evaluate(SYM,pos,[],[algo],intents,epoch)


def test_verified_exact_after_reduce_and_stop_update():
    result=decide()
    assert result["status"]=="PROTECTED_PARITY_VERIFIED"
    assert result["verified_contracts"]=="0.95"
    assert result["may_execute"] is False and result["may_release"] is False


@pytest.mark.parametrize("mutator,reason",[
    (lambda i,e,p,a:a.update(sz="1.20"),"POST_REDUCE_QUANTITY_MISMATCH"),
    (lambda i,e,p,a:i[ID].update(remaining_quantity=1.20),"POST_REDUCE_QUANTITY_MISMATCH"),
    (lambda i,e,p,a:e.__setattr__("remaining_contracts",1.20),"POST_REDUCE_QUANTITY_MISMATCH"),
    (lambda i,e,p,a:a.update(slTriggerPx="103.55"),"STOP_PRICE_MISMATCH"),
    (lambda i,e,p,a:a.update(tpTriggerPx="99.55"),"TAKE_PROFIT_PRICE_MISMATCH"),
    (lambda i,e,p,a:a.update(algoClOrdId="other"),"OCO_OWNERSHIP_ID_MISMATCH"),
    (lambda i,e,p,a:a.update(algoId="other"),"OCO_OWNERSHIP_ID_MISMATCH"),
    (lambda i,e,p,a:a.update(reduceOnly="false"),"OCO_NOT_REDUCE_ONLY"),
    (lambda i,e,p,a:a.update(state="canceled"),"OCO_NOT_LIVE_OR_WRONG_INSTRUMENT"),
    (lambda i,e,p,a:p.update(entry_timestamp_ms=10001),"POSITION_LIFECYCLE_ID_MISMATCH"),
    (lambda i,e,p,a:e.__setattr__("remaining_reserved_risk_usdt",0),"RISK_RESERVATION_MISSING"),
    (lambda i,e,p,a:i[ID].update(config_hash="different"),"CONFIG_OR_RISK_ID_MISMATCH"),
])
def test_conflicting_exchange_or_journal_is_unconfirmed(mutator,reason):
    result=decide(mutator)
    assert result["status"]=="EVIDENCE_MISMATCH"
    assert result["reason"]==reason


def test_reduce_resize_pending_is_explicit_even_when_old_oco_live():
    result=decide(lambda i,e,p,a:i[ID].update(protection_phase="reduce_resize_pending"))
    assert result["status"]=="REDUCE_RESIZE_PENDING"


def test_rejected_stop_update_terminal_is_not_unresolved():
    def change(i,e,p,a):
        i["stop1"]=dict(intent_id="stop1",kind="StopUpdateIntent",state="terminal")
    assert decide(change)["status"]=="PROTECTED_PARITY_VERIFIED"


def test_stop_update_unknown_does_not_claim_protection_pass():
    def change(i,e,p,a):
        i["stop1"]=dict(intent_id="stop1",kind="StopUpdateIntent",state="reconcile_required")
    assert decide(change)["status"]=="MANAGEMENT_ACTION_UNRESOLVED"


def test_no_parent_after_rejected_stop_is_not_invented():
    assert decide(lambda i,e,p,a:i[ID].update(state="protection_pending"))["status"]=="ENTRY_OWNER_UNCONFIRMED"


def test_position_flat_clean_only_without_remaining_entry():
    i,e,p,a=fixtures()
    i[ID]["state"]="terminal"
    assert m.evaluate(SYM,None,[],[],i)["status"]=="FLAT_CLEAR"
    i[ID]["state"]="protected"
    assert m.evaluate(SYM,None,[],[],i)["status"]=="FLAT_WITH_UNRESOLVED_EVIDENCE"


def test_unknown_exchange_list_is_not_assumed_flat():
    i,e,p,a=fixtures()
    assert m.evaluate(SYM,p,None,[a],i,e)["status"]=="EXCHANGE_UNKNOWN"


def test_read_ledger_corrupted_fails_without_mutation(tmp_path):
    src=tmp_path/"candidate_c_intent_ledger_SOL_USDT_USDT.jsonl"
    src.write_text('{"event":"created","seq":0,"prev_hash":"GENESIS","checksum":"bad"}\n')
    before=src.read_bytes()
    with pytest.raises(Exception):
        m.read_ledger(tmp_path,SYM)
    assert before==src.read_bytes()
    assert not list(tmp_path.glob("*.quarantine"))


def test_atomic_report_json_and_no_credentials(tmp_path):
    path=tmp_path/"report.json"
    report=dict(version="v33-47",results=[dict(symbol=SYM,status="PROTECTED_PARITY_VERIFIED")])
    m.save_atomic(path,report)
    assert json.loads(path.read_text())==report
    assert list(tmp_path.glob("*.tmp"))==[]


def test_source_never_requests_writes_or_releases():
    src=Path(m.__file__).read_text()
    for word in ("create_order(", "amend_protective_stop(", "cancel_protection(",
                 "mark_protected(", "mark_terminal(", "create_position_with_sl_tp(",
                 "systemctl restart"):
        assert word not in src
    assert "IntentLedger.load(" not in src.replace('IntentLedger.load (','')


class FakeClient:
    def __init__(self,position,algos):
        self.pos=position;self.algos=algos;self.exchange=self
        self.calls=[]
    def fetch_position(self):
        self.calls.append("position");return self.pos
    def fetch_open_orders(self,symbol):
        self.calls.append("orders");return []
    def fetch_pending_protection_orders(self):
        self.calls.append("oco");return self.algos


def test_audit_treats_get_failures_as_unknown(monkeypatch,tmp_path):
    monkeypatch.setattr(m,"read_ledger",lambda root,symbol:({}, "hash"))
    bad=FakeClient(None,[])
    def fail():raise RuntimeError("GET unavailable")
    bad.fetch_position=fail
    report=m.audit(tmp_path,lambda s:bad)
    assert report["count_by_status"]=={"READ_ONLY_QUERY_UNCONFIRMED":2}
    assert all(not x["may_execute"] for x in report["results"])
