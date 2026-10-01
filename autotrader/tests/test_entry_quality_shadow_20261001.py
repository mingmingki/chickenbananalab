import importlib.util


def test_entry_quality_shadow_module_exists():
    assert importlib.util.find_spec("entry_quality_shadow") is not None

import entry_quality_shadow as shadow
import analysis_report


def _trade(trade_id="t1", *, entry_time="2026-10-01T14:12:57", group="core",
           side="long", net=-5.0, gpt="approve_now", alignment="with_regime",
           states=None, manual=False):
    states = states or {tf: "bullish" for tf in ("1d", "4h", "1h", "5m", "3m")}
    open_event = {"type": "open", "market_regime": "bullish", "trade_alignment": alignment}
    if manual:
        open_event["market_regime"] = None
        open_event["trade_alignment"] = None
    return {
        "trade_id": trade_id, "symbol": "BTC/USDT:USDT", "side": side,
        "entry_time": entry_time, "strategy_group": group,
        "net_pnl": net, "lifecycle_net": net, "events": [open_event],
        "features": {
            "tf": {tf: {"state": state} for tf, state in states.items()},
            "gpt_decision": gpt,
            "gpt_confidence": 0.8 if gpt == "approve_now" else None,
            "gemini_confidence": 0.78,
            "trade_alignment": alignment,
        },
    }


def _entry_shadow(trade_id="t1", mfe60=0.5, mae60=0.2):
    return {
        "trade_id": trade_id, "resolved": True, "barrier_outcome": "none_120m",
        "horizons": {
            "30": {"mfe_r": 0.2, "mae_r": 0.1},
            "60": {"mfe_r": mfe60, "mae_r": mae60},
            "120": {"mfe_r": 0.8, "mae_r": 0.3},
        },
    }


def test_cohort_boundary_is_inclusive_and_predeploy_is_excluded():
    before = _trade("before", entry_time="2026-10-01T14:12:56")
    edge = _trade("edge", entry_time="2026-10-01T14:12:57")
    after = _trade("after", entry_time="2026-10-01T14:13:00")
    selected = shadow.select_post_deploy_cohort([before, edge, after])
    assert [r["trade_id"] for r in selected] == ["edge", "after"]


def test_provenance_splits_auto_core_candidate_and_manual_core():
    auto = _trade("auto")
    candidate = _trade("cc", group="candidate_c", gpt="unknown")
    manual = _trade("manual", gpt="unknown", manual=True)
    assert shadow.classify_provenance(auto) == "auto_core"
    assert shadow.classify_provenance(candidate) == "candidate_c_rule"
    assert shadow.classify_provenance(manual) == "manual_core"


def test_gate_inputs_are_entry_time_only_and_future_labels_are_separate():
    out = shadow.evaluate_trade(_trade(), _entry_shadow())
    assert out["live_authority"] is False
    assert out["mode"] == "shadow_only"
    assert out["gate_inputs"]["tf_4h_aligned"] is True
    assert out["labels"]["mfe60_r"] == 0.5
    assert "mfe60_r" not in out["gate_inputs"]
    assert "mae60_r" not in out["gate_inputs"]
    assert "actual_net" not in out["gate_inputs"]


def test_rule_grid_keeps_conditions_separate_instead_of_reusing_score4():
    states = {"1d": "bullish", "4h": "bullish", "1h": "bullish",
              "5m": "bullish", "3m": "bearish"}
    out = shadow.evaluate_trade(_trade(states=states), _entry_shadow())
    rules = out["candidate_rules"]
    assert rules["strict_4h1h_5m3m"] is False
    assert rules["relaxed_4h1h_5m"] is True
    assert out["gate_inputs"]["tf_3m_aligned"] is False
    assert out["gate_inputs"]["tf_5m_aligned"] is True


