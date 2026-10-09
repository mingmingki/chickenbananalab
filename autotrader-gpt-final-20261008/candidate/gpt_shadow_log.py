import datetime
import json
import os

import jsonl_cache
import notification_policy


def _log_path(user_dir: str) -> str:
    return os.path.join(user_dir, "gpt_shadow_log.jsonl")


def record_verification(
    user_dir: str,
    symbol: str,
    gemini_action: str | None,
    gemini_confidence: float | None,
    gpt_decision: str | None,
    gpt_confidence: float | None,
    gpt_reasoning: str,
    decision_id: str | None = None,
    event_type: str | None = None,
    order_success: bool | None = None,
    mode: str | None = None,
    gate_result: str | None = None,
    error_reason: str | None = None,
    pipeline_context: str | None = None,
    outcome: dict | None = None,
) -> None:
    """decision_id/event_type: 반대방향 전환(reversal)처럼 Gemini 판단 하나에서 청산+진입
    두 번의 실주문(그래서 검증도 두 번)이 나올 수 있는데, 같은 decision_id로 묶고
    event_type(signal_close/reversal_close/reversal_entry/entry)으로 구분해야 나중에
    "같은 판단에서 나온 두 이벤트"를 중복 집계하지 않는다.
    order_success: 이 검증이 실제로 성공한 주문에 대한 것인지.
    mode: "shadow"(사후 기록, 실거래에 영향 없음) / "entry_gate"(이 검증 결과가 실제로
    신규 진입 여부를 결정했음).
    gate_result: mode="entry_gate"일 때만 의미 있음 - "approved"/"blocked_wait"/
    "blocked_reject"/"blocked_error" 중 하나.
    error_reason: gpt_decision이 None일 때만 의미 있음 - "timeout"/"api_error"/
    "parse_error"/"sdk_error"/"unknown" 중 하나(openai_analyzer의 canonical 값 그대로).
    pipeline_context: "entry_gate"/"shadow_verification"/"hold_audit" 중 하나 - 같은
    error_reason이라도 맥락에 따라 실거래 영향이 다르므로(entry_gate만 fail-closed로
    주문을 막음) UI가 이 필드로 문구를 구분한다. mode와 겹치는 정보지만, mode는
    "entry_gate"/"shadow" 2가지뿐이라 Shadow 안에서 shadow_verification과 hold_audit을
    구분 못 해 별도로 둔다."""
    # Candidate C never belongs to the CORE GPT entry gate.
    if mode == 'entry_gate' and symbol not in notification_policy.CORE:
        return
    os.makedirs(user_dir, exist_ok=True)
    with open(_log_path(user_dir), "a", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {
                    **(outcome or {}),
                    "symbol": symbol,
                    "gemini_action": gemini_action,
                    "gemini_confidence": gemini_confidence,
                    "gpt_decision": gpt_decision,
                    "gpt_confidence": gpt_confidence,
                    "gpt_reasoning": gpt_reasoning,
                    "decision_id": decision_id,
                    "event_type": event_type,
                    "order_success": order_success,
                    "mode": mode,
                    "gate_result": gate_result,
                    "error_reason": error_reason,
                    "pipeline_context": pipeline_context,
                    "time": datetime.datetime.now().isoformat(timespec="seconds"),
                },
                ensure_ascii=False,
            )
            + "\n"
        )


def _load_all(user_dir: str) -> list:
    return jsonl_cache.load_jsonl_cached(_log_path(user_dir))


def recent(user_dir: str, limit: int = 100) -> list:
    """최신순으로 최근 검증 기록을 반환한다."""
    return list(reversed(_load_all(user_dir)))[:limit]


def recent_by_mode(user_dir: str, mode: str, limit: int = 100) -> list:
    """최신순으로 특정 mode 기록만 반환한다.

    Dashboard의 CORE GPT 진입 게이트는 실제 주문 여부를 결정한 entry_gate만
    보여줘야 하므로, 과거 Shadow 기록과 섞지 않는다.
    """
    out = []
    for record in reversed(_load_all(user_dir)):
        if record.get("mode") != mode or record.get("symbol") not in notification_policy.CORE:
            continue
        if mode == "entry_gate":
            labels = {"blocked_wait": "GPT 대기", "blocked_reject": "GPT 거절",
                      "blocked_error": "GPT 오류", "blocked_risk_score": "과거 위험점수 차단"}
            outcome = (
                "주문 완료" if record.get("order_success") is True else
                "승인 · 주문 미실행" if record.get("gate_result") == "approved" else
                labels.get(record.get("gate_result"), "주문 미실행")
            )
            record = dict(record, order_outcome=outcome)
        out.append(record)
        if len(out) >= limit:
            break
    return out


