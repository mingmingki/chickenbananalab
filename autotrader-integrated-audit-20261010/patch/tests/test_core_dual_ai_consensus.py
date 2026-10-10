"""CORE consensus regression: fake only the provider/exchange boundaries."""
import datetime as dt
import json
import logging
from types import SimpleNamespace
from pathlib import Path

import pytest
import trader
import openai_analyzer
import gpt_shadow_log
from state import TraderState


@pytest.fixture
def entry_env(tmp_path, monkeypatch):
    real = dt.datetime
    class Clock(real):
        @classmethod
        def now(cls, tz=None):
            result = cls(2026, 10, 7, 14, 0)
            return result.replace(tzinfo=tz) if tz is not None else result
    monkeypatch.setattr(trader.datetime, "datetime", Clock)
    symbol = "ADA/USDT:USDT"
    cfg = SimpleNamespace(
        user_dir=str(tmp_path), logger=logging.getLogger("consensus"),
        GPT_ENTRY_GATE_ENABLED=True, OPENAI_API_KEY="fixture",
        MIN_CONFIDENCE=.6, LEVERAGE=5,
        _core_loss_guards={symbol: SimpleNamespace(allow_new_entry=lambda equity: True)},
    )
    state = TraderState()
    client = SimpleNamespace(fetch_usdt_equity=lambda: 1887.81, fetch_last_price=lambda: 100.,
                             fetch_position=lambda: None)
    orders = []
    def submit(*args, **kwargs):
        orders.append({"side":args[4], "amount":args[5], "sl":args[7], "tp":args[8]})
        return True
    monkeypatch.setattr(trader, "_execute_entry", submit)
    monkeypatch.setattr(trader.ai_exit_plan_audit, "enrich_with_exchange_protection", lambda _c,r:r)
    return cfg, state, client, symbol, orders


def invoke(env, monkeypatch, *, side="short", verdict="approve_now", confidence=.78):
    cfg,state,client,symbol,orders=env
    seen=[]
    def review(*args, **kwargs):
        seen.append(args[5])
        return {"decision":verdict, "confidence":confidence, "reasoning":"fixture",
                "exit_plan_decision":"reject", "exit_plan":None,
                "error_reason":"timeout" if verdict is None else None}
    monkeypatch.setattr(openai_analyzer, "verify", review)
    trader._handle_new_entry(
        cfg,state,client,symbol,side,{"action":side,"confidence":.72},
        "consensus-d1","entry",["5m"],"fixture",None,100.,10.,
        104. if side=="short" else 96.,92. if side=="short" else 108.,
    )
    return seen


@pytest.mark.parametrize("side,confidence",[
    ("short",.6),("short",.75),("short",.78),("long",.75),("long",.78),
])
def test_approved_core_order_uses_configured_amount_without_historical_score_veto(entry_env,monkeypatch,side,confidence):
    seen=invoke(entry_env,monkeypatch,side=side,confidence=confidence)
    _,state,_,symbol,orders=entry_env
    assert len(seen)==1
    assert orders==[{"side":side,"amount":10.,"sl":104. if side=="short" else 96.,
                    "tp":92. if side=="short" else 108.}]
    attempt=state.snapshot()["symbols"][symbol]["last_entry_attempt"]
    assert attempt["status"]=="FILLED"
    assert attempt["gpt_confidence"]==confidence


@pytest.mark.parametrize("verdict,confidence,status",[
    ("wait",.99,"GPT_WAIT"),("reject",.99,"GPT_REJECT"),
    (None,None,"GPT_ERROR"),("approve_now",None,"GPT_ERROR"),
])
def test_nonapproval_never_submits(entry_env,monkeypatch,verdict,confidence,status):
    seen=invoke(entry_env,monkeypatch,verdict=verdict,confidence=confidence)
    _,state,_,symbol,orders=entry_env
    assert len(seen)==1
    assert orders==[]
    assert state.snapshot()["symbols"][symbol]["last_entry_attempt"]["status"]==status


