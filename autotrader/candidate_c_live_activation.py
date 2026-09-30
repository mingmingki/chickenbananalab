"""Candidate C 실매매 최종 사용자 승인 + 홀드아웃 위험 확인 영속 저장소
(2026-09-14, v9 핸드오프 CLAUDE_INSTRUCTION.md 항목5).

candidate_c_runtime.live_activation_blockers()의 3개 고정 상수 중 둘
('live_activation_not_validated', 'observed_holdout_negative')을 무조건
삭제하거나 자동 통과시키지 않고, "인증된 사용자가 화면에서 실제 대상/한도/
검증 상태를 보고 직접 확인했다"는 사실 자체를 서버에 영속 기록해 그 기록이
있을 때만 조건부로 제거하기 위한 저장소다.

절대 원칙(이 모듈이 스스로 깨면 안 됨):
- Claude/서버 코드가 이 승인 기록을 대신 만들거나 자동으로 채우지 않는다 -
  오직 실제 로그인된 사용자의 명시적 POST 요청(candidate_c_start_api.py류
  라우트)만 record_*()를 호출한다.
- 저장 파일이 손상되면 fail-closed로 "미승인"으로 취급한다(과거에 승인이
  있었을 수도 있다는 이유로 통과시키지 않는다).
- 승인은 그 순간의 종목/한도(state_fingerprint) 또는 그 순간의 홀드아웃
  결과(result_fingerprint)에 묶인다 - 나중에 설정이나 증거가 달라지면 이전
  승인은 자동으로 무효가 되어 재확인을 요구한다(조용히 새 상태까지 계속
  덮어주지 않는다).
- 홀드아웃 확인은 성과 숫자 자체를 절대 바꾸지 않는다 - "이 위험을 안다"는
  기록만 남긴다.
"""
from __future__ import annotations

import hashlib
import json
import os
import time

import process_lock


def _path(user_dir: str) -> str:
    return os.path.join(user_dir, "candidate_c_live_activation_state.json")


def _state_fingerprint(cfg) -> str:
    """활성화 승인이 유효한 '그 순간의 실제 조건'을 하나의 값으로 묶는다 -
    대상 계정 심볼이나 사이징 방식/거래당 위험/한도(증거금/레버리지/주문상한/동시포지션/일일손실한도×2)
    중 하나라도 승인 이후 바뀌면 이 값이 달라져 이전 승인이 자동으로
    무효화된다."""
    payload = {
        "symbols": sorted(getattr(cfg, "CANDIDATE_C_SYMBOLS", None) or []),
        "sizing_mode": getattr(cfg, "CANDIDATE_C_SIZING_MODE", "FIXED_MARGIN"),
        "risk_per_trade_pct": getattr(cfg, "CANDIDATE_C_RISK_PER_TRADE_PCT", 1.0),
        "fixed_margin_usdt": getattr(cfg, "CANDIDATE_C_FIXED_MARGIN_USDT", None),
        "leverage": getattr(cfg, "CANDIDATE_C_LEVERAGE", None),
        "max_order_notional_usdt": getattr(cfg, "CANDIDATE_C_MAX_ORDER_NOTIONAL_USDT", None),
        "max_concurrent_positions": getattr(cfg, "CANDIDATE_C_MAX_CONCURRENT_POSITIONS", None),
        "max_daily_loss_pct": getattr(cfg, "CANDIDATE_C_MAX_DAILY_LOSS_PCT", None),
        "account_max_daily_loss_pct": getattr(cfg, "ACCOUNT_HARD_DAILY_LOSS_PCT", None),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _load(user_dir: str) -> dict:
    try:
        state = process_lock.load_json_or_default(_path(user_dir), dict, corrupted_error_prefix="candidate_c_live_activation_state")
    except RuntimeError:
        # fail-closed: 손상된 승인 기록은 "승인 없음"과 동일하게 취급한다(과거에
        # 있었을 수도 있는 승인을 신뢰해 통과시키지 않는다) - 단, 호출부가 원인을
        # 알 수 있게 별도 플래그를 남긴다.
        return {"_corrupted": True}
    return state if isinstance(state, dict) else {}


def _save(user_dir: str, state: dict) -> None:
    process_lock.save_json_atomic(_path(user_dir), state)


def activation_state_fingerprint(cfg) -> str:
    return _state_fingerprint(cfg)


def record_activation_approval(user_dir: str, cfg, approved_by: str) -> dict:
    """실매매 시작 최종 승인을 기록한다 - 실제 로그인 사용자의 명시적 요청에서만
    호출돼야 한다(Claude가 대신 호출하지 않음)."""
    state = _load(user_dir)
    state.pop("_corrupted", None)
    record = {
        "state_fingerprint": _state_fingerprint(cfg),
        "symbols": sorted(getattr(cfg, "CANDIDATE_C_SYMBOLS", None) or []),
        "approved_by": approved_by,
        "approved_at": time.time(),
    }
    state["activation_approval"] = record
    _save(user_dir, state)
    return record


def clear_activation_approval(user_dir: str) -> None:
    state = _load(user_dir)
    state.pop("_corrupted", None)
    state.pop("activation_approval", None)
    _save(user_dir, state)


def activation_approval_status(user_dir: str, cfg) -> dict:
    """{'approved': bool, 'approval': dict|None, 'reason': str|None} - 화면이
    "언제·누가·어떤 조건에" 승인했는지, 또는 왜 지금은 무효인지 보여줄 수
    있게 raw 기록도 함께 돌려준다."""
    state = _load(user_dir)
    if state.get("_corrupted"):
        return {"approved": False, "approval": None, "reason": "approval_record_corrupted"}
    approval = state.get("activation_approval")
    if not approval:
        return {"approved": False, "approval": None, "reason": "not_yet_approved"}
    current = _state_fingerprint(cfg)
    if approval.get("state_fingerprint") != current:
        return {"approved": False, "approval": approval, "reason": "approval_stale_settings_changed"}
    return {"approved": True, "approval": approval, "reason": None}


def record_holdout_risk_ack(user_dir: str, result_fingerprint: str, acknowledged_by: str) -> dict:
    state = _load(user_dir)
    state.pop("_corrupted", None)
    record = {
        "result_fingerprint": result_fingerprint,
        "acknowledged_by": acknowledged_by,
        "acknowledged_at": time.time(),
    }
    state["holdout_risk_ack"] = record
    _save(user_dir, state)
    return record


def holdout_risk_ack_status(user_dir: str, current_result_fingerprint: str) -> dict:
    state = _load(user_dir)
    if state.get("_corrupted"):
        return {"acknowledged": False, "ack": None, "reason": "ack_record_corrupted"}
    ack = state.get("holdout_risk_ack")
    if not ack:
        return {"acknowledged": False, "ack": None, "reason": "not_yet_acknowledged"}
    if ack.get("result_fingerprint") != current_result_fingerprint:
        return {"acknowledged": False, "ack": ack, "reason": "ack_stale_result_changed"}
    return {"acknowledged": True, "ack": ack, "reason": None}
