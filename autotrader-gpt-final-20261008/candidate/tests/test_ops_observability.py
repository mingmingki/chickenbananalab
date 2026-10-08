import datetime as dt
import json
from pathlib import Path
import ops_observability as o

def dump(path,obj):
    Path(path).write_text(json.dumps(obj,ensure_ascii=False),encoding="utf-8")

def lines(path,rows):
    Path(path).write_text("\n".join(json.dumps(x,ensure_ascii=False) for x in rows)+"\n",encoding="utf-8")

def test_snapshot_aggregates_strategy_cost_and_health(tmp_path):
    now=dt.datetime(2026,10,6,20,0,tzinfo=o.KST)
    dump(tmp_path/"trade_learning_analysis_cache.json",{"analysis":{
        "generated_at":"2026-10-06T19:00:00+09:00",
        "groups":[
          {"dimension":"side","value":"long","count":3,"net_pnl":5,"win_rate":66.7,"profit_factor":1.5},
          {"dimension":"side","value":"short","count":2,"net_pnl":-2,"win_rate":50,"profit_factor":0.8}],
        "trades":[{"trade_id":"t1","side":"long","entry_price":100,"exit_price":105,"net_pnl":5,"partial_reduction_count":1}]
    }})
    lines(tmp_path/"entry_counterfactual_shadow.jsonl",[{
        "entry_id":"e1","trade_id":"t1","entry_time":"2026-10-06T10:00:00+09:00","resolved":True,
        "late_entry_signature":True,"immediate_adverse":False,"clean_follow_through":True,
        "horizons":{"30":{"mfe_r":.5,"mae_r":.1},"60":{"mfe_r":1,"mae_r":.2},"120":{"mfe_pct":10,"mfe_r":2,"mae_r":.3}}}])
    lines(tmp_path/"token_usage.jsonl",[
      {"time":"2026-10-06T19:00:00+09:00","provider":"openai","purpose":"entry_gate","cost_usd":.1},
      {"time":"2026-10-06T19:10:00+09:00","provider":"gemini","purpose":"review","cost_usd":.2}])
    lines(tmp_path/"ai_review_reports.jsonl",[{"created_at":"2026-10-06T18:00:00+09:00","status":"no_new_sample"}])
    lines(tmp_path/"mfe_profit_shadow_log.jsonl",[{"observed_at":"2026-10-06T19:00:00+09:00","event_type":"mfe_profit_shadow_trigger"}])
    # Stale legacy health files must not create false alarms.
    dump(tmp_path/"worker_health_state.json",{"running":False,"updated_at_ms":1,"heartbeats_ms":{"BTC/USDT:USDT":1}})
    dump(tmp_path/"daily_loss_baseline.json",{"start_equity":1000})
    lines(tmp_path/"capital_flow_equity_snapshots.jsonl",[{"ts_ms":now.timestamp()*1000,"equity":990}])
    live={"running":True,"last_error":None,"settings":{"max_daily_loss_pct":5.0},"symbols":{}}
    snap=o.build_snapshot(tmp_path,journal_text="ENTRY_FRESHNESS side=long allowed=False\nevent_call_budget",
                          recent_journal_text="[BTC/USDT:USDT] TF=1m,3m,5m,1h,4h,1d",
                          live_state=live,now=now)
    assert snap["live_authority"] is False
    assert snap["strategy"]["entry_quality"]["late_entry_count"]==1
    assert snap["strategy"]["mfe_capture"]["median_capture_pct"]==50.0
    assert snap["strategy"]["side_performance"]["long"]["profit_factor"]==1.5
    assert snap["strategy"]["effects"]["partial_take_profit"]["net_pnl"]==5
    assert snap["cost_efficiency"]["actual_paid_calls_24h"]==2
    assert snap["cost_efficiency"]["gpt_actual_calls_24h"]==1
    assert snap["cost_efficiency"]["observed_cache_skip_24h"]==2
    assert snap["health"]["ok"] is True

def test_health_flags_missing_core_protection(tmp_path):
    now=dt.datetime(2026,10,6,20,0,tzinfo=o.KST)
    live={"running":True,"settings":{"max_daily_loss_pct":5.0},"symbols":{
        "ETH/USDT:USDT":{"live_position":{"side":"long","contracts":1},
                         "live_protection":{"status":"MISSING"}}}}
    h=o.evaluate_health(tmp_path,live_state=live,
                        recent_journal_text="[ETH/USDT:USDT] TF=1m,3m,5m,1h,4h,1d",
                        now_epoch=now.timestamp())
    assert any(x["code"]=="core_protection_missing" for x in h["issues"])
    assert h["ok"] is False

def test_health_requires_current_state_api(tmp_path):
    now=dt.datetime(2026,10,6,20,0,tzinfo=o.KST)
    h=o.evaluate_health(tmp_path,live_state=None,now_epoch=now.timestamp())
    assert any(x["code"]=="state_api_unavailable" for x in h["issues"])
    assert h["ok"] is False

def test_observability_has_no_trading_authority():
    text=Path(o.__file__).read_text(encoding="utf-8")
    for token in ("create_order","cancel_order","set_leverage","trader.py"):
        assert token not in text

def test_watchdog_refresh_contract_has_no_learning_or_trading_authority():
    root=Path(__file__).resolve().parents[1]
    text=(root/"ops_watchdog.py").read_text(encoding="utf-8")
    assert "run_analysis(str(user_dir),update_hypotheses=False)" in text
    for token in ("create_order","cancel_order","set_leverage"):
        assert token not in text

def test_runtime_error_count_is_event_based_not_traceback_line_based(tmp_path):
    now=dt.datetime(2026,10,7,17,10,tzinfo=o.KST)
    timeout_one=("2026-10-07 17:06:52,162 [ERROR] trader: GPT API call failed\n"
                 "Traceback (most recent call last):\n"
                 "  File \"x.py\", line 1, in x\n"
                 "httpcore2.ReadTimeout: timed out\n"
                 "The above exception was the direct cause of the following exception:\n"
                 "Traceback (most recent call last):\n"
                 "openai.APITimeoutError: Request timed out.\n"
                 "2026-10-07 17:06:52,322 [INFO] trader: GPT validation failed\n")
    timeout_two=timeout_one.replace("17:06:52","17:07:52")
    journal=("[BTC/USDT:USDT] TF=1m,3m,5m,1h,4h,1d\n"+timeout_one+timeout_two)
    h=o.evaluate_health(tmp_path,live_state={"running":True,"symbols":{}},
                        recent_journal_text=journal,now_epoch=now.timestamp())
    assert o._runtime_error_event_count(journal)==2
    assert not any(x["code"]=="repeated_runtime_errors" for x in h["issues"])

    journal+="2026-10-07 17:08:52,162 [ERROR] trader: independent exchange failure\n"
    h=o.evaluate_health(tmp_path,live_state={"running":True,"symbols":{}},
                        recent_journal_text=journal,now_epoch=now.timestamp())
    issue=next(x for x in h["issues"] if x["code"]=="repeated_runtime_errors")
    assert issue["detail"]=="3 error events in recent window"
