import datetime as dt
import importlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import adaptive_exit_log
import mfe_profit_shadow as mfe
import trader
import web_app

SYMBOL = "BTC/USDT:USDT"

@pytest.mark.parametrize("side,stop,changed,peak,retrace", [
    ("long",90.,95.,106.,103.), ("short",110.,105.,94.,97.),
    ("long",90.,80.,106.,103.), ("short",110.,120.,94.,97.)])
def test_profit_giveback_uses_frozen_initial_r_after_stop_changes(tmp_path,side,stop,changed,peak,retrace):
    pos=dict(position_id="p1",side=side,entry_price=100.,contracts=4.)
    mfe.observe(str(tmp_path),SYMBOL,pos,sl_price=stop,price=peak,bar_time="b1")
    out=mfe.observe(str(tmp_path),SYMBOL,pos,sl_price=changed,price=retrace,bar_time="b2")
    assert out["initial_r"] == 10.
    assert out["mfe_r"] == pytest.approx(.6)
    assert out["current_r"] == pytest.approx(.3)
    assert out["giveback_r"] == pytest.approx(.3)
    assert out["live_candidate"]["initial_r"] == 10.

def test_corrupt_persisted_r_never_authorizes_profit_candidate(tmp_path):
    pos=dict(position_id="p1",side="long",entry_price=100.,contracts=4.)
    mfe.observe(str(tmp_path),SYMBOL,pos,sl_price=90.,price=106.,bar_time="b1")
    f=tmp_path/mfe.STATE_FILE
    data=json.loads(f.read_text());data[SYMBOL]["initial_r"]=float("inf");f.write_text(json.dumps(data))
    before=f.read_bytes()
    with pytest.raises(ValueError,match="initial_r"):
        mfe.observe(str(tmp_path),SYMBOL,pos,sl_price=90.,price=103.,bar_time="b2")
    assert f.read_bytes() == before

def cooldown_fixture():
    spec=importlib.util.spec_from_file_location("v9_cooldown_fixture",Path(__file__).with_name("test_post_reduce_close_cooldown.py"))
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod);return mod

@pytest.mark.parametrize("side",["long","short"])
def test_confirmed_breakdown_can_close_residual_during_general_cooldown(monkeypatch,tmp_path,side):
    actions,events=cooldown_fixture().review(monkeypatch,tmp_path,side=side,breakdown=True)
    assert actions == [("close","position_ai_close_all")]
    assert any(e.get("reason") == "confirmed_breakdown_after_reduce" for e in events)

def test_corrupt_reduce_time_still_blocks_breakdown_general_review(monkeypatch,tmp_path):
    actions,events=cooldown_fixture().review(monkeypatch,tmp_path,breakdown=True,
        saved_fields={"last_reduction_order_time":"invalid"})
    assert actions == []
    assert any(e.get("state_error") == "reduction_time_unavailable" for e in events)

def test_real_nested_plan_displays_targets_and_margin_risk_without_writes(tmp_path):
    row=dict(symbol=SYMBOL,decision_timestamp=123,policy_hash="p",input_snapshot_hash="s",mode="LIVE_BOUNDED",
        plan_hash="plan",configured_notional=1000.,effective_notional=750.,planned_loss_usdt=30.,
        stop_price=95.,tp1={"price":110.,"fraction":.25},tp2={"price":120.,"fraction":.25},
        runner_fraction=.5,reason_code="adaptive",diagnostics=[])
    adaptive_exit_log.append_plan(tmp_path,row)
    f=tmp_path/"adaptive_exit_plans.jsonl";before=f.read_bytes()
    out=web_app._adaptive_exit_state_for_api(tmp_path,[SYMBOL],leverage=5.,equity=1500.)[SYMBOL]
    assert out["tp1_price"] == 110.
    assert out["tp2_price"] == 120.
    assert out["configured_margin_usdt"] == 200.
    assert out["effective_margin_usdt"] == 150.
    assert out["planned_loss_equity_pct"] == 2.
    assert out["margin_basis"] == "latest_plan_notional/configured_leverage"
    assert f.read_bytes() == before

