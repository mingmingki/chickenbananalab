"""v33-48 exact-evidence shadow triage and no accidental production recovery."""
from datetime import datetime,timedelta,timezone
from pathlib import Path
from types import SimpleNamespace
import pytest
import candidate_c_recovery_triage_v33_48 as m

SYM="SOL/USDT:USDT"
ID="int_123"
ALGO="alg123"
SHA="a"*64

def entry():
    return dict(kind="EntryIntent",intent_id=ID,symbol=SYM,
        state="protection_pending",requested_side="short",requested_quantity=3.14,
        remaining_quantity=0.95,remote_submission_finalized=True,
        protective_algo_ids=[ALGO],attach_algo_cl_ord_id="owner_a",
        config_hash="hash1",config_version_id="v1",reservation_id="risk123",
        reserved_risk_usdt=5.0)

def epoch():
    return SimpleNamespace(entry_intent_id=ID,side="short",
        exchange_position_id="pos123",exchange_entry_timestamp_ms=111,
        config_hash="hash1",config_version_id="v1",reservation_id="risk123",
        remaining_reserved_risk_usdt=2.0,
        raw_entry_price=103.61,remaining_contracts=0.95,
        current_stop_price=103.44,target_price=99.5)

def pos():
    return dict(position_id="pos123",entry_timestamp_ms=111,
        side="short",entry_price=103.61,contracts=0.95)

def algo():
    return dict(algoId=ALGO,algoClOrdId="owner_a",ordType="oco",state="live",
        instId="SOL-USDT-SWAP",side="buy",reduceOnly="true",
        sz="0.95",slTriggerPx="103.44",tpTriggerPx="99.5")

class Client:
    def __init__(self):
        self.position=pos();self.protection=algo();self.open_orders=[]
        self.fail_position_on_second=False;self.count=0;self.exchange=self
        self.trades=[dict(id="t1",order="ord1",amount=3.14,price=103.61)]
        self.order=dict(id="ord1",clientOrderId=ID,status="closed",
                        side="sell",filled=3.14)
    def fetch_position(self):
        self.count+=1
        if self.fail_position_on_second and self.count>=2:
            return dict(self.position,contracts=0.94)
        return self.position
    def fetch_open_orders(self,symbol):return self.open_orders
    def fetch_pending_protection_orders(self):return [self.protection]
    def fetch_order_status_by_client_id(self,id):return self.order
    def fetch_entry_order_fills(self,id):return self.trades

def setup(monkeypatch):
    monkeypatch.setattr(m.baseline,"_last_epoch",lambda *a:epoch())
    monkeypatch.setattr(m,"_prior_owned_protection",lambda *a:True)
    monkeypatch.setattr(m.baseline,"read_ledger",lambda root,symbol:({ID:entry()},SHA))

def classify_status(status):
    return m.classify("/tmp",dict(symbol=SYM,status=status,ledger_sha256=SHA))

@pytest.mark.parametrize("status,expected",[
    ("PROTECTED_PARITY_VERIFIED","NO_REPAIR_REQUIRED"),
    ("FLAT_CLEAR","NO_REPAIR_REQUIRED"),
    ("EVIDENCE_MISMATCH","MANUAL_REVIEW_REQUIRED"),
    ("REDUCE_RESIZE_PENDING","MANUAL_REVIEW_REQUIRED"),
    ("MANAGEMENT_ACTION_UNRESOLVED","MANUAL_REVIEW_REQUIRED"),
    ("LEDGER_INTEGRITY_UNCONFIRMED","MANUAL_REVIEW_REQUIRED"),
    ("ENTRY_OWNER_UNCONFIRMED","MANUAL_REVIEW_REQUIRED")])
def test_classification_is_not_auto_release(status,expected):
    out=classify_status(status)
    assert out["status"]==expected
    assert out["may_mutate_ledger"] is False
    assert out["may_send_orders"] is False
    assert out["may_release_entry_gate"] is False

