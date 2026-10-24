"""No overtrading on small-loss Gemini full close; still allow emergency exits."""
import logging
from types import SimpleNamespace
import pytest
import trader
from state import TraderState

S="ETH/USDT:USDT"

class C:
 def __init__(self,root):
  self.user_dir=str(root);self.POSITION_AI_REVIEW_COOLDOWN_MINUTES=15
  self.MIN_CONFIDENCE=.6;self.POSITION_AI_LIVE_EXECUTE=True
  self.CORE_AI_STRATEGY_AUTHORITY=True
  self.CORE_GEMINI_MANAGEMENT_ONLY=True
  self.EXECUTION_MODE="LIVE"
  self.logger=logging.getLogger("exit-guard-v28")
  self._events=[]

def exercise(monkeypatch,tmp_path,loss_r,allow_full=False,breakdown=False,side="long",assessment="invalidated",gemini_confidence=.91):
 cfg=C(tmp_path)
 position=dict(side=side,entry_price=100.,mark_price=99. if side=="long" else 101.,
               contracts=5.,position_id="p",entry_timestamp_ms=1791910000000)
 c=SimpleNamespace(fetch_current_protection=lambda *_:dict(algo_id="oco",sz=5.,
                             sl_price=90. if side=="long" else 110.,tp_price=120.))
 monkeypatch.setattr(trader.core_ai_context,"with_evidence",lambda s,*_:s)
 monkeypatch.setattr(trader.core_ai_context,"reuse",lambda *a,**kw:dict(assessment=assessment,confidence=gemini_confidence,
                        reasoning="market weakening",management_action="CLOSE_ALL",risk_level="high"))
 monkeypatch.setattr(trader.strategy_authority,"held_review_valid",lambda *a,**k:True)
 monkeypatch.setattr(trader.core_management_policy,"enabled",lambda *_:True)
 monkeypatch.setattr(trader.core_management_policy,"valid_review",lambda *a,**k:True)
 monkeypatch.setattr(trader.core_management_policy,"decision",lambda *a,**k:dict(action="CLOSE_ALL",confidence=.91,reasoning="risk"))
 monkeypatch.setattr(trader,"_position_ai_close_guard",lambda *a,**k:dict(allow_close_all=allow_full,
              reason=("deterministic_breakdown" if breakdown else "downgrade_to_reduce"),loss_r=loss_r,breakdown=breakdown))
 closes=[]
 monkeypatch.setattr(trader,"_execute_close",lambda *a,**k:closes.append(k.get("reason")) or True)
 monkeypatch.setattr(trader.position_ai_log,"record_event",lambda *a,**k:cfg._events.append((a,k)))
 trader._handle_position_ai_review(cfg,TraderState(),c,S,["5m","1h"],"summary",position,
        dict(action="hold",confidence=.8),{},review_path="general")
 return closes,cfg._events

@pytest.mark.parametrize("loss_r",[-0.001,0.,.05,.19])
def test_mild_loss_gate_blocks_costly_full_exit(monkeypatch,tmp_path,loss_r):
 closes,events=exercise(monkeypatch,tmp_path,loss_r)
 if loss_r<0:
  assert closes==["position_ai_close_all"]
 else:
  assert closes==[]
  assert any(a[1]=="position_ai_hold" and kw.get("reason")=="core_ai_close_churn_guard" for a,kw in events)

@pytest.mark.parametrize("loss_r,breakdown",[(.21,False),(.05,True),(None,False)])
def test_breakdown_emergency_or_unknown_risk_full_close_still_allowed(monkeypatch,tmp_path,loss_r,breakdown):
 closes,_=exercise(monkeypatch,tmp_path,loss_r,allow_full=True,breakdown=breakdown)
 assert closes==["position_ai_close_all"]


@pytest.mark.parametrize("loss_r", [0.2,0.35,0.49])
def test_gemini_recent_reduce_blocks_second_full_close(monkeypatch,tmp_path,loss_r):
    monkeypatch.setattr(trader,"_post_reduce_close_cooldown",
        lambda *a,**k:{"blocked":True,"reason":"post_reduce_close_cooldown",
                       "remaining_seconds":342.0})
    closes, events=exercise(monkeypatch,tmp_path,loss_r,allow_full=True)
    assert closes==[]
    assert any(a[1]=="position_ai_hold" and kw.get("reason")=="post_reduce_close_cooldown"
               for a,kw in events)


