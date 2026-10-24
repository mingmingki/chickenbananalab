"""v32-1: never downgrade a genuine missing/under-sized/orphaned OCO."""
import time
from types import SimpleNamespace
import pytest
import trader
import core_protection_transaction as tx
import ops_observability as health

S="XRP/USDT:USDT"
ALG="oco-owned-93"

class Exchange:
 def market(self, symbol):return {"id":"XRP-USDT-SWAP"}
class Client:
 symbol=S
 exchange=Exchange()
 def __init__(self, size):
  self.size=size
  self.reads=0
 def fetch_current_protection(self,_):return None
 def fetch_position(self):
  return dict(side="short",contracts=6.02,position_id="p1",
              entry_timestamp_ms=1730000000,entry_price=1.3658)
 def fetch_pending_protection_orders(self):
  self.reads+=1
  return [dict(instId="XRP-USDT-SWAP",side="buy",state="live",
               reduceOnly="true",algoId=ALG,sz=str(self.size),
               slTriggerPx="1.3815",tpTriggerPx="1.3345")]

P=dict(side="short",contracts=6.02,position_id="p1",
       entry_timestamp_ms=1730000000,entry_price=1.3658)

def owned(monkeypatch,*,when=1000.,sl=1.3815,tp=1.3345,
          lc=True,ago=25.,before=9.62,contracts=3.60):
 old=dict(algo_id=ALG,sl_price=sl,tp_price=tp,sz=before)
 opening=dict(P,contracts=before)
 row=dict(pending_order=dict(protection=old,position=opening,
          before_contracts=before,contracts=contracts,
          client_order_id="cr-owned",submitted_at=when-ago,
          lifecycle_id=tx.reduce_v2_state.position_identity(P) if lc else "other"))
 monkeypatch.setattr(tx.reduce_v2_state,"get",lambda *_:row)
 return row

def snapshot(size,user_dir="x",now_epoch=1000.):
 return trader._live_protection_snapshot(Client(size),P,user_dir=user_dir,now_epoch=now_epoch)

def problem(tmp_path,obs):
 live=dict(running=True,symbols={S:dict(live_position=P,live_protection=obs)})
 return [v for v in health.evaluate_health(tmp_path,live_state=live,
   recent_journal_text="[XRP/USDT:USDT] TF=1m")["issues"]
   if v["code"].startswith("core_protection")]

def test_recent_owned_oversized_is_visible_warning(monkeypatch,tmp_path):
 owned(monkeypatch)
 o=snapshot(9.62)
 assert o["status"]=="RESIZE_IN_PROGRESS"
 assert o["observed_contracts"]==9.62 and o["expected_contracts"]==6.02
 assert o["reduction_execution_id"]=="cr-owned"
 issues=problem(tmp_path,o)
 assert len(issues)==1 and issues[0]["code"]=="core_protection_resize_pending"
 assert issues[0]["severity"]=="warning"

@pytest.mark.parametrize("age",[90.001,120.,900.])
def test_timeout_mismatch_escalates_to_critical(monkeypatch,tmp_path,age):
 owned(monkeypatch,ago=age)
 o=snapshot(9.62)
 assert o["status"]=="MISMATCH_CONFIRMED"
 assert problem(tmp_path,o)[0]["severity"]=="critical"

@pytest.mark.parametrize("size",[5.9,5.99,6.01,6.03])
def test_not_old_oversize_is_critical(monkeypatch,tmp_path,size):
 owned(monkeypatch)
 o=snapshot(size)
 assert o["status"]=="MISMATCH_CONFIRMED"
 assert problem(tmp_path,o)[0]["severity"]=="critical"

def test_orphaned_old_protection_is_critical(monkeypatch):
 owned(monkeypatch,lc=False)
 assert snapshot(9.62)["status"]=="MISMATCH_CONFIRMED"

@pytest.mark.parametrize("kw",[{"sl":1.3810},{"tp":1.33},{"before":9.60},
  {"contracts":3.0},{"ago":-5.}])
def test_changed_owned_evidence_never_suppresses(monkeypatch,kw):
 owned(monkeypatch,**kw)
 assert snapshot(9.62)["status"]=="MISMATCH_CONFIRMED"

def test_exact_protection_still_verified(monkeypatch):
 owned(monkeypatch)
 assert snapshot(6.02)["status"]=="VERIFIED"

def test_no_pending_remains_critical(monkeypatch):
 monkeypatch.setattr(tx.reduce_v2_state,"get",lambda *_:None)
 assert snapshot(9.62)["status"]=="MISMATCH_CONFIRMED"

def test_unreadable_pending_remains_critical(monkeypatch):
 def bad(*_):raise ValueError("corrupt pending")
 monkeypatch.setattr(tx.reduce_v2_state,"get",bad)
 assert snapshot(9.62)["status"]=="MISMATCH_CONFIRMED"

def test_without_account_scope_does_not_downgrade(monkeypatch):
 owned(monkeypatch)
 assert snapshot(9.62,user_dir=None)["status"]=="MISMATCH_CONFIRMED"

def test_boundary_grace_is_inclusive(monkeypatch):
 owned(monkeypatch,ago=90.)
 assert snapshot(9.62)["status"]=="RESIZE_IN_PROGRESS"
