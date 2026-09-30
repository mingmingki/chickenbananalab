"""Candidate C(규칙 기반 4H 추세 + Donchian 돌파 엔진) 전용 GPT Entry Gate 어댑터.

CORE의 openai_analyzer.verify()(검증된 계약, purpose="entry_gate")를 그대로 재사용한다 -
trader._gpt_entry_gate()나 SHORT_LEVEL 등 CORE 전용 구조는 가져오지 않고, 이 모듈이
"규칙 기반 EntryIntent -> GPT가 이해할 수 있는 입력"으로만 변환해서 같은 verify() 계약에
태운다.

계약(사용자 지시, 2026-09-12):
- approve_now만 주문 허용. wait/reject/timeout/api_error/parse_error/sdk_error는 전부
  fail-closed(차단) - "hold"로 뭉뚱그려 기록하지 않고 gate_result/error_reason으로
  원인별로 분리해서 반환한다(호출부가 그대로 로그에 남길 수 있게).
- GPT는 신호를 새로 만들 수 없고 승인/거부만 한다 - 이 함수는 "허용 여부"만 반환하고,
  intent(side/가격/사이즈)는 그대로 호출부에 남아있는 원본을 그대로 쓴다(여기서 절대
  변형하지 않는다).
- GPT 응답을 기다리는 동안(네트워크 왕복 시간) 가격/돌파 조건이 무효화될 수 있으므로,
  approve_now를 받은 뒤에도 is_still_valid_fn()으로 다시 확인하고, 무효화됐으면
  gate_result="blocked_stale"로 별도 차단한다(승인 자체를 취소하는 게 아니라 "그 사이
  상황이 바뀌었다"는 별개 사유)."""
import logging

import candidate_c_gpt_gate_log
import gemini_analyzer
import openai_analyzer
from adaptive_exit_engine import (select_verified_ai_price_plan, build_ai_price_contract, format_ai_price_contract)
from adaptive_exit_policy import production_adaptive_exit_policy

logger = logging.getLogger("trader.candidate_c_gpt_gate")


def _build_exit_price_contract(intent, snapshot: dict):
    try:
        return build_ai_price_contract(
            intent.side, snapshot.get("current_price"), snapshot.get("atr_4h"), production_adaptive_exit_policy(),
        )
    except Exception:
        return None

# CORE의 GPT_ENTRY_TIMEOUT_SECONDS(trader.py)와 동일한 값 - 신규 진입은 시간에 민감해서
# 재시도 없이 짧게 끊는다(entry_gate 용도, Shadow 용도의 더 긴 timeout과는 다르다).
GPT_GATE_TIMEOUT_SECONDS = 15.0
GPT_GATE_MAX_RETRIES = 0


def _build_gemini_shaped_decision(intent) -> dict:
    """intent(EntryIntent)를 openai_analyzer.verify()가 요구하는 gemini_decision 모양으로
    변환한다. 이건 GPT에게 "무엇을 검증해야 하는지" 알려주는 입력일 뿐이고, GPT의 응답으로
    이 값이나 intent 자체를 덮어쓰지 않는다 - side는 항상 intent.side가 최종값이다."""
    return {
        "action": intent.side,
        "confidence": None,
        "exit_plan": None,
        "reasoning": (
            f"규칙 기반 신호(setup_id={intent.setup_id}, reason_code={intent.reason_code}): "
            f"4H 추세 + Donchian 채널 돌파 확정. "
            f"stop={intent.raw_stop_price} target={intent.raw_target_price}"
        ),
    }


def _build_candle_summary(intent, snapshot: dict) -> str:
    """GPT 프롬프트용 요약 - Gemini의 재량적 분석 포맷을 흉내내지 않고, 이 전략이 실제로
    근거로 삼는 값(4H 추세 방향, Donchian 상/하단, 현재가)만 명시적으로 전달한다."""
    return (
        "[Candidate C 규칙 기반 전략 - 100% 결정론적, Gemini 재량 판단 아님]\n"
        f"심볼={snapshot.get('symbol')}\n"
        f"방향={intent.side}\n"
        f"4H 추세 방향={snapshot.get('trend_direction')}\n"
        f"Donchian 상단={snapshot.get('donchian_upper')} 하단={snapshot.get('donchian_lower')}\n"
        f"현재가={snapshot.get('current_price')}\n"
        f"진입 stop={intent.raw_stop_price} target={intent.raw_target_price}\n"
        "이 신호는 4H EMA20/EMA50 추세 + Donchian 채널 돌파 규칙으로 이미 확정된 것이다. "
        "신호 자체를 재해석하거나 방향을 바꾸려 하지 말고, 지금 이 돌파에 실제로 진입할 "
        "만큼 타당한 시점인지만 승인(approve_now) 또는 거부(reject/wait)하라."
    )


