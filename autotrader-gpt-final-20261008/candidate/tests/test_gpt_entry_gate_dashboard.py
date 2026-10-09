import json
from pathlib import Path

import gpt_shadow_log


def _append(user_dir, **row):
    path = Path(user_dir) / "gpt_shadow_log.jsonl"
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(dict(symbol="BTC/USDT:USDT", **row), ensure_ascii=False) + "\n")


def test_recent_by_mode_returns_only_entry_gate_newest_first(tmp_path):
    _append(tmp_path, time="1", mode="shadow", decision_id="s", gpt_decision="approve_now")
    _append(tmp_path, time="2", mode="entry_gate", decision_id="g1",
            gpt_decision="wait", gate_result="blocked_wait")
    _append(tmp_path, time="3", mode="shadow", decision_id="s2", gpt_decision="reject")
    _append(tmp_path, time="4", mode="entry_gate", decision_id="g2",
            gpt_decision="approve_now", gate_result="approved")
    rows = gpt_shadow_log.recent_by_mode(str(tmp_path), "entry_gate", limit=10)
    assert [r["time"] for r in rows] == ["4", "2"]
    assert all(r["mode"] == "entry_gate" for r in rows)


def test_summary_exposes_separate_mode_counts_and_gate_buckets(tmp_path):
    _append(tmp_path, mode="shadow", decision_id="s1", gpt_decision="approve_now")
    _append(tmp_path, mode="shadow", decision_id="s2", gpt_decision="wait")
    _append(tmp_path, mode="entry_gate", decision_id="g1",
            gpt_decision="approve_now", gate_result="approved")
    _append(tmp_path, mode="entry_gate", decision_id="g2",
            gpt_decision="reject", gate_result="blocked_reject")
    _append(tmp_path, mode=None, decision_id=None, gpt_decision=None)
    s = gpt_shadow_log.summary(str(tmp_path))
    assert s["shadow_mode_count"] == 2
    assert s["entry_gate_mode_count"] == 2
    assert s["no_mode_count"] == 1
    assert s["gate_count"] == 2
    assert s["gate_approved_count"] == 1
    assert s["gate_blocked_reject_count"] == 1


def test_dashboard_labels_entry_gate_not_shadow():
    text = (Path(__file__).resolve().parents[1] / "templates/dashboard.html").read_text()
    assert "<h2>CORE GPT 진입 게이트</h2>" in text
    assert "<h2>GPT 검증 (Shadow Mode)</h2>" not in text
    assert "GPT 승인 결과를 진입 게이트에 반영합니다." in text
    assert '----- CORE GPT 진입 게이트 -----' in text