def test_gemini_confirmed_structural_breakdown_can_close_during_cooldown(monkeypatch,tmp_path):
    monkeypatch.setattr(trader,"_post_reduce_close_cooldown",
        lambda *a,**k:{"blocked":True,"reason":"post_reduce_close_cooldown",
                       "remaining_seconds":700.0})
    closes,_=exercise(monkeypatch,tmp_path,.5,allow_full=True,breakdown=True)
    assert closes==["position_ai_close_all"]


def test_gemini_profitable_close_is_not_blocked_by_reduce_cooldown(monkeypatch,tmp_path):
    monkeypatch.setattr(trader,"_post_reduce_close_cooldown",
        lambda *a,**k:{"blocked":True,"reason":"post_reduce_close_cooldown",
                       "remaining_seconds":100.0})
    closes,_=exercise(monkeypatch,tmp_path,-.1,allow_full=False)
    assert closes==["position_ai_close_all"]


def test_gemini_after_reduce_cooldown_full_close_allowed(monkeypatch,tmp_path):
    monkeypatch.setattr(trader,"_post_reduce_close_cooldown",
        lambda *a,**k:{"blocked":False,"reason":"ok","remaining_seconds":0.0})
    closes,_=exercise(monkeypatch,tmp_path,.4,allow_full=True)
    assert closes==["position_ai_close_all"]



@pytest.mark.parametrize("side",["long","short"])
def test_verified_high_confidence_invalidated_050r_bypasses_cooldown(monkeypatch,tmp_path,side):
    monkeypatch.setattr(trader,"_post_reduce_close_cooldown",
        lambda *_:pytest.fail("verified emergency close cannot wait"))
    closes,events=exercise(monkeypatch,tmp_path,.50,allow_full=True,side=side,
                          assessment="invalidated",gemini_confidence=.90)
    assert closes==["position_ai_close_all"]
    assert any(a[1]=="post_reduce_close_escalated" and kw["reason"]=="high_confidence_invalidation_050r"
               for a,kw in events)


@pytest.mark.parametrize("side",["long","short"])
def test_075r_urgent_even_with_weakening_model(monkeypatch,tmp_path,side):
    monkeypatch.setattr(trader,"_post_reduce_close_cooldown",
        lambda *_:pytest.fail("0.75R urgent adverse position must be eligible immediately"))
    closes,events=exercise(monkeypatch,tmp_path,.75,allow_full=True,side=side,
                          assessment="weakening",gemini_confidence=.66)
    assert closes==["position_ai_close_all"]
    assert any(a[1]=="post_reduce_close_escalated" and kw["reason"]=="emergency_loss_075r"
               for a,kw in events)


@pytest.mark.parametrize("assessment,confidence,loss_r", [
    ("weakening",.95,.60),("invalidated",.79,.60),("invalidated",.95,.499),
    ("invalidated",True,.60),("invalidated",float("nan"),.60),
])
def test_nonurgent_adverse_still_waits_after_reduce(monkeypatch,tmp_path,assessment,confidence,loss_r):
    monkeypatch.setattr(trader,"_post_reduce_close_cooldown",
        lambda *_:{"blocked":True,"reason":"post_reduce_close_cooldown","remaining_seconds":501})
    closes,events=exercise(monkeypatch,tmp_path,loss_r,allow_full=True,
                          assessment=assessment,gemini_confidence=confidence)
    assert not closes
    assert any(a[1]=="position_ai_hold" and kw.get("reason")=="post_reduce_close_cooldown"
               for a,kw in events)


def test_invalid_unknown_risk_remains_fail_safe_without_cooldown(monkeypatch,tmp_path):
    monkeypatch.setattr(trader,"_post_reduce_close_cooldown",
        lambda *_:pytest.fail("unknown-risk fail-safe cannot wait"))
    closes,_=exercise(monkeypatch,tmp_path,None,allow_full=True)
    assert closes==["position_ai_close_all"]


def test_emergency_classification_requires_confirmed_close_authority():
    g={"allow_close_all":False,"loss_r":.95,"breakdown":False}
    assert trader._core_post_reduce_emergency_reason(g,{"assessment":"invalidated","confidence":.99}) is None


def test_threshold_floats_and_boundaries():
    review={"assessment":"invalidated","confidence":.8}
    for value,want in ((.499,None),(.50,"high_confidence_invalidation_050r"),
                       (.749,"high_confidence_invalidation_050r"),(.75,"emergency_loss_075r")):
        actual=trader._core_post_reduce_emergency_reason(
            {"allow_close_all":True,"loss_r":value},review)
        assert actual==want
