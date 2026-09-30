from pathlib import Path


def test_candidate_c_card_has_manual_close_button_and_endpoint():
    text = Path("templates/dashboard.html").read_text(encoding="utf-8")
    assert "function closeCandidateCSymbol" in text
    assert '"/api/candidate_c_close_symbol"' in text
    assert "이 포지션만 청산 · 재진입" in text
    assert "candidate_manual_close_cooldown" in text
