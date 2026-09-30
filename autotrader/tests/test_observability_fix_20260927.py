from pathlib import Path
from types import SimpleNamespace
import web_app


def test_candidate_legacy_sl_only_requires_both_missing_target_and_adaptive_hash():
    assert hasattr(web_app, "_candidate_c_legacy_sl_only")
    f = web_app._candidate_c_legacy_sl_only
    assert f(SimpleNamespace(target_price=None, adaptive_exit_policy_hash=None)) is True
    assert f(SimpleNamespace(target_price=130.0, adaptive_exit_policy_hash=None)) is False
    assert f(SimpleNamespace(target_price=None, adaptive_exit_policy_hash="abc")) is False


def test_dashboard_distinguishes_legacy_missing_tp_from_new_missing_tp():
    html = Path("templates/dashboard.html").read_text(encoding="utf-8")
    assert "TP 없음 (기존 포지션 · SL 보호 정상)" in html
    assert "legacy_sl_only" in html
    assert "TP 확인 안 됨" in html


def test_dashboard_separates_gpt_reduce_proposal_from_execution_gate():
    html = Path("templates/dashboard.html").read_text(encoding="utf-8")
    assert "GPT 관리 제안:" in html
    assert "실행 게이트:" in html
    assert "gemini_assessment" in html


def test_core_fixed_mode_summary_states_new_entry_margin_and_notional():
    html = Path("templates/dashboard.html").read_text(encoding="utf-8")
    assert "신규진입 고정 증거금" in html
    assert "예상 명목" in html
