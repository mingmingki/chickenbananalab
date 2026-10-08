from pathlib import Path
from types import SimpleNamespace

import candidate_c_hybrid_cycle as cc_cycle
import core_short_level


def test_candidate_manual_all_disables_adaptive_decision_context():
    cfg = SimpleNamespace(
        CANDIDATE_C_ORDER_MODE='MANUAL_ALL',
        CANDIDATE_C_SIZING_MODE='FIXED_MARGIN',
        ADAPTIVE_EXIT_MODE='LIVE_BOUNDED',
        CANDIDATE_C_FIXED_MARGIN_USDT=500.0,
        CANDIDATE_C_LEVERAGE=5,
        CANDIDATE_C_MAX_ORDER_NOTIONAL_USDT=5000.0,
        ADAPTIVE_EXIT_APPROVED_POLICY_HASH='approved',
        user_dir='/tmp/noop',
    )
    result = cc_cycle._adaptive_decision_context_kwargs(cfg)
    assert result['adaptive_exit_mode'] == 'OFF'
    assert result['sizing_mode'] == 'FIXED_MARGIN'


def test_core_fixed_margin_is_not_reduced_by_short_attack_level():
    for level in ('EARLY','TACTICAL','STRONG','FULL_BEARISH'):
        assert core_short_level.compute_margin('fixed', 500.0, 500.0, level) == 500.0


def test_core_settings_removes_verbose_legacy_help_text():
    html = Path('templates/dashboard.html').read_text(encoding='utf-8')
    forbidden = (
        '(0~1, 이 값 미만이면 AI의 진입/청산을 무시)',
        '(진입 후 이 시간 동안 AI 판단에 의한 청산·반대 전환을 제한합니다. 손절·익절은 별도 작동합니다.)',
        'approve_now만 신규 주문 허용',
        'Gemini가 보유 근거를 재검토하고 GPT가 최종 관리 행동을 판단합니다.',
        'core-fast-reduce-status',
    )
    for text in forbidden:
        assert text not in html