def evidence(side="long",identity="journal:BTC/USDT:USDT|long|2026-10-10T20:00:00"):
    opened=dict(side=side,entry_price=100.,time="2026-10-10T20:00:00")
    pos=dict(position_id="p1",entry_timestamp_ms=123,side=side,entry_price=100.,
        mark_price=103. if side=="long" else 97.,contracts=3.)
    peak=dict(position_identity=identity,side=side,entry_price=100.,initial_r=10.,
        mfe_r=.6,entry_timestamp_ms=123,last_bar_time="2026-10-10T11:05:00+00:00")
    reduced=dict(baseline_known=True,initial_contracts=4.,actual_reduced_contracts=1.,lifecycle_id="p1:123:"+side,cumulative_reduced_ratio=.25,reduce_stage=1,
        last_reduction_order_time="2026-10-10T20:03:00")
    return pos,opened,peak,reduced

@pytest.mark.parametrize("side",["long","short"])
def test_ai_context_uses_same_trade_peak_and_actual_residual(side):
    m=importlib.import_module("position_management_context")
    args=evidence(side,"journal:BTC/USDT:USDT|"+side+"|2026-10-10T20:00:00")
    out=m.build(SYMBOL,*args,now=dt.datetime(2026,10,10,11,7,tzinfo=dt.timezone.utc))
    assert out["mfe_known"] is True
    assert out["mfe_r"] == .6
    assert out["current_r"] == pytest.approx(.3)
    assert out["giveback_r"] == pytest.approx(.3)
    assert out["cumulative_reduced_ratio"] == .25
    assert out["remaining_contracts"] == 3.
    assert out["order_authority"] is False

@pytest.mark.parametrize("identity",["old-okx-id","journal:BTC/USDT:USDT|long|2026-10-09T20:00:00"])
def test_ai_context_never_attaches_previous_trade_peak(identity):
    m=importlib.import_module("position_management_context")
    out=m.build(SYMBOL,*evidence(identity=identity),
        now=dt.datetime(2026,10,10,11,7,tzinfo=dt.timezone.utc))
    assert out["mfe_known"] is False
    assert "mfe_r" not in out

def test_ai_context_excludes_stale_peak_and_other_lifecycle_reduction():
    m=importlib.import_module("position_management_context")
    pos,opened,peak,reduced=evidence();reduced["lifecycle_id"]="p1:456:long"
    out=m.build(SYMBOL,pos,opened,peak,reduced,
        now=dt.datetime(2026,10,10,12,tzinfo=dt.timezone.utc))
    assert out["mfe_known"] is False
    assert out["reduction_known"] is False
    assert "cumulative_reduced_ratio" not in out

def test_ai_handlers_share_evidence_without_extra_provider_call(monkeypatch,tmp_path):
    import logging
    from state import TraderState
    cfg=SimpleNamespace(user_dir=str(tmp_path),OPENAI_API_KEY="test",MIN_CONFIDENCE=.6,
        POSITION_AI_REVIEW_COOLDOWN_MINUTES=15,POSITION_AI_LIVE_EXECUTE=True,
        logger=logging.getLogger("v9-context"))
    pos,opened,peak,reduced=evidence()
    calls=[]
    monkeypatch.setattr(trader,"_position_management_evidence",lambda *_:"\n[POSITION_MANAGEMENT_EVIDENCE]\nverified same-trade peak\n")
    monkeypatch.setattr(trader.gemini_analyzer,"analyze_held_position",
        lambda *a,**k:calls.append(("gemini",a[3])) or {"assessment":"thesis_intact","confidence":.9})
    monkeypatch.setattr(trader.openai_analyzer,"verify_position_management",
        lambda *a,**k:calls.append(("gpt",a[3])) or {"action":"HOLD","confidence":.9})
    client=SimpleNamespace(fetch_current_protection=lambda *_:None)
    trader._handle_position_ai_review(cfg,TraderState(),client,SYMBOL,["5m"],"candles",pos,{"action":"hold"},{})
    assert len(calls) == 2
    assert all("[POSITION_MANAGEMENT_EVIDENCE]" in summary for _,summary in calls)