def test_shadow_eligible_with_exact_independent_proof(monkeypatch):
    setup(monkeypatch)
    out=m.classify("/tmp",dict(symbol=SYM,status="ENTRY_OWNER_UNCONFIRMED",
        ledger_sha256=SHA),lambda s:Client())
    assert out["status"]=="SHADOW_RECOVERY_CANDIDATE"
    assert out["recovery_type"]=="PARENT_PROTECTED_STATE"
    assert out["may_mutate_ledger"] is False
    assert out["may_release_entry_gate"] is False

@pytest.mark.parametrize("adjust,reason",[
    (lambda c:c.protection.update(sz="0.94"),"POST_REDUCE_PRICE_OR_QUANTITY_MISMATCH"),
    (lambda c:c.protection.update(slTriggerPx="103.40"),"POST_REDUCE_PRICE_OR_QUANTITY_MISMATCH"),
    (lambda c:c.protection.update(tpTriggerPx="99.60"),"POST_REDUCE_PRICE_OR_QUANTITY_MISMATCH"),
    (lambda c:c.protection.update(algoClOrdId="unknown"),"OCO_OWNERSHIP_UNVERIFIED"),
    (lambda c:c.protection.update(algoId="foreign"),"OCO_OWNERSHIP_UNVERIFIED"),
    (lambda c:c.protection.update(state="canceled"),"OCO_TERMINAL_OR_NOT_REDUCE_ONLY"),
    (lambda c:c.protection.update(reduceOnly="false"),"OCO_TERMINAL_OR_NOT_REDUCE_ONLY"),
    (lambda c:c.open_orders.append({"id":"unresolved"}),"POSITION_ORDER_OCO_SCOPE_NOT_EXACT"),
    (lambda c:c.trades.clear(),"ENTRY_FILLS_INCOMPLETE"),
    (lambda c:c.order.update(clientOrderId="other"),"ENTRY_ORDER_NOT_OWNED"),
    (lambda c:setattr(c,"fail_position_on_second",True),"LIVE_POSITION_CHANGED_DURING_READS"),
])
def test_evidence_mismatch_cannot_recover(monkeypatch,adjust,reason):
    setup(monkeypatch)
    c=Client();adjust(c)
    out=m.classify("/tmp",dict(symbol=SYM,status="ENTRY_OWNER_UNCONFIRMED",
        ledger_sha256=SHA),lambda symbol:c)
    assert out["status"]=="MANUAL_REVIEW_REQUIRED",out
    assert out["reason"]==reason

def test_management_pending_blocks_candidate(monkeypatch):
    setup(monkeypatch)
    orig=m.baseline.read_ledger
    def rows(root,symbol):
        intents,sha=orig(root,symbol)
        intents["m"]=dict(kind="ReduceIntent",state="reconcile_required",intent_id="m")
        return intents,sha
    monkeypatch.setattr(m.baseline,"read_ledger",rows)
    out=m.classify("/tmp",dict(symbol=SYM,status="ENTRY_OWNER_UNCONFIRMED",
        ledger_sha256=SHA),lambda s:Client())
    assert out["reason"]=="MANAGEMENT_OR_ORDER_PENDING"

def test_ledger_changed_since_audit_blocks_candidate(monkeypatch):
    setup(monkeypatch)
    out=m.classify("/tmp",dict(symbol=SYM,status="ENTRY_OWNER_UNCONFIRMED",
        ledger_sha256="other"),lambda s:Client())
    assert out["reason"]=="LEDGER_CHANGED_SINCE_BASELINE"

def test_stale_snapshot_is_never_used(monkeypatch):
    setup(monkeypatch)
    report=dict(observed_at=(datetime.now(timezone.utc)-timedelta(hours=2)).isoformat(),
        results=[dict(symbol=SYM,status="ENTRY_OWNER_UNCONFIRMED",ledger_sha256=SHA)])
    out=m.analyze("/tmp",report,lambda s:(_ for _ in ()).throw(AssertionError("NO_GET")))
    assert out["results"][0]["status"]=="STALE_BASELINE"
    assert out["can_apply_recovery"] is False

def test_source_explicitly_read_only():
    text=Path(m.__file__).read_text()
    for string in ("mark_protected(", "mark_residual_quantity(", "create_order(",
       "amend_protective_stop(", "cancel_protection(", "systemctl restart"):
        assert string not in text
    assert 'can_apply_recovery=False' in text
