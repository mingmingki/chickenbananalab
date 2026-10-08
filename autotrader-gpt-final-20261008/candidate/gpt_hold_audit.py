"""GPT Hold Audit(Shadow 전용 실험) 기록.

Gemini가 hold라고 판단하면 GPT 신규진입 게이트(openai_analyzer.verify)가 아예 호출되지
않는다 - 그래서 "실제로는 좋은 진입 기회였는데 Gemini가 보수적으로 hold를 낸" 경우를
지금 구조로는 관측할 수 없다. Hold Audit은 그 중 "기회일 가능성이 높은 hold"만 로컬
조건(trader._hold_audit_candidate)으로 좁혀서, GPT에게 Gemini의 판단을 전혀 보여주지
않고 독립적으로 재검토시킨 결과를 기록한다.

이 로그는 어떤 경우에도 실거래에 연결되지 않는다 - 별도 파일(gpt_shadow_log와 다름)로
분리해서, 기존 GPT 신규진입 게이트/Shadow Mode의 agree_rate 등 통계와 절대 섞이지 않게
한다. actual_order 필드는 이 모듈이 기록하는 모든 행에서 항상 False다.
"""

import datetime
import json
import os

import jsonl_cache


def _log_path(user_dir: str) -> str:
    return os.path.join(user_dir, "gpt_hold_audit_log.jsonl")


def record_audit(
    user_dir: str,
    symbol: str,
    market_regime: str | None,
    regime_confidence: float | None,
    gpt_action: str | None,
    gpt_confidence: float | None,
    reasoning: str,
    reference_price: float | None,
    audit_id: str,
    error_reason: str | None = None,
) -> None:
    """gpt_action이 None이면(API 오류/timeout/파싱 실패 등 openai_analyzer.verify_hold_audit가
    실패를 반환한 경우) "실패"로 기록된다 - summary()의 error_count에 잡힌다.
    error_reason: gpt_action이 None일 때만 의미 있음 - "timeout"/"api_error"/
    "parse_error"/"sdk_error"/"unknown" 중 하나(openai_analyzer의 canonical 값을 그대로
    저장, 내부 예외 문자열/응답 전문은 저장하지 않는다). Hold Audit은 항상 Shadow
    전용이라 이 값이 무엇이든 실거래에는 영향이 없다.
    reference_price: Audit 시점 가격 - 이후 5/15/30/60분 뒤 가격과 비교해서 "GPT가
    맞았으면 실제로 얼마나 수익이었을지" 사후 분석하기 위해 남긴다."""
    os.makedirs(user_dir, exist_ok=True)
    with open(_log_path(user_dir), "a", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {
                    "mode": "hold_audit",
                    "symbol": symbol,
                    "market_regime": market_regime,
                    "regime_confidence": regime_confidence,
                    "gpt_action": gpt_action,
                    "gpt_confidence": gpt_confidence,
                    "reasoning": reasoning,
                    "reference_price": reference_price,
                    "audit_id": audit_id,
                    "actual_order": False,
                    "error_reason": error_reason,
                    "pipeline_context": "hold_audit",
                    "time": datetime.datetime.now().isoformat(timespec="seconds"),
                },
                ensure_ascii=False,
            )
            + "\n"
        )


def _load_all(user_dir: str) -> list:
    return jsonl_cache.load_jsonl_cached(_log_path(user_dir))


def recent(user_dir: str, limit: int = 100) -> list:
    """최신순으로 최근 Audit 기록을 반환한다."""
    return list(reversed(_load_all(user_dir)))[:limit]


def last_audit_time(user_dir: str, symbol: str) -> str | None:
    """서버 재시작 후 Hold Audit 쿨다운을 복구할 때 쓰는, 이 심볼의 마지막 Audit
    시각(성공/오류 무관 - 실패한 호출도 방금 API를 한 번 썼으므로 쿨다운 대상이다)."""
    last = None
    for rec in _load_all(user_dir):
        if rec.get("symbol") == symbol:
            last = rec.get("time")
    return last


def summary(user_dir: str) -> dict:
    """long/short/no_entry/error(호출 실패) 건수를 요약한다. 이 통계는 순수 관찰용이며
    어떤 게이트에도 쓰이지 않는다.

    error_count는 기존과 동일하게 유지하되(하위 호환), 원인별 세부 카운트를 추가한다.
    error_reason 필드 자체가 없는 행(이 필드가 생기기 전에 기록된 과거 행)은
    timeout 등으로 추정하지 않고 legacy_no_verdict_count로 따로 센다."""
    records = _load_all(user_dir)
    total = len(records)
    long_count = sum(1 for r in records if r.get("gpt_action") == "long")
    short_count = sum(1 for r in records if r.get("gpt_action") == "short")
    no_entry_count = sum(1 for r in records if r.get("gpt_action") == "no_entry")
    error_count = total - long_count - short_count - no_entry_count

    error_records = [r for r in records if r.get("gpt_action") not in ("long", "short", "no_entry")]
    timeout_count = sum(1 for r in error_records if r.get("error_reason") == "timeout")
    api_error_count = sum(1 for r in error_records if r.get("error_reason") == "api_error")
    parse_error_count = sum(1 for r in error_records if r.get("error_reason") == "parse_error")
    sdk_error_count = sum(1 for r in error_records if r.get("error_reason") == "sdk_error")
    unknown_count = sum(1 for r in error_records if r.get("error_reason") == "unknown")
    legacy_no_verdict_count = sum(1 for r in error_records if "error_reason" not in r)

    return {
        "count": total,
        "long_count": long_count,
        "short_count": short_count,
        "no_entry_count": no_entry_count,
        "error_count": error_count,
        "timeout_count": timeout_count,
        "api_error_count": api_error_count,
        "parse_error_count": parse_error_count,
        "sdk_error_count": sdk_error_count,
        "unknown_count": unknown_count,
        "legacy_no_verdict_count": legacy_no_verdict_count,
    }