def test_summary_separates_provenance_and_reports_counterfactual_net():
    rows = [
        _trade("a", net=10.0),
        _trade("b", net=-8.0, states={"1d":"bullish","4h":"bullish","1h":"bullish","5m":"bullish","3m":"bearish"}),
        _trade("m", net=-3.0, gpt="unknown", manual=True),
        _trade("c", net=-6.0, group="candidate_c", gpt="unknown"),
    ]
    shadows = {r["trade_id"]: _entry_shadow(r["trade_id"]) for r in rows}
    summary = shadow.summarize_rows(rows, shadows)
    assert summary["cohorts"]["auto_core"]["count"] == 2
    assert summary["cohorts"]["manual_core"]["count"] == 1
    assert summary["cohorts"]["candidate_c_rule"]["count"] == 1
    strict = summary["rules"]["strict_4h1h_5m3m"]
    assert strict["passed_count"] == 1 and strict["blocked_count"] == 1
    assert strict["filtered_actual_net"] == 10.0
    assert strict["net_improvement_if_blocked"] == 8.0


def test_summary_is_idempotent_and_sample_target_requires_twenty_auto_core_trades():
    rows = [_trade(f"t{i}", net=1.0) for i in range(3)]
    shadows = {r["trade_id"]: _entry_shadow(r["trade_id"]) for r in rows}
    first = shadow.summarize_rows(rows, shadows)
    second = shadow.summarize_rows(rows, shadows)
    assert first == second
    assert first["sample_status"] == "insufficient"
    assert first["sample_target_min"] == 20


def test_analysis_report_renders_postdeploy_entry_quality_shadow():
    snap = {
        "release":"r","period":"all","coverage":{},"overall":{},"symbols":[],"sides":[],
        "top_positive":[],"top_negative":[],"tf":[],"confidence":[],"self_learning":{},
        "review":{},"settings":{},"positions":[],"system_issues":{},
        "entry_quality_shadow": {
            "mode":"shadow_only","live_authority":False,"cohort_start_kst":"2026-10-01T14:12:57",
            "sample_target_min":20,"sample_status":"insufficient",
            "cohorts":{"auto_core":{"count":3,"actual_net":-4.0,"profit_factor":0.5},
                       "manual_core":{"count":1,"actual_net":-1.0,"profit_factor":None},
                       "candidate_c_rule":{"count":1,"actual_net":2.0,"profit_factor":None}},
            "rules":{"strict_4h1h_5m3m":{"passed_count":1,"blocked_count":2,"unresolved_count":0,
                     "filtered_actual_net":2.0,"net_improvement_if_blocked":6.0,"profit_factor":None}},
        },
    }
    text = analysis_report.build_report(snap)["text"]
    assert "[24. Post-deploy Entry Quality Shadow]" in text
    assert "실전 영향 없음" in text
    assert "strict_4h1h_5m3m" in text


def test_web_report_snapshot_wires_entry_quality_shadow():
    from pathlib import Path
    import web_app
    text = Path(web_app.__file__).read_text(encoding="utf-8")
    assert "import entry_quality_shadow" in text
    assert "'entry_quality_shadow': entry_quality_shadow.summary(ctx.dir)" in text


def test_summary_remaps_entry_shadow_from_entry_id_to_trade_id(monkeypatch):
    row = _trade("joined", net=2.0)
    monkeypatch.setattr(shadow.trade_learning_features, "build_featured_trades", lambda _u: [row])
    monkeypatch.setattr(
        shadow.entry_counterfactual_shadow,
        "_read_latest",
        lambda _u: {"entry-id-1": _entry_shadow("joined", mfe60=0.7, mae60=0.1)},
    )
    out = shadow.summary("/tmp/unused")
    assert out["cohorts"]["auto_core"]["avg_mfe60_r"] == 0.7
    assert out["cohorts"]["auto_core"]["avg_mae60_r"] == 0.1


def test_report_surfaces_unresolved_chase_and_thesis_conditions():
    snap = {
        "release":"r","period":"all","coverage":{},"overall":{},"symbols":[],"sides":[],
        "top_positive":[],"top_negative":[],"tf":[],"confidence":[],"self_learning":{},
        "review":{},"settings":{},"positions":[],"system_issues":{},
        "entry_quality_shadow": {"cohort_start_kst":"2026-10-01T14:12:57","sample_target_min":20,
            "sample_status":"insufficient","cohorts":{},"rules":{},
            "conditions":{"chase_not_extreme":{"passed_count":0,"blocked_count":0,"unresolved_count":3},
                          "thesis_lock_clear":{"passed_count":0,"blocked_count":0,"unresolved_count":3}}},
    }
    text = analysis_report.build_report(snap)["text"]
    assert "chase_not_extreme" in text
    assert "thesis_lock_clear" in text
    assert "미해결 3" in text
