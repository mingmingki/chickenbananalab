from pathlib import Path

import candidate_c_trader_adapter as adapter
import log_readability


def test_reason_text_no_setup_is_human_readable():
    assert adapter._candidate_c_monitor_reason_text("no_setup") == "Donchian setup 대기"


def test_monitor_history_deduplicates_same_confirmed_bar(monkeypatch):
    monkeypatch.setattr(adapter.runtime, "snapshot", lambda *_: {
        "monitor_history": [
            {"last_closed_bar_ms": 100, "stage_text": "old"},
            {"last_closed_bar_ms": 200, "stage_text": "replace"},
        ]
    })
    rows = adapter._candidate_c_monitor_history(
        "/tmp/u", "DOGE/USDT:USDT",
        {"last_closed_bar_ms": 200, "stage_text": "new"}, limit=12,
    )
    assert [r["last_closed_bar_ms"] for r in rows] == [100, 200]
    assert rows[-1]["stage_text"] == "new"


def test_readable_log_keeps_candidate_monitor_message():
    raw = (
        "20:30:00 [INFO] [DOGE/USDT:USDT] Candidate C LIVE 감시 · 5분 확정봉 · "
        "4H=LONG · Donchian 돌파 대기 · 판단가=0.0921 · 진입선=0.09272 · "
        "거리=0.67% · 사유=Donchian setup 대기 · 차단=없음"
    )
    text, _ = log_readability.format_line(raw)
    assert "Candidate C LIVE 감시" in text
    assert "4H=LONG" in text
    assert "원문 보기" not in text


def test_monitor_telemetry_exposes_direction_target_and_distance(monkeypatch):
    from types import SimpleNamespace
    snap = SimpleNamespace(
        bars_10m_prior_20=[{"high": 102.0, "low": 98.0}] * 20,
        bar_10m_current={"close": 100.0},
    )
    monkeypatch.setattr(adapter.tfc, "build_as_of_snapshot", lambda *a, **k: snap)
    monkeypatch.setattr(adapter.tfc, "donchian_setup_condition", lambda *a, **k: False)
    monkeypatch.setattr(adapter.dec, "direction_from_4h_indicators", lambda **k: "LONG")
    monitor = adapter._candidate_c_monitor_telemetry(
        "DOGE/USDT:USDT",
        bars_4h=[{"close_time_ms": 1_000}],
        bars_1h=[{"close_time_ms": 1_000}],
        bars_5m=[{"open_time_ms": 500, "close_time_ms": 1_000, "close": 100.0}],
        indicator_fn=lambda bars, donchian_n: [{"ema_20": 90.0, "ema_50": 80.0, "close": 100.0}],
        result={"intent_kind": "NoAction", "reason_code": "no_setup"},
        blockers=[],
        live_execute=True,
    )
    assert monitor["mode"] == "LIVE"
    assert monitor["direction_4h"] == "LONG"
    assert monitor["target_price"] == 102.0
    assert round(monitor["distance_pct"], 2) == 2.0
    assert monitor["stage_text"] == "Donchian 돌파 대기"
    assert monitor["next_5m_close_ms"] == 301_000


def test_dashboard_contains_live_heartbeat_and_progress_ui():
    text = (Path(__file__).resolve().parents[1] / "templates/dashboard.html").read_text()
    assert "실매매 감시 정상" in text
    assert "마지막 체크" in text
    assert "다음 5분 확정봉" in text
    assert "진입선까지" in text
    assert "최근 5분 감시 이력" in text
    assert "(!known || !st.runtime?.running)" in text


def test_source_logs_monitor_once_after_new_bar_publish():
    text = Path(adapter.__file__).read_text()
    assert "_log_candidate_c_monitor(logger_, symbol, monitor)" in text
    assert "새 확정 5분봉을 실제로 처리한 직후 딱 한 번만" in text


def test_dashboard_candidate_monitor_is_position_aware():
    text = (Path(__file__).resolve().parents[1] / "templates/dashboard.html").read_text()
    assert "const ccHoldingStage = st.actual_position" in text
    assert "ccHoldingStage || mon.stage_text" in text


def test_dashboard_contains_separate_breakout_failure_shadow_card():
    text = (Path(__file__).resolve().parents[1] / "templates/dashboard.html").read_text()
    assert 'id="cc-breakout-shadow-card"' in text
    assert '관찰용 · 실주문 영향 없음' in text
    assert 'cc-breakout-shadow-summary' in text