def rule_based_entry_without_gpt(cfg, intent, snapshot: dict, *, is_still_valid_fn) -> dict:
    """[2026-09-16, 사용자 직접 지시 - Candidate C 전용 GPT ON/OFF] CANDIDATE_C_
    GPT_ENTRY_GATE_ENABLED=False일 때 verify_candidate_signal() 대신 호출된다.
    GPT를 전혀 부르지 않는다(openai_analyzer.verify 호출 자체가 없음) - "GPT가
    approve했다"고 위장하지 않고, gate_result를 approved와 절대 혼동될 수 없는
    별도 값("rule_based_no_gpt_review")으로 남겨 승인율 통계에 섞이지 않게 한다.
    GPT 왕복이 없으므로 그 시간 동안의 가격 변화 문제 자체가 없지만, 다른 모든
    검사(4H 추세·Donchian·ATR·정책·위험)는 호출부가 decide() 단계에서 이미
    통과시킨 뒤이므로 여기서는 순수하게 "신선한지"만 한 번 더 확인한다 - 실제
    주문 직전 재확인은 _execute_entry() 쪽 계좌 공통락 안에서 그대로 유지된다."""
    logger_ = cfg.logger or logger
    if not is_still_valid_fn():
        logger_.info(
            "[%s] Candidate C 규칙 기반 진입(GPT 미사용) - 확인 중 신호가 무효화되어 주문 취소(blocked_stale)",
            snapshot.get("symbol"),
        )
        result_dict = {
            "allowed": False, "gate_result": "blocked_stale", "error_reason": None,
            "gpt_confidence": None, "gpt_reasoning": "",
        }
    else:
        logger_.info(
            "[%s] Candidate C 규칙 기반 진입(GPT 미사용) - 신규 %s 주문 진행",
            snapshot.get("symbol"), intent.side,
        )
        result_dict = {
            "allowed": True, "gate_result": "rule_based_no_gpt_review", "error_reason": None,
            "gpt_confidence": None,
            "gpt_reasoning": "CANDIDATE_C_GPT_ENTRY_GATE_ENABLED=False - GPT 검토 없이 규칙 기반(4H 추세+Donchian 돌파) 신호로 진입",
        }
    # verify_candidate_signal()과 동일한 관찰용 기록 경로(대시보드 "GPT 승인/차단"
    # 통계) - gate_result가 "approved"/"blocked_*"와 절대 겹치지 않는 고유 값이라
    # 승인율 집계에 미사용 건이 섞이지 않는다. 기록 자체는 순수 관찰용이라 실패해도
    # 매매 판단에 영향 없음(fail-open).
    try:
        candidate_c_gpt_gate_log.record(
            cfg.user_dir, snapshot.get("symbol"), intent.side,
            result_dict["gate_result"], result_dict["error_reason"],
        )
    except Exception:
        logger_.warning("[%s] Candidate C GPT 미사용 게이트 로그 기록 실패", snapshot.get("symbol"), exc_info=True)
    return result_dict


