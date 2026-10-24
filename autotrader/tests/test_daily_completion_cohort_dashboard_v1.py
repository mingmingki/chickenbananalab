from pathlib import Path

def test_daily_completion_api_includes_post_release_cohort_and_coverage_reasons():
    src=Path("web_app.py").read_text(encoding="utf-8")
    assert "post_release_cohort" in src
    assert "feature_missing_reasons" in src
    assert "release_cohort_analysis" in src

def test_dashboard_surfaces_post_release_economic_kpi_and_missing_reasons():
    html=Path("templates/dashboard.html").read_text(encoding="utf-8")
    assert "현 릴리스 cohort" in html
    assert "누락 원인" in html
    assert "비용후 Net" in html