@pytest.mark.parametrize("block",["pause","kill","daily_loss","stale_price"])
def test_execution_limits_still_prevent_order(entry_env,monkeypatch,block):
    cfg,state,client,symbol,orders=entry_env
    if block=="pause":
        import symbol_entry_control
        monkeypatch.setattr(symbol_entry_control,"is_paused",lambda *_:True)
    elif block=="kill":
        monkeypatch.setattr(trader.core_kill_switch,"is_active",lambda *_:True)
        monkeypatch.setattr(trader.core_kill_switch,"get_reason",lambda *_:"fixture")
    elif block=="daily_loss":
        cfg._core_loss_guards[symbol].allow_new_entry=lambda _:False
    else:
        client.fetch_last_price=lambda:101.
    invoke(entry_env,monkeypatch)
    assert orders==[]


def test_failed_exchange_submission_is_reported_as_failed(entry_env,monkeypatch):
    monkeypatch.setattr(trader,"_execute_entry",lambda *a,**k:False)
    invoke(entry_env,monkeypatch)
    cfg,state,_,symbol,_=entry_env
    attempt=state.snapshot()["symbols"][symbol]["last_entry_attempt"]
    assert attempt["status"]=="LOCAL_BLOCKED"
    assert attempt["reason"]=="entry_not_submitted"
    row=gpt_shadow_log.recent_by_mode(cfg.user_dir,"entry_gate")[0]
    assert row["gpt_decision"]=="approve_now"
    assert row["order_success"] is False


@pytest.mark.parametrize("gate,success,want",[
    ("approved",True,"주문 완료"),
    ("approved",False,"승인 · 주문 미실행"),
    ("blocked_risk_score",False,"과거 위험점수 차단"),
    ("blocked_wait",False,"GPT 대기"),
    ("blocked_error",False,"GPT 오류"),
])
def test_dashboard_api_distinguishes_model_verdict_and_order_result(tmp_path,gate,success,want):
    gpt_shadow_log.record_verification(str(tmp_path),"XRP/USDT:USDT",
        gemini_action="short",gemini_confidence=.72,gpt_decision="approve_now",
        gpt_confidence=.78,gpt_reasoning="fixture",decision_id="d",
        event_type="entry",order_success=success,mode="entry_gate",gate_result=gate)
    row=gpt_shadow_log.recent_by_mode(str(tmp_path),"entry_gate")[0]
    assert row.get("order_outcome")==want


def test_gpt_receives_local_market_observations_even_with_short_summary_condensing(monkeypatch,tmp_path):
    prompts=[]
    def create(**kwargs):
        prompts.append(kwargs["messages"][0]["content"])
        return SimpleNamespace(usage=None, choices=[SimpleNamespace(message=SimpleNamespace(
            content=json.dumps({"decision":"approve_now","confidence":.78,"reasoning":"fixture",
                                "exit_plan_decision":"reject","exit_plan":None})))])
    fake=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    fake.with_options=lambda **kw:fake
    monkeypatch.setattr(openai_analyzer,"_get_client",lambda _:fake)
    monkeypatch.setattr(openai_analyzer,"_ensure_response_models_warmed_up",lambda:None)
    cfg=SimpleNamespace(OPENAI_API_KEY="fixture",OPENAI_MODEL="fixture",
        user_dir=str(tmp_path),logger=logging.getLogger("consensus_prompt"))
    openai_analyzer.verify(cfg,"ADA/USDT:USDT",["5m","1d"],
        "[1일봉] daily\n[5분봉] current microstructure",None,
        {"action":"short","confidence":.72,
         "_core_entry_observations":{"short_level_none":{"level":"NONE"},"entry_overextension":{"reason":"short_chase_30m_drop"}}},
        purpose="entry_gate",short_level_ctx={"level":"NONE","reasons":{}})
    assert "short_chase_30m_drop" in prompts[0]
    assert "[5분봉] current microstructure" in prompts[0]


