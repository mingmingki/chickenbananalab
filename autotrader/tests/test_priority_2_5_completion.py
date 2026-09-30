from types import SimpleNamespace
import datetime as dt
import pytest

import ai_exit_plan_audit as audit
import analysis_report
import exit_reentry_shadow as ers
import web_app


def test_exchange_protection_enrichment_reads_okx_values_not_requested_copy():
    calls=[]
    class Client:
        def fetch_current_protection(self, close_side):
            calls.append(close_side)
            return {"algo_id":"a1","sl_price":91.25,"tp_price":123.5,"sz":7.0}
    base={"engine":"CORE","symbol":"ETH/USDT:USDT","side":"long",
          "order_executed":True,"final_sl":90.0,"final_tp":120.0}
    out=audit.enrich_with_exchange_protection(Client(), base)
    assert calls == ["sell"]
    assert out["exchange_verified"] is True
    assert out["actual_sl"] == 91.25
    assert out["actual_tp"] == 123.5
    assert out["actual_algo_id"] == "a1"
    assert out["actual_sl"] != out["final_sl"]

def test_ai_exit_report_prints_gemini_gpt_and_okx_prices():
    snap={"release":"r","period":"all","accounting_basis":"completed_lifecycle_economic_v1",
          "coverage":{},"overall":{},"symbols":[],"sides":[],"top_positive":[],"top_negative":[],
          "tf":[],"confidence":[],"self_learning":{"state_counts":{}},"positions":[],
          "system_issues":{},"review":{},"settings":{},"filter_counterfactual":{},
          "exit_reentry":{},"candidate_c_breakout_shadow":{},
          "ai_exit_observability":{"total":1,"ai_applied":1,"adaptive_fallback":0,
            "gpt":{"approve":0,"revise":1,"reject":0},
            "recent":[{"engine":"CORE","symbol":"ETH/USDT:USDT","side":"long",
              "gemini_exit_plan":{"stop_loss_price":90.0,"take_profit_1_price":110.0,"take_profit_2_price":120.0},
              "gpt_exit_plan_decision":"revise","gpt_exit_plan":{"stop_loss_price":91.0,"take_profit_1_price":111.0,"take_profit_2_price":121.0},
              "ai_source":"gpt_revised","result":"ai_exit_plan_applied","final_sl":91.0,"final_tp":121.0,
              "exchange_verified":True,"actual_sl":91.25,"actual_tp":121.5}]}}
    text=analysis_report.build_report(snap,now=dt.datetime(2026,9,29,17,0,tzinfo=dt.timezone(dt.timedelta(hours=9))))["text"]
    assert "Gemini SL 90.000000" in text
    assert "GPT revise" in text
    assert "GPT SL 91.000000" in text
    assert "OKX SL 91.250000" in text

def test_stale_adaptive_records_are_reported_separately(monkeypatch):
    rows=[
      {"mode":"LIVE_BOUNDED","plan_hash":"legacy","symbol":"XRP/USDT:USDT","decision_timestamp":0,
       "configured_notional":2000.0,"effective_notional":2000.0,"planned_loss_usdt":155.0},
      {"mode":"LIVE_BOUNDED","plan_hash":"old","symbol":"XRP/USDT:USDT","decision_timestamp":100,
       "configured_notional":2000.0,"effective_notional":2000.0,"planned_loss_usdt":150.0},
      {"mode":"LIVE_BOUNDED","plan_hash":"cur","symbol":"XRP/USDT:USDT","decision_timestamp":200,
       "configured_notional":1000.0,"effective_notional":800.0,"planned_loss_usdt":50.0,
       "side":"short","stop_price":1.6,"tp1":{"price":1.3},"tp2":{"price":1.1},"reason_code":"ok"},
    ]
    monkeypatch.setattr(web_app.adaptive_exit_log,"load_recent",lambda *a,**k:rows)
    cfg=SimpleNamespace(CORE_ORDER_MODE="FIXED_MARGIN_AUTO_EXIT",POSITION_FIXED_USDT=200.0,LEVERAGE=5)
    current=web_app._recent_adaptive_report_plans("/tmp/u",cfg=cfg)
    stale=web_app._recent_adaptive_stale_plans("/tmp/u",cfg=cfg)
    assert current[0]["configured_notional"] == 1000.0
    assert any(r["configured_notional"] == 2000.0 and r["stale_reason"] == "configured_notional_mismatch" for r in stale)
    assert any(r["decision_timestamp"] == 0 and r["stale_reason"] == "legacy_timestamp_missing" for r in stale)

