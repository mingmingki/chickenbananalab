"""v33-26 validation: no information leakage, no orders, cost-sensitive cohorts."""
import json
from datetime import datetime,timedelta,timezone
from pathlib import Path

import pytest
import core_exit_strategy_eval_v33_26 as v

SYM="ETH/USDT:USDT"
KST=timezone(timedelta(hours=9))


def evidence(day=10,*,entry=100.,exit=98.,partial=None,signal=105.,mode="with_signal",identity="a"):
    opened=f"2026-10-{day:02}T09:00:00"
    closed=f"2026-10-{day:02}T09:40:00"
    prefix=dict(symbol=SYM,side="long",strategy_group="core",dry_run=False)
    items=[dict(prefix,type="open",time=opened,price=entry,
                amount=.1,execution_id="open-"+identity,sl_price=95.)]
    if partial:
        items.append(dict(prefix,type="reduce",time=f"2026-10-{day:02}T09:20:00",
            amount=partial,close_price=100.5,okx_net_pnl=.2,
            execution_id="red-"+identity,pnl_source="order_fills"))
    contract_remaining=1-partial if partial is not None else 1
    items.append(dict(prefix,type="close",time=closed,amount=contract_remaining,
                      close_price=exit,okx_net_pnl=-.25,
                      execution_id="close-"+identity,pnl_source="okx_realized"))
    signals=[]
    if mode=="with_signal":
        signals=[dict(event_type="mfe_profit_shadow_trigger",
           policy="arm_0.50_giveback_0.25",position_identity=f"journal:{SYM}|long|{opened}",
           side="long",symbol=SYM,
           observed_at=f"2026-10-{day:02}T09:10:00+09:00",
           trigger_price=signal,mfe_r=.75,current_r=.40,giveback_r=.35)]
    return items,signals


def test_verified_net_and_no_signal_baseline_stays_equal():
    j,s=evidence(mode="no_signal")
    r=v.analyze(j,s)
    assert r["data_quality"]["verified_completed"]==1
    assert r["candidate_tables"]["all"]["arm_0.50_giveback_0.25|fraction=0.25|slippage_bps=5"]["triggered"]==0
    assert all(t["delta_net_usdt"]==0 for t in r["candidate_tables"]["all"].values())


def test_candidate_profit_sensitive_and_fee_included():
    j,s=evidence()
    r=v.analyze(j,s)
    key="arm_0.50_giveback_0.25|fraction=0.25|slippage_bps=5"
    outcome=r["candidate_tables"]["all"][key]
    assert outcome["triggered"]==1
    assert outcome["delta_net_usdt"]>0
    assert outcome["simulated_net_usdt"]>outcome["baseline_net_usdt"]
    assert all(flag["live_eligible"] is False for flag in r["decisions"].values())


def test_premature_full_close_does_not_create_early_candidate():
    j,s=evidence(partial=.25)
    s[0]["observed_at"]="2026-10-10T09:30:00+09:00"
    report=v.analyze(j,s)
    key="arm_0.50_giveback_0.25|fraction=0.25|slippage_bps=5"
    row=report["completed"][0]["studies"][key]
    assert row["status"]=="TRIGGER_AFTER_FIRST_EXIT"
    assert report["candidate_tables"]["all"][key]["delta_net_usdt"]==0


def test_unreconciled_close_does_not_fabricate_hypothetical():
    j,s=evidence()
    j[-1]["amount"]=.5
    report=v.analyze(j,s)
    assert report["data_quality"]["full_exit_price_exclusions"]["exit_quantity_not_reconciled"]==1
    key="arm_0.50_giveback_0.25|fraction=0.25|slippage_bps=5"
    assert report["candidate_tables"]["all"][key]["triggered"]==0


def test_unverified_or_open_not_in_any_pnl():
    j,s=evidence()
    j[-1]["pnl_source"]="estimated"
    report=v.analyze(j,s)
    assert report["data_quality"]["verified_completed"]==0
    assert report["data_quality"]["excluded_lifecycles"]["CLOSED_UNVERIFIED"]==1
    assert report["candidate_tables"]["all"]["arm_0.50_giveback_0.25|fraction=0.25|slippage_bps=5"]["baseline_net_usdt"]==0


def test_costs_cannot_improve_given_same_executions():
    j,s=evidence(exit=105,signal=105.1)
    r=v.analyze(j,s)
    key_a="arm_0.50_giveback_0.25|fraction=0.25|slippage_bps=5"
    key_b="arm_0.50_giveback_0.25|fraction=0.25|slippage_bps=15"
    assert r["candidate_tables"]["all"][key_b]["delta_net_usdt"] <= r["candidate_tables"]["all"][key_a]["delta_net_usdt"]


def test_holdout_no_in_sample_leakage_or_gate_without_minimum():
    journal=[];mfe=[]
    for day in (10,11,12,13,14):
        a,b=evidence(day,identity=str(day))
        journal+=a;mfe+=b
    r=v.analyze(journal,mfe)
    assert r["split"]["train"]==3 and r["split"]["holdout"]==2
    assert all(d["live_eligible"] is False for d in r["decisions"].values())


def test_mfe_cohort_classification_is_not_called_never_profitable():
    j,s=evidence(exit=98,mode="no_signal")
    r=v.analyze(j,s)
    assert r["classification_counts"]["LOSS_WITHOUT_DOCUMENTED_MFE_TRIGGER"]==1
    assert "NEVER_PROFITABLE" not in r["classification_counts"]


def test_simulation_does_not_place_orders():
    source=Path("core_exit_strategy_eval_v33_26.py").read_text()
    assert "may_execute=False" in source
    for token in ("create_order(", "cancel_order(", "amend_protective_stop(", "fetch_ohlcv(",
                  "openai_analyzer.", "gemini_analyzer."):
        assert token not in source


def test_on_user_dir_requires_no_connected_exchange(tmp_path):
    j,s=evidence()
    (tmp_path/"trades_log.jsonl").write_text("".join(json.dumps(row)+"\n" for row in j))
    (tmp_path/"mfe_profit_shadow_log.jsonl").write_text("".join(json.dumps(row)+"\n" for row in s))
    r=v.from_user_dir(tmp_path,include_completed=False)
    assert r["may_execute"] is False
    assert "completed" not in r
    assert r["data_quality"]["verified_completed"]==1


def test_authenticated_read_only_api_contract():
    code=Path("web_app.py").read_text()
    route=code.split("@app.route('/api/ops/exit-strategy-v33-26'",1)[1].split(
        "@app.route(",1)[0]
    assert "@login_required" in route
    assert "get_context(session['username'])" in route
    assert "core_exit_strategy_eval_v33_26.from_user_dir" in route
    for forbidden in ("create_order(", "cancel_order(", "fetch_position(", "openai_analyzer."):
        assert forbidden not in route
