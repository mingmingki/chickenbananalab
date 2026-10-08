from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_long_stop_context_reports_consumed_and_remaining_without_reversing_meaning():
    from position_risk_context import format_position_risk_context
    text = format_position_risk_context(
        {'side':'long','entry_price':100.0,'mark_price':99.0}, {'sl_price':90.0})
    assert '현재가→손절 실제 가격거리 9.09%' in text
    assert '진입→손절 위험거리 소진율 10%' in text
    assert '남은 진입→손절 위험거리 90%' in text
    assert '손절까지 남은 거리의 10%' not in text


def test_short_stop_context_uses_same_directionally_correct_math():
    from position_risk_context import format_position_risk_context
    text = format_position_risk_context(
        {'side':'short','entry_price':100.0,'mark_price':101.0}, {'sl_price':110.0})
    assert '현재가→손절 실제 가격거리 8.91%' in text
    assert '진입→손절 위험거리 소진율 10%' in text
    assert '남은 진입→손절 위험거리 90%' in text


def test_low_price_assets_keep_real_price_precision_in_ai_context():
    from position_risk_context import format_position_risk_context
    text = format_position_risk_context(
        {'side':'long','entry_price':0.092146,'mark_price':0.091660}, {'sl_price':0.085300})
    assert '진입가 0.092146' in text
    assert '현재가 0.09166' in text
    assert '손절가 0.0853' in text
    assert '진입→손절 위험거리 소진율 7%' in text
    assert '남은 진입→손절 위험거리 93%' in text


def test_both_ai_reviewers_use_shared_stop_context():
    assert 'format_position_risk_context' in (ROOT/'gemini_analyzer.py').read_text()
    assert 'format_position_risk_context' in (ROOT/'openai_analyzer.py').read_text()


def test_dashboard_hides_reduction_gate_and_diagnostics_for_hold_advice():
    text=(ROOT/'templates'/'dashboard.html').read_text()
    assert "const showReduction = review?.action && review.action !== 'HOLD';" in text
    assert 'showReduction && coreReductionGateText' in text
    assert 'showReduction ? `<details class="ai-diag-details"' in text
    assert '실제 감축 상태:' in text
    assert '이번 감축 25% step' not in text
