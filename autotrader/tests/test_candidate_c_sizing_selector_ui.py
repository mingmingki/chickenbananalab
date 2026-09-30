from pathlib import Path

DASHBOARD = (Path(__file__).resolve().parents[1] / "templates/dashboard.html").read_text(encoding="utf-8")


def test_candidate_c_exposes_three_explicit_order_modes():
    assert 'name="cc-order-mode"' in DASHBOARD
    assert 'value="AUTO_ALL"' in DASHBOARD
    assert 'value="FIXED_MARGIN_AUTO_EXIT"' in DASHBOARD
    assert 'value="MANUAL_ALL"' in DASHBOARD
    assert '전체 자동계산' in DASHBOARD
    assert '고정 증거금 + SL/TP 자동계산' in DASHBOARD
    assert '모두 직접 지정' in DASHBOARD


def test_candidate_c_order_mode_controls_fields_and_save_payload():
    assert 'function getCandidateCOrderMode()' in DASHBOARD
    assert 'function syncCandidateCOrderModeUi()' in DASHBOARD
    assert 'id="cc-risk-per-trade-row"' in DASHBOARD
    assert 'id="cc-fixed-margin-row"' in DASHBOARD
    assert 'id="cc-manual-sl-row"' in DASHBOARD
    assert 'id="cc-manual-tp-row"' in DASHBOARD
    assert 'order_mode: getCandidateCOrderMode()' in DASHBOARD