def test_three_way_counterfactual_reports_net_for_close_reduce50_and_hold():
    row={"side":"long","exit_price":100.0,"original_sl":90.0,"original_tp":120.0,
         "actual_exit_lifecycle_net":-10.0,"actual_exit_final_close_net":-8.0,
         "exit_quantity_coin":1.0,"final_entry_price":105.0,"actual_close_fee_rate":0.001,
         "actual_close_funding_fee":0.0,
         "counterfactual_path":[{"time":"2026-09-29T10:30:00","close":118.0,"high":121.0,"low":99.0}],
         "horizons":{"120":{"time":"2026-09-29T12:00:00","close":118.0,"high":121.0,"low":99.0}}}
    out=ers.three_way_counterfactual(row)
    assert out["resolved"] is True
    assert out["close_all_net"] == pytest.approx(-10.0)
    # hypothetical final close at 120: gross 15, fee 0.12 => 14.88; prefix = -2
    assert out["hold_net"] == pytest.approx(12.88)
    assert out["reduce50_net"] == pytest.approx(1.44)
    assert out["winner"] == "HOLD"


def test_historical_counterfactual_path_backfill_uses_confirmed_fetcher(tmp_path):
    ers.record_ai_exit(str(tmp_path),{"symbol":"PI/USDT:USDT","side":"short",
        "exit_time":"2026-09-29T10:00:00+09:00","exit_price":100.0,"original_sl":110.0,"original_tp":80.0})
    bars=[{"time":"2026-09-29T10:05:00+09:00","close":99.0,"high":100.0,"low":98.0},
          {"time":"2026-09-29T12:00:00+09:00","close":90.0,"high":92.0,"low":89.0}]
    calls=[]
    def fetcher(symbol,start,end):
        calls.append((symbol,start,end)); return bars
    changed=ers.backfill_counterfactual_paths(str(tmp_path),fetcher)
    row=ers.recent(str(tmp_path),1)[0]
    assert changed >= 1 and calls
    assert len(row["counterfactual_path"]) == 2
    assert row["horizons"]["120"] is not None

def test_shadow_only_coverage_reconcile_promotes_pi_without_live_authority(tmp_path, monkeypatch):
    import learning_state
    pid="pi-pattern"
    learning_state.append_transition(str(tmp_path),pid,None,"DISCOVERY","initial_evidence",
        {"dimension":"symbol","value":"PI/USDT:USDT","sample_count":87,"coverage":0.625})
    learning_state.write_snapshot(str(tmp_path),{pid:{"state":"DISCOVERY","reason":"initial_evidence",
        "evidence":{"dimension":"symbol","value":"PI/USDT:USDT","sample_count":87,"coverage":0.625}}})
    evidence={pid:{"dimension":"symbol","value":"PI/USDT:USDT","sample_count":87,"coverage":1.0,
        "resolved_count":0,"shadow_benefit_net":0.0,"recent_benefit_net":0.0,"recent_direction":"negative",
        "long_direction":"negative","outlier_share":0.0,"data_integrity_issue":False}}
    out=learning_state.reconcile_shadow_only_dimension_coverage(str(tmp_path),evidence)
    state,_=learning_state.load_active_state(str(tmp_path))
    assert out["promoted"] == [pid]
    assert state[pid]["state"] == "SHADOW_LEARNING"
    assert all(x.get("new_state") not in ("VALIDATED","LIVE_BOUNDED") for x in learning_state.recent_transitions(str(tmp_path),20))
