from pathlib import Path

HTML = Path(__file__).resolve().parents[1] / "templates" / "dashboard.html"


def test_core_and_candidate_use_same_engine_layout():
    text = HTML.read_text()
    assert '<div class="panel engine-live-panel" id="candidate-c-panel">' in text
    assert '<div class="panel engine-live-panel" id="core-live">' in text
    assert text.count('class="engine-head"') >= 2
    assert text.count('class="engine-controls"') >= 2
    assert text.count('class="engine-summary') >= 2


def test_candidate_sequence_matches_core_sequence():
    text = HTML.read_text()
    assert text.index('id="candidate-c-panel"') < text.index('id="cc-positions"') < text.index('id="cc-settings-details"')
    assert text.index('id="core-live"') < text.index('id="core-positions"') < text.index('id="core-settings-panel"')


def test_candidate_settings_are_grouped_by_function():
    text = HTML.read_text()
    for label in ("실행 설정", "주문 계산 방식", "위험 한도"):
        assert f"<h3>{label}</h3>" in text
    for field in (
        "cc-mode", "cc-symbol-doge", "cc-symbol-sol", "cc-gpt-entry-gate",
        "cc-margin", "cc-leverage", "cc-notional",
        "cc-max-positions", "cc-group-loss", "cc-account-loss",
    ):
        assert f'id="{field}"' in text


def test_long_explanations_are_collapsed_or_compacted():
    text = HTML.read_text()
    assert "집계 기준 보기" in text
    assert "포지션 크기 계산 기준" not in text
    assert "운영 설명" in text
    assert "2026-08-30 FAST 서브시스템 제거" not in text
    assert "포지션을 더 오래 들고 있게 되어 왕복 매매 횟수" not in text


def test_runtime_summaries_use_chips_not_long_runon_lines():
    text = HTML.read_text()
    assert 'id="cc-settings"' in text
    assert 'class="setting-chip"' in text
    assert 'const coreModeChip' in text
    assert "SHORT 레벨 자동배분" not in text
    assert "Live 전환 차단 사유: 관측된 항목 없음" not in text


def test_core_reentry_thesis_diagnostics_are_rendered():
    text = HTML.read_text()
    assert "ai_close_thesis_not_recovered" in text
    assert "reentry_thesis_status" in text
    assert "reentry_thesis_1h_pass" in text
    assert "reentry_thesis_5m_count" in text