def summary(user_dir: str) -> dict:
    """Gemini와 GPT가 서로 동의(approve_now)/보류(wait)/반대(reject)한 건수와 비율을 요약한다.
    나중에 "GPT가 반대했던 거래를 실제로 안 했으면 성과가 어땠을지" 비교할 때 쓸
    기초 통계다 - 이 자체는 실거래에 아무 영향을 주지 않는다.

    decision은 approve_now(방향·타이밍 모두 동의) / wait(방향엔 동의하나 타이밍은 보류) /
    reject(방향 자체에 반대) 세 가지다. 예전 스키마(2단계 approve/reject)로 기록된 과거
    데이터는 legacy_count로 따로 센다 - 그래야 count == approve_now+wait+reject+legacy+
    no_verdict가 항상 맞고, agree_rate도 legacy를 뺀 새 스키마 기록끼리만 비교할 수 있다.

    legacy 판별은 decision 문자열이 아니라 decision_id 필드의 존재 여부로 한다 - 문자열로
    판별하면 "reject"는 옛 스키마에서도 똑같은 값이라(approve/reject 2단계) 구분이 안 되고
    옛 reject가 새 reject로 잘못 섞여 든다. decision_id는 이 필드가 도입된 이후 기록에만
    항상 채워지므로 신/구 스키마를 정확히 가른다.

    no_verdict_count: 신버전 기록인데 gpt_decision이 approve_now/wait/reject 중 어느
    것도 아닌 경우(대표적으로 entry_gate의 blocked_error - GPT를 아예 확인 못 해서
    verdict 자체가 없음)를 센다. 이것도 legacy와 마찬가지로 agree_rate 분모에서 빠져야
    한다 - 안 빼면 "GPT를 실제로 호출도 못 한 케이스"가 마치 GPT가 판단해준 것처럼
    분모에 섞여 동의율이 실제보다 낮게 나온다."""
    records = [r for r in _load_all(user_dir)
               if r.get('symbol') in notification_policy.CORE]
    total = len(records)
    shadow_mode_count = sum(1 for r in records if r.get("mode") == "shadow")
    entry_gate_mode_count = sum(1 for r in records if r.get("mode") == "entry_gate")
    no_mode_count = total - shadow_mode_count - entry_gate_mode_count
    new_records = [r for r in records if r.get("decision_id") is not None]
    legacy = total - len(new_records)
    approve_now = sum(1 for r in new_records if r.get("gpt_decision") == "approve_now")
    wait = sum(1 for r in new_records if r.get("gpt_decision") == "wait")
    reject = sum(1 for r in new_records if r.get("gpt_decision") == "reject")
    verdict_total = approve_now + wait + reject
    no_verdict = len(new_records) - verdict_total
    agree_rate = (approve_now / verdict_total * 100) if verdict_total else None

    # no_verdict를 원인별로 세분화한다(사용자 요구) - error_reason 필드 자체가 없는
    # 행(이 필드가 생기기 전에 기록된 과거 행)은 timeout 등으로 추정하지 않고
    # legacy_no_verdict_count로 따로 센다. 아래 6개 합은 항상 no_verdict와 같다.
    no_verdict_records = [r for r in new_records if r.get("gpt_decision") not in ("approve_now", "wait", "reject")]
    timeout_count = sum(1 for r in no_verdict_records if r.get("error_reason") == "timeout")
    api_error_count = sum(1 for r in no_verdict_records if r.get("error_reason") == "api_error")
    parse_error_count = sum(1 for r in no_verdict_records if r.get("error_reason") in ("parse_error","empty_response"))
    sdk_error_count = sum(1 for r in no_verdict_records if r.get("error_reason") == "sdk_error")
    unknown_count = sum(1 for r in no_verdict_records if "error_reason" in r and r.get("error_reason") not in ("timeout","api_error","parse_error","empty_response","sdk_error"))
    legacy_no_verdict_count = sum(1 for r in no_verdict_records if "error_reason" not in r)

    # mode="entry_gate"인 기록만 실제로 주문 여부를 결정했다 - 이 카운트는 "GPT 필터를
    # 실전 게이트로 썼을 때 실제로 몇 건을 승인/차단했는지"를 보여준다.
    gate_records = [r for r in new_records if r.get("mode") == "entry_gate"]
    gate_approved = sum(1 for r in gate_records if r.get("gate_result") == "approved")
    gate_blocked_wait = sum(1 for r in gate_records if r.get("gate_result") == "blocked_wait")
    gate_blocked_reject = sum(1 for r in gate_records if r.get("gate_result") == "blocked_reject")
    gate_blocked_error = sum(1 for r in gate_records if r.get("gate_result") == "blocked_error")
    # CORE tactical short(2026-08-28, 사용자 지시) - GPT가 "wait"을 줬지만
    # tactical_short_confirmation으로 override되어 실제 진입한 건수. 이 버킷을 따로
    # 안 세면 gate_approved+blocked_wait+blocked_reject+blocked_error 합이 gate_count와
    # 안 맞아 집계가 어긋난다(이번 세션에서 이미 한 번 겪은 aggregation 버그와 같은
    # 종류의 실수를 반복하지 않기 위함).
    gate_tactical_override = sum(1 for r in gate_records if r.get("gate_result") == "tactical_wait_override")

    return {
        "count": total,
        "shadow_mode_count": shadow_mode_count,
        "entry_gate_mode_count": entry_gate_mode_count,
        "no_mode_count": no_mode_count,
        "approve_now_count": approve_now,
        "wait_count": wait,
        "reject_count": reject,
        "legacy_count": legacy,
        "no_verdict_count": no_verdict,
        "timeout_count": timeout_count,
        "api_error_count": api_error_count,
        "parse_error_count": parse_error_count,
        "sdk_error_count": sdk_error_count,
        "unknown_count": unknown_count,
        "legacy_no_verdict_count": legacy_no_verdict_count,
        "agree_rate": agree_rate,
        "gate_count": len(gate_records),
        "gate_approved_count": gate_approved,
        "gate_timeout_bypass_count": sum(1 for r in gate_records if r.get("gate_result")=="TIMEOUT_BYPASS"),
        "gate_blocked_wait_count": gate_blocked_wait,
        "gate_blocked_reject_count": gate_blocked_reject,
        "gate_blocked_error_count": gate_blocked_error,
        "gate_tactical_override_count": gate_tactical_override,
    }
