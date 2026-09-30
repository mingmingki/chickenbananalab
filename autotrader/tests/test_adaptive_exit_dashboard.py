from pathlib import Path

import adaptive_exit_log
import web_app


def test_absent_adaptive_records_return_no_data(tmp_path):
    state=web_app._adaptive_exit_state_for_api(tmp_path,["BTC/USDT:USDT","DOGE/USDT:USDT"])
    assert state["BTC/USDT:USDT"]["status"] == "NO_DATA"
    assert state["DOGE/USDT:USDT"]["status"] == "NO_DATA"


def test_latest_record_shape_is_read_only_and_malformed_tail_is_contained(tmp_path):
    record={
        "symbol":"BTC/USDT:USDT","decision_timestamp":123,"policy_hash":"p",
        "input_snapshot_hash":"s","mode":"ADVISORY","plan_hash":"plan",
        "configured_margin_usdt":500.0,"effective_margin_usdt":120.0,
        "planned_loss_usdt":30.0,"stop_price":95.0,"tp1_price":110.0,"tp2_price":120.0,
        "runner_fraction":0.5,"reason_code":"<img src=x onerror=alert(1)>",
        "diagnostics":[["gemini_thesis","weakening"],["learning_state","SHADOW"]],
    }
    adaptive_exit_log.append_plan(tmp_path,record)
    with (Path(tmp_path)/"adaptive_exit_plans.jsonl").open("a",encoding="utf-8") as f:
        f.write("{broken\n")
    state=web_app._adaptive_exit_state_for_api(tmp_path,["BTC/USDT:USDT"])["BTC/USDT:USDT"]
    assert state["status"] == "ADVISORY"
    assert state["configured_margin_usdt"] == 500.0
    assert state["effective_margin_usdt"] == 120.0
    assert state["reason_code"].startswith("<img")
    assert state["audit_corrupt_lines"] == 1


def test_dashboard_uses_textcontent_and_labels_advisory():
    html=Path("templates/dashboard.html").read_text(encoding="utf-8")
    assert 'id="adaptive-exit-summary"' in html
    assert 'adaptiveExitEl.textContent' in html
    assert 'Adaptive Exit' in html
    assert 'ADVISORY' in html or 'Advisory' in html
    assert 'adaptiveExitEl.innerHTML' not in html