@pytest.mark.parametrize("case",["short_none","long_1h","correction_long","chase"])
def test_cycle_market_filters_reach_gpt_for_final_decision(tmp_path,monkeypatch,case):
    from test_core_ai_budget_gate_20261004 import cycle_fixture
    cfg,state,client=cycle_fixture(tmp_path,monkeypatch)
    side="short" if case=="short_none" else "long"
    cfg.GPT_ENTRY_GATE_ENABLED=True
    cfg.OPENAI_API_KEY="fixture"
    cfg.MIN_CONFIDENCE=.6
    cfg.POSITION_SIZE_MODE="FIXED"
    cfg.POSITION_FIXED_USDT=200.
    cfg.CORE_SHORT_SIZING_MODE="fixed"
    cfg.CORE_SHORT_MAX_MARGIN_USDT=200.
    cfg.LEVERAGE=5
    cfg.REENTRY_COOLDOWN_MINUTES=15
    cfg.STOP_LOSS_PCT=2.
    cfg.TAKE_PROFIT_PCT=4.
    client.fetch_last_price=lambda:100.
    guard=SimpleNamespace(allow_new_entry=lambda _:True)
    monkeypatch.setattr(trader.gemini_analyzer,"analyze",lambda *a,**k:{"action":side,"confidence":.72})
    monkeypatch.setattr(trader.core_long_confirmation,"check_long_confirmation",lambda *a:(False,"one_h_weak"))
    monkeypatch.setattr(trader.core_short_level,"classify",lambda **kw:
                        {"level":"NONE","reasons":{"4h_bearish":False},"regime":"bearish","confidence":.72})
    monkeypatch.setattr(trader,"_core_entry_overextension_gate",lambda *a:
                        {"allowed":case!="chase","reason":"short_chase_30m_drop" if case=="chase" else "ok"})
    if case=="correction_long":
        monkeypatch.setattr(trader.core_post_runup_correction,"evaluate",lambda *a:{"active":True,"runup_24h_pct":3.,"peak_retracement_pct":1.5,"peak_extension_atr":2.})
    monkeypatch.setattr(trader.risk_manager,"calculate_position_size",lambda *a:10.)
    monkeypatch.setattr(trader.risk_manager,"quantize_coin_amount_to_market",lambda *a:a[-1])
    monkeypatch.setattr(trader.risk_manager,"sl_tp_prices",lambda *a:(96.,108.))
    monkeypatch.setattr(trader,"_run_core_adaptive_entry_shadow",lambda *a,**k:None)
    monkeypatch.setattr(trader,"_core_adaptive_live_entry_decision",lambda *a,**k:{"active":False})
    approvals=[];orders=[]
    def review(*args,**kwargs):
        approvals.append({"decision":args[5],"short_level":kwargs.get("short_level_ctx")})
        return {"decision":"approve_now","confidence":.78,"reasoning":"fixture",
                "exit_plan_decision":"reject","exit_plan":None,"error_reason":None}
    monkeypatch.setattr(openai_analyzer,"verify",review)
    def submit(*args,**kwargs):
        orders.append((args[4],args[5]))
        state.update_symbol("BTC/USDT:USDT",position={"side":args[4],"entry_price":100.,"contracts":args[5]})
        return True
    monkeypatch.setattr(trader,"_execute_entry",submit)
    monkeypatch.setattr(trader.ai_exit_plan_audit,"enrich_with_exchange_protection",lambda _c,r:r)
    trader.run_cycle(cfg,state,client,"BTC/USDT:USDT",guard)
    assert len(approvals)==1
    assert orders==[(side,10.)]
    if case=="short_none":
        assert approvals[0]["short_level"]["sizing_mode"]=="fixed"
        assert approvals[0]["short_level"]["selected_margin"]==200.
    assert state.snapshot()["symbols"]["BTC/USDT:USDT"]["last_entry_attempt"]["status"]=="FILLED"
