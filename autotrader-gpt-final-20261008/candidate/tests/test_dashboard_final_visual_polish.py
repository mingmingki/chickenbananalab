from pathlib import Path

HTML = Path(__file__).resolve().parents[1] / "templates" / "dashboard.html"


def test_final_visual_polish_defines_consistent_type_scale():
    text = HTML.read_text()
    assert "2026-09-22 final visual polish" in text
    assert "font-size:13px;" in text
    assert ".account-stat-value" in text
    assert "font-size:17px;" in text
    assert "table { font-size:11.5px;" in text


def test_final_visual_polish_keeps_compact_mobile_layout():
    text = HTML.read_text()
    assert "@media (max-width:720px)" in text
    assert ".account-stat-value { font-size:16px; }" in text
    assert ".panel h2,.engine-title h2 { font-size:14.5px; }" in text


def test_pnl_summary_numeric_columns_are_right_aligned():
    text = HTML.read_text()
    assert ".pnl-summary-table th:not(:first-child)" in text
    assert ".pnl-summary-table td:not(:first-child) { text-align:right; }" in text