def review_exit_plan_only(cfg, intent, snapshot: dict) -> dict:
    """Review Candidate C SL/TP only; never grant or deny entry admission."""
    logger_ = cfg.logger or logger
    candle_summary = _build_candle_summary(intent, snapshot)
    exit_price_contract = _build_exit_price_contract(intent, snapshot)
    exit_price_contract_text = format_ai_price_contract(exit_price_contract)
    try:
        gemini_shaped = gemini_analyzer.propose_entry_exit_plan(
            cfg, snapshot.get("symbol"), intent.side, snapshot.get("tf_list", []), candle_summary,
            intent.raw_stop_price, intent.raw_target_price,
            exit_price_contract=exit_price_contract_text,
        )
    except Exception as exc:
        logger_.warning(
            "[%s] Candidate C Gemini SL/TP 제안 실패(%s) - Adaptive fallback 후보 유지",
            snapshot.get("symbol"), type(exc).__name__, exc_info=True,
        )
        gemini_shaped = _build_gemini_shaped_decision(intent)

    result = openai_analyzer.verify(
        cfg, snapshot.get("symbol"), snapshot.get("tf_list", []), candle_summary,
        snapshot.get("position"), gemini_shaped,
        timeout=GPT_GATE_TIMEOUT_SECONDS, purpose="exit_plan_only",
        max_retries=GPT_GATE_MAX_RETRIES, exit_price_contract=exit_price_contract,
    )
    if result is None or result.get("decision") is None:
        error_reason = (result or {}).get("error_reason") or "unknown"
        logger_.info(
            "[%s] Candidate C AI_EXIT_PLAN 가격검증 실패(%s) - 진입은 유지, Adaptive fallback",
            snapshot.get("symbol"), error_reason,
        )
        return {
            "ai_exit_plan": None, "ai_exit_source": "gpt_exit_review_error",
            "gemini_exit_plan": gemini_shaped.get("exit_plan"),
            "gpt_exit_plan_decision": (result or {}).get("exit_plan_decision"),
            "gpt_exit_plan": (result or {}).get("exit_plan"),
            "exit_review_error": error_reason,
        }

    selected_ai_exit, ai_exit_source = select_verified_ai_price_plan(gemini_shaped, result)
    logger_.info(
        "[%s] Candidate C AI_EXIT_PLAN 가격전용 검증 source=%s gemini=%s gpt_decision=%s gpt_plan=%s",
        snapshot.get("symbol"), ai_exit_source, gemini_shaped.get("exit_plan"),
        result.get("exit_plan_decision"), result.get("exit_plan"),
    )
    return {
        "ai_exit_plan": selected_ai_exit, "ai_exit_source": ai_exit_source,
        "gemini_exit_plan": gemini_shaped.get("exit_plan"),
        "gpt_exit_plan_decision": result.get("exit_plan_decision"),
        "gpt_exit_plan": result.get("exit_plan"),
        "exit_review_error": None,
    }


def verify_candidate_signal(cfg, intent, snapshot: dict, *, is_still_valid_fn) -> dict:
    """규칙 기반 EntryIntent를 GPT Entry Gate에 통과시킨다.

    반환: {"allowed": bool, "gate_result": "approved"|"blocked_wait"|"blocked_reject"|
    "blocked_error"|"blocked_stale", "error_reason": str|None, "gpt_confidence": float|None,
    "gpt_reasoning": str}

    대시보드의 "GPT 승인/차단/오류 수"용 최소 구조화 로그(2026-09-13, 사용자 지시) -
    이 로그는 순수 관찰용이라 기록 실패가 매매 판단에 영향을 주면 안 된다(fail-open)."""
    result_dict = _verify_candidate_signal_inner(cfg, intent, snapshot, is_still_valid_fn=is_still_valid_fn)
    try:
        candidate_c_gpt_gate_log.record(
            cfg.user_dir, snapshot.get("symbol"), intent.side,
            result_dict["gate_result"], result_dict["error_reason"],
        )
    except Exception:
        (cfg.logger or logger).warning(
            "[%s] Candidate C GPT 게이트 로그 기록 실패", snapshot.get("symbol"), exc_info=True)
    return result_dict


