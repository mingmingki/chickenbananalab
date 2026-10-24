from pathlib import Path

HTML = Path("templates/dashboard.html").read_text(encoding="utf-8")

def test_dashboard_fetches_operating_cost_api():
    assert "fetchWithTimeout('/api/operating-costs')" in HTML

def test_dashboard_renders_all_operating_cost_fields():
    for field in ("ai-24h-cost","ai-monthly-projection","server-monthly-projection","ops-monthly-projection","operating-cost-assumptions"):
        assert f'getElementById("{field}")' in HTML or f"getElementById('{field}')" in HTML

def test_operating_cost_panel_uses_backend_summary_not_hardcoded_values():
    assert "projected_monthly_ai_cost_usd" in HTML
    assert "monthly_estimate_usd" in HTML
    assert "rolling_24h_cost_usd" in HTML
