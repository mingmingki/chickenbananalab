from types import SimpleNamespace

import adaptive_exit_engine as ae
import gemini_analyzer
import openai_analyzer
from adaptive_exit_policy import production_adaptive_exit_policy


def test_contract_exposes_long_executable_stop_and_rr_bounds():
    p=production_adaptive_exit_policy()
    c=ae.build_ai_price_contract("long",100.0,2.0,p)
    assert c["entry_price"] == 100.0
    assert c["stop_price_min"] == 90.0
    assert c["stop_price_max"] == 97.0
    assert c["tp1_r_min"] == p["tp1_r_min"]
    assert ae.validate_ai_price_plan_contract({"stop_loss_price":96.0,"take_profit_1_price":106.0,"take_profit_2_price":112.0},c) == "ok"
    assert ae.validate_ai_price_plan_contract({"stop_loss_price":101.0,"take_profit_1_price":106.0,"take_profit_2_price":112.0},c) == "direction_invalid"


def test_contract_text_tells_reviewer_to_revise_invalid_gemini_plan():
    p=production_adaptive_exit_policy(); c=ae.build_ai_price_contract("long",100.0,2.0,p)
    text=ae.format_ai_price_contract(c)
    assert "entry_reference_price=100" in text
    assert "stop_price_range=[90" in text and "97" in text
    assert "approve 금지" in text and "revise" in text


def test_gemini_prompts_state_directional_price_invariant():
    assert "stop_loss_price < 현재가격 < take_profit_1_price" in gemini_analyzer.PROMPT_TEMPLATE
    assert "stop_loss_price > 현재가격 > take_profit_1_price" in gemini_analyzer.PROMPT_TEMPLATE
    assert "exit_price_contract" in gemini_analyzer.ENTRY_EXIT_PLAN_PROMPT_TEMPLATE


def test_openai_prompt_accepts_execution_contract_placeholder():
    assert "exit_price_contract" in openai_analyzer.PROMPT_TEMPLATE
    assert "approve 금지" in openai_analyzer.PROMPT_TEMPLATE


def test_gpt_approve_of_invalid_gemini_plan_is_not_accepted():
    p=production_adaptive_exit_policy(); c=ae.build_ai_price_contract("long",100.0,2.0,p)
    bad={"stop_loss_price":101.0,"take_profit_1_price":110.0,"take_profit_2_price":120.0}
    verdict,plan,reason=openai_analyzer._apply_exit_contract_to_verdict("approve",bad,None,c)
    assert verdict == "reject"
    assert plan is None and reason == "gemini_contract_direction_invalid"


def test_gpt_can_revise_invalid_gemini_plan_into_executable_prices():
    p=production_adaptive_exit_policy(); c=ae.build_ai_price_contract("long",100.0,2.0,p)
    bad={"stop_loss_price":101.0,"take_profit_1_price":110.0,"take_profit_2_price":120.0}
    fixed={"stop_loss_price":96.0,"take_profit_1_price":106.0,"take_profit_2_price":112.0,"confidence":0.8,"reasoning":"repair"}
    verdict,plan,reason=openai_analyzer._apply_exit_contract_to_verdict("revise",bad,fixed,c)
    assert verdict == "revise"
    assert plan["stop_loss_price"] == 96.0 and reason == "gpt_revision_contract_ok"


def test_core_gpt_gate_forwards_exit_price_contract(monkeypatch):
    import logging, trader
    captured={}
    def fake_verify(*args,**kwargs):
        captured.update(kwargs)
        return {"decision":"approve_now","confidence":0.8,"reasoning":"ok","exit_plan_decision":"approve","exit_plan":None,"error_reason":None}
    monkeypatch.setattr(trader.openai_analyzer,"verify",fake_verify)
    cfg=SimpleNamespace(logger=logging.getLogger("test"))
    contract=ae.build_ai_price_contract("long",100.0,2.0,production_adaptive_exit_policy())
    allowed,gate,result=trader._gpt_entry_gate(cfg,"X",["5m"],"summary",None,{"action":"long","confidence":0.8},exit_price_contract=contract)
    assert allowed is True and gate == "approved"
    assert captured["exit_price_contract"] == contract


def test_candidate_c_exit_review_forwards_same_contract_to_gemini_and_gpt(monkeypatch):
    import logging, candidate_c_gpt_gate_adapter as cc
    seen={}
    plan={"stop_loss_price":96.0,"take_profit_1_price":106.0,"take_profit_2_price":112.0,"confidence":0.8,"reasoning":"ok"}
    def fake_gemini(*args,**kwargs):
        seen["gemini_contract"]=kwargs.get("exit_price_contract")
        return {"action":"long","confidence":0.8,"exit_plan":plan,"reasoning":"ok"}
    def fake_verify(*args,**kwargs):
        seen["gpt_contract"]=kwargs.get("exit_price_contract")
        return {"decision":"approve_now","confidence":0.8,"reasoning":"ok","exit_plan_decision":"approve","exit_plan":None,"error_reason":None}
    monkeypatch.setattr(cc.gemini_analyzer,"propose_entry_exit_plan",fake_gemini)
    monkeypatch.setattr(cc.openai_analyzer,"verify",fake_verify)
    cfg=SimpleNamespace(logger=logging.getLogger("test"))
    intent=SimpleNamespace(side="long",raw_stop_price=94.0,raw_target_price=115.0,setup_id="s",reason_code="r")
    snap={"symbol":"SOL/USDT:USDT","tf_list":["4h","1h"],"current_price":100.0,"atr_4h":2.0,
          "trend_direction":"LONG","donchian_upper":99.0,"donchian_lower":90.0,"position":None}
    out=cc.review_exit_plan_only(cfg,intent,snap)
    assert seen["gemini_contract"] and "entry_reference_price=100" in seen["gemini_contract"]
    assert seen["gpt_contract"]["entry_price"] == 100.0
    assert out["ai_exit_source"] == "gemini_approved"