def _verify_candidate_signal_inner(cfg, intent, snapshot: dict, *, is_still_valid_fn) -> dict:
    # 2026-09-15(사용자 직접 지시) - 이 파일은 원래 고정된 모듈 로거("trader.
    # candidate_c_gpt_gate")만 썼다. 그러면 공용 파일 로그(루트 로거에 붙음)엔
    # 남아도, 대시보드 "실행 로그" 패널(계정별 로거 trader.<username>에만 붙는
    # 별도 링버퍼, web_app.py의 LogRingBuffer)에는 절대 안 뜬다 - 형제 로거일 뿐
    # 자식이 아니라 전파가 안 됨. candidate_c_trader_adapter.py가 이미 쓰던
    # cfg.logger 우선 패턴을 그대로 따른다(cfg.logger가 없는 테스트 등에서만
    # 모듈 로거로 폴백).
    logger_ = cfg.logger or logger
    candle_summary = _build_candle_summary(intent, snapshot)
    exit_price_contract = _build_exit_price_contract(intent, snapshot)
    exit_price_contract_text = format_ai_price_contract(exit_price_contract)
    try:
        gemini_shaped = gemini_analyzer.propose_entry_exit_plan(
            cfg, snapshot.get("symbol"), intent.side, snapshot.get("tf_list", []), candle_summary,
            intent.raw_stop_price, intent.raw_target_price,
            exit_price_contract=exit_price_contract_text,
        )
    except Exception as exc:
        logger_.warning(
            "[%s] Candidate C Gemini SL/TP 제안 실패(%s) - GPT가 가격 복구 가능, 기존 Adaptive fallback 유지",
            snapshot.get("symbol"), type(exc).__name__, exc_info=True,
        )
        gemini_shaped = _build_gemini_shaped_decision(intent)

    result = openai_analyzer.verify(
        cfg, snapshot.get("symbol"), snapshot.get("tf_list", []), candle_summary,
        snapshot.get("position"), gemini_shaped,
        timeout=GPT_GATE_TIMEOUT_SECONDS, purpose="entry_gate",
        max_retries=GPT_GATE_MAX_RETRIES, exit_price_contract=exit_price_contract,
    )

    if result is None or result.get("decision") is None:
        error_reason = (result or {}).get("error_reason") or "unknown"
        logger_.info(
            "[%s] Candidate C GPT Entry Gate 실패(원인=%s) - fail-closed, 신규 %s 주문 취소",
            snapshot.get("symbol"), error_reason, intent.side,
        )
        return {
            "allowed": False, "gate_result": "blocked_error", "error_reason": error_reason,
            "gpt_confidence": None, "gpt_reasoning": "",
        }

    decision = result["decision"]
    confidence = result.get("confidence")
    reasoning = result.get("reasoning", "")

    if decision == "wait":
        logger_.info(
            "[%s] Candidate C GPT Entry Gate 대기(wait) - 신규 %s 주문 보류(confidence=%s): %s",
            snapshot.get("symbol"), intent.side, confidence, reasoning,
        )
        return {
            "allowed": False, "gate_result": "blocked_wait", "error_reason": None,
            "gpt_confidence": confidence, "gpt_reasoning": reasoning,
        }
    if decision == "reject":
        logger_.info(
            "[%s] Candidate C GPT Entry Gate 거부(reject) - 신규 %s 주문 취소(confidence=%s): %s",
            snapshot.get("symbol"), intent.side, confidence, reasoning,
        )
        return {
            "allowed": False, "gate_result": "blocked_reject", "error_reason": None,
            "gpt_confidence": confidence, "gpt_reasoning": reasoning,
        }

    # decision == "approve_now"
    if confidence is None:
        logger_.warning(
            "[%s] GPT가 approve_now를 줬지만 confidence 값이 유효하지 않아 fail-closed로 차단",
            snapshot.get("symbol"),
        )
        return {
            "allowed": False, "gate_result": "blocked_error", "error_reason": "parse_error",
            "gpt_confidence": None, "gpt_reasoning": reasoning,
        }

    if not is_still_valid_fn():
        logger_.info(
            "[%s] GPT approve_now 받았으나 대기 중 신호가 무효화되어 주문 취소(blocked_stale)",
            snapshot.get("symbol"),
        )
        return {
            "allowed": False, "gate_result": "blocked_stale", "error_reason": None,
            "gpt_confidence": confidence, "gpt_reasoning": reasoning,
        }

    selected_ai_exit, ai_exit_source = select_verified_ai_price_plan(gemini_shaped, result)
    logger_.info(
        "[%s] Candidate C GPT Entry Gate 승인(approve_now) - 신규 %s 주문 진행(confidence=%s): %s",
        snapshot.get("symbol"), intent.side, confidence, reasoning,
    )
    logger_.info(
        "[%s] Candidate C AI_EXIT_PLAN 검증 source=%s gemini=%s gpt_decision=%s gpt_plan=%s",
        snapshot.get("symbol"), ai_exit_source, gemini_shaped.get("exit_plan"),
        result.get("exit_plan_decision"), result.get("exit_plan"),
    )
    return {
        "allowed": True, "gate_result": "approved", "error_reason": None,
        "gpt_confidence": confidence, "gpt_reasoning": reasoning,
        "ai_exit_plan": selected_ai_exit, "ai_exit_source": ai_exit_source,
        "gemini_exit_plan": gemini_shaped.get("exit_plan"),
        "gpt_exit_plan_decision": result.get("exit_plan_decision"),
    }
