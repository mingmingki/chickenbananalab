import json
from types import SimpleNamespace
from unittest.mock import Mock
import pytest

import trader
import core_entry_policy
import strategy_authority
import core_management_context_v33_77 as receipts
import candidate_c_gpt_gate_adapter as cgate
import gpt_primary_v33_97 as primary

class Cfg:
    GPT_PRIMARY_ONLY = True
    CORE_AI_STRATEGY_AUTHORITY = True
    OPENAI_API_KEY = "fake"
    OPENAI_MODEL = "gpt-5-mini"
    MIN_CONFIDENCE = .6
    user_dir = "/tmp"
    logger = Mock()

def test_core_primary_valid_never_calls_second_gpt(monkeypatch):
    cfg=Cfg()
    monkeypatch.setattr(trader.openai_analyzer,"verify",lambda *a,**k: pytest.fail("double GPT call"))
    decision={"action":"short","confidence":.79,"reasoning":"primary directional choice"}
    allowed,gate,result=trader._gpt_entry_gate(cfg,"BTC/USDT:USDT",[],"market",None,decision)
    assert allowed and gate=="primary_authorized"
    assert result["authority"]=="gpt_primary_once"
    assert result["decision"]=="primary_authorized"
    assert core_entry_policy.gate_status(gate)=="GPT_PRIMARY_AUTHORIZED"
    assert result["reviewed_exit_price_contract"] is None

def test_invalid_primary_or_missing_confidence_fail_closed(monkeypatch):
    cfg=Cfg()
    monkeypatch.setattr(trader.openai_analyzer,"verify",lambda *a,**k: pytest.fail("double GPT"))
    for d in ({"action":"hold","confidence":.9},{"action":"long","confidence":.1},
              {"action":"short","confidence":float("nan")},{"action":"short","confidence":True}):
        allowed,gate,_=trader._gpt_entry_gate(cfg,"BTC/USDT:USDT",[],"",None,d)
        assert not allowed and gate=="blocked_error"

def test_primary_prices_kept_by_deterministic_contract_path():
    plan={"stop_loss_price":95,"take_profit_1_price":103,"take_profit_2_price":106,
          "confidence":.8,"reasoning":"structure"}
    out,source=strategy_authority.select_core_prices({"exit_plan":plan},None,"primary_authorized")
    assert out is not None
    assert out["stop_loss_price"]==95
    assert source=="gpt_primary_direction_authorized"
    assert strategy_authority.select_core_prices({"exit_plan":None},None,"primary_authorized")[0] is None

def test_primary_held_exit_receipt_preserves_lifecycle_and_confidence():
    result=dict(action="REDUCE_50",reduce_fraction=.25,
                gpt_verified=True,gpt_primary_decision=True,gpt_confidence=.8)
    r=receipts.make_receipt(result,"BTC/USDT:USDT","live-123",1.0,1_000_000.0)
    assert r["source"]=="v33-98_primary_management"
    assert receipts.valid_receipt(r,symbol="BTC/USDT:USDT",lifecycle_id="live-123",
                                  contracts=1.0,action="REDUCE_50",reduce_fraction=.25,
                                  min_confidence=.6,now=1_000_010.)
    assert not receipts.valid_receipt(r,symbol="BTC/USDT:USDT",lifecycle_id="different",
                                      contracts=1.0,action="REDUCE_50",reduce_fraction=.25,
                                      min_confidence=.6,now=1_000_010.)

