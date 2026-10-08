from pathlib import Path


def test_candidate_c_action_reason_is_not_mislabeled_as_gpt():
    text = Path("templates/dashboard.html").read_text(encoding="utf-8")
    card = text[text.index("async function refreshCandidateC"):text.index("async function startTrading")]
    assert 'GPT: ${ccEscape(st.gpt_gate_reason)}' not in card
    assert '처리 사유: ${ccEscape(ccActionReasonText(st.gpt_gate_reason))}' in card


def test_stop_update_reason_has_human_readable_label():
    text = Path("templates/dashboard.html").read_text(encoding="utf-8")
    assert 'stop_update_would_loosen_skipped: "손절폭 확대 방지 — 기존 SL 유지"' in text
    assert 'return CC_ACTION_REASON_LABELS[raw]' in text
