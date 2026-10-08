from pathlib import Path

HTML = Path(__file__).resolve().parents[1] / "templates" / "dashboard.html"

def test_premium_finish_is_css_only_and_present():
    text = HTML.read_text()
    assert "premium dashboard finish: visual-only override" in text
    assert "radial-gradient(circle at 8% 2%" in text
    assert ".topbar::before" in text
    assert ".account-stat::before" in text
    assert ".engine-live-panel::after" in text

def test_premium_finish_has_consistent_finance_visual_details():
    text = HTML.read_text()
    assert "font-variant-numeric:tabular-nums" in text
    assert "tbody tr:nth-child(even) td" in text
    assert "tbody tr:hover td" in text
    assert ".setting-chip.good" in text
    assert ".pos-card:not(.disabled):hover" in text

def test_mobile_navigation_remains_compact():
    text = HTML.read_text()
    assert ".dashboard-nav { overflow-x:auto; flex-wrap:nowrap; }" in text
    assert ".dashboard-nav a { white-space:nowrap; }" in text