@pytest.mark.parametrize("action,confidence,stale,expected",[
    ("long",.8,False,"primary_authorized"),
    ("hold",.7,False,"blocked_wait"),
    ("short",.9,False,"blocked_wait"),
    ("long",.4,False,"blocked_wait"),
    ("long",.8,True,"blocked_stale"),
])
def test_candidate_one_gpt_primary_response_no_second_verifier(monkeypatch,action,confidence,stale,expected):
    cfg=Cfg()
    monkeypatch.setattr(cgate.openai_analyzer,"verify",lambda *a,**k: pytest.fail("double GPT"))
    monkeypatch.setattr(cgate.gemini_analyzer,"propose_entry_exit_plan",lambda *a,**k: pytest.fail("double primary GPT"))
    outcome={"action":action,"confidence":confidence,"reasoning":"setup"}
    called=[]
    def once(*args,**kw):
        called.append(1)
        return SimpleNamespace(text=json.dumps(outcome),usage_metadata=SimpleNamespace(
            prompt_token_count=2,candidates_token_count=1))
    monkeypatch.setattr(primary,"_generate_content_observed",once)
    monkeypatch.setattr("usage_log.record_usage",lambda *args,**kwargs:None)
    monkeypatch.setattr(cgate.candidate_c_gpt_gate_log,"record",lambda *args,**kw: None)
    intent=SimpleNamespace(symbol="DOGE/USDT:USDT",side="long",setup_id="setup",reason_code="confirmed",
        raw_stop_price=.09,raw_target_price=.13)
    snap=dict(symbol=intent.symbol,current_price=.11,atr_4h=.005,tf_list=["4h"],trend_direction="up",donchian_upper=.1)
    r=cgate.verify_candidate_signal(cfg,intent,snap,is_still_valid_fn=lambda:not stale)
    assert r["gate_result"]==expected
    assert r["allowed"] == (expected=="primary_authorized")
    assert len(called)==1

def test_candidate_api_failure_never_creates_order(monkeypatch):
    cfg=Cfg()
    monkeypatch.setattr(primary,"_generate_content_observed",lambda *a,**k:(_ for _ in ()).throw(TimeoutError()))
    monkeypatch.setattr(cgate.candidate_c_gpt_gate_log,"record",lambda *a,**k:None)
    intent=SimpleNamespace(symbol="DOGE/USDT:USDT",side="long",raw_stop_price=90,raw_target_price=110)
    r=cgate.verify_candidate_signal(cfg,intent,dict(symbol=intent.symbol),is_still_valid_fn=lambda:True)
    assert not r["allowed"] and r["gate_result"]=="blocked_error"

def test_ui_dashboard_gemini_input_conditional():
    from jinja2 import Environment,FileSystemLoader
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]
    template=Environment(loader=FileSystemLoader(root/"templates")).get_template("dashboard.html")
    raw=template.environment.loader.get_source(template.environment,"dashboard.html")[0]
    assert "{% if not gpt_primary_only %}" in raw
    assert "{% if gpt_primary_only %}GPT API 키 (단독 판단)" in raw
    assert "document.getElementById(\"gemini-key\")?.value" in raw

def test_chart_only_candidate_never_invokes_ai_even_when_core_gpt_enabled(monkeypatch,tmp_path):
    import candidate_c_hybrid_live_adapter as live
    cfg=SimpleNamespace(user_dir=str(tmp_path),GPT_PRIMARY_ONLY=True,
        CANDIDATE_C_CHART_ONLY=True,CANDIDATE_C_GPT_ENTRY_GATE_ENABLED=False,
        CANDIDATE_C_LIVE_EXECUTE=True)
    intent=SimpleNamespace(kind="EntryIntent",symbol="DOGE/USDT:USDT",
        side="long",reason_code="breakout",setup_id="setup")
    monkeypatch.setattr(live,"_candidate_c_new_entry_allowed",lambda *a,**kw:True)
    monkeypatch.setattr(live,"_candidate_manual_close_entry_block",lambda *a,**kw:None)
    monkeypatch.setattr(live.gga,"verify_candidate_signal",
        lambda *a,**kw:pytest.fail("Candidate C chart-only must not call AI"))
    called=[]
    def chart_gate(*a,**kw):
        called.append(1)
        return {"allowed":False,"gate_result":"blocked_stale","error_reason":None}
    monkeypatch.setattr(live.gga,"rule_based_entry_without_gpt",chart_gate)
    r=live._execute_entry(cfg,object(),intent,{"symbol":intent.symbol},
                          1000.0,lambda:False,None)
    assert r["gate_result"]=="blocked_stale"
    assert not r["executed"]
    assert len(called)==1