def test_ai_context_reads_real_open_journal_schema(tmp_path):
    import trade_log
    m=importlib.import_module("position_management_context")
    pos,_,peak,reduced=evidence()
    trade_log.record_open(str(tmp_path),SYMBOL,"long",100.,4.,False,sl_price=90.,tp_price=130.)
    opened=trade_log.last_unclosed_open(str(tmp_path),SYMBOL)
    assert "price" in opened and "entry_price" not in opened
    peak["position_identity"]="journal:%s|long|%s"%(SYMBOL,opened["time"])
    out=m.build(SYMBOL,pos,opened,peak,reduced,now=dt.datetime(2026,10,10,11,7,tzinfo=dt.timezone.utc))
    assert out["mfe_known"] is True

def test_ai_context_keeps_unknown_reduction_baseline_unknown():
    m=importlib.import_module("position_management_context")
    pos,opened,peak,reduced=evidence()
    reduced.update(baseline_known=False,cumulative_reduced_ratio=0.,reduce_stage=0)
    out=m.build(SYMBOL,pos,opened,peak,reduced,now=dt.datetime(2026,10,10,11,7,tzinfo=dt.timezone.utc))
    assert out["reduction_known"] is False
    assert "cumulative_reduced_ratio" not in out

@pytest.mark.parametrize("stamp",[None,456])
def test_ai_context_requires_same_exchange_entry_timestamp(stamp):
    m=importlib.import_module("position_management_context")
    pos,opened,peak,reduced=evidence();peak["entry_timestamp_ms"]=stamp
    out=m.build(SYMBOL,pos,opened,peak,reduced,now=dt.datetime(2026,10,10,11,7,tzinfo=dt.timezone.utc))
    assert out["mfe_known"] is False

def test_reused_journal_and_exchange_id_reset_peak_on_new_fill(tmp_path):
    pos=dict(position_id="same",lifecycle_id="same-journal",entry_timestamp_ms=123,
             side="long",entry_price=100.,contracts=4.)
    mfe.observe(str(tmp_path),SYMBOL,pos,sl_price=90.,price=108.,bar_time="b1")
    pos["entry_timestamp_ms"]=456
    out=mfe.observe(str(tmp_path),SYMBOL,pos,sl_price=90.,price=100.2,bar_time="b2")
    assert out["entry_timestamp_ms"] == 456
    assert out["mfe_r"] == pytest.approx(.02)
    assert out["new_triggers"] == []
    assert out["live_candidate"] is None

def test_real_exchange_fill_time_excludes_old_journal_candles(tmp_path,monkeypatch):
    import logging
    import pandas as pd
    cfg=SimpleNamespace(user_dir=str(tmp_path),logger=logging.getLogger("v9-entry"))
    entered=pd.Timestamp("2026-10-10T11:00:30Z")
    pos=dict(position_id="same",entry_timestamp_ms=int(entered.timestamp()*1000),
             side="long",entry_price=100.,contracts=4.)
    monkeypatch.setattr(trader.trade_log,"last_unclosed_open",lambda *_:dict(
        side="long",price=100.,sl_price=90.,time="2026-10-10T19:50:00"))
    frame=pd.DataFrame([dict(timestamp=pd.Timestamp("2026-10-10T10:55:00Z"),close=103.,high=110.,low=99.),
                        dict(timestamp=pd.Timestamp("2026-10-10T11:01:00Z"),close=100.2,high=100.3,low=100.)])
    out=trader._observe_mfe_profit_shadow(cfg,SYMBOL,pos,{"1m":frame})
    assert out["mfe_r"] == pytest.approx(.03)
