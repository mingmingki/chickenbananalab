from pathlib import Path

def test_dashboard_has_daily_completion_and_promotion_explanation_surfaces():
    html=Path("templates/dashboard.html").read_text(encoding="utf-8")
    assert "매일 자동매매 점검" in html
    assert "daily-completion-body" in html
    assert "승격 대기 사유" in html

def test_web_api_exposes_daily_completion_endpoint():
    src=Path("web_app.py").read_text(encoding="utf-8")
    assert "/api/analysis/daily-completion" in src
    assert "promotion_progress" in src
