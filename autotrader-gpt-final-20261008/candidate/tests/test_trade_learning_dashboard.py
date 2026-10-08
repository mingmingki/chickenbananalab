from pathlib import Path
HTML=Path(__file__).resolve().parents[1]/'templates'/'dashboard.html'

def test_analysis_button_and_modal_tabs_exist():
    text=HTML.read_text()
    assert 'id="trade-learning-analysis-btn"' in text
    for label in ('전체 요약','TF 분석','조건 분석','개별 거래','AI 학습','6시간 리뷰'):
        assert label in text

def test_analysis_ui_states_advisory_only_and_sample_tiers():
    text=HTML.read_text()
    assert '관찰된 연관성이지 인과관계가 아닙니다' in text
    assert '실매매 설정 자동 변경 없음' in text
    assert 'exploratory' in text and 'established_sample' in text
    assert '자동 적용' not in text
