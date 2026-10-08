"""Candidate C GPT Entry Gate 판정 로그(2026-09-13, 사용자 지시 - 대시보드에
"Candidate C GPT 승인/차단/오류 수"를 보여주기 위한 최소 구조화 로그).

CORE의 gpt_shadow_log.py/position_ai_log.py와 동일한 append-only JSONL 관례를
따른다 - 이 로그 자체는 매매 판단에 전혀 관여하지 않는다(순수 관찰/집계용)."""
import datetime
import json
import os

import jsonl_cache


def _log_path(user_dir: str) -> str:
    return os.path.join(user_dir, "candidate_c_gpt_gate_log.jsonl")


def record(user_dir: str, symbol: str, side: str, gate_result: str, error_reason: str | None) -> None:
    os.makedirs(user_dir, exist_ok=True)
    record_ = {
        "symbol": symbol, "side": side, "gate_result": gate_result, "error_reason": error_reason,
        "time": datetime.datetime.now().isoformat(timespec="seconds"),
    }
    with open(_log_path(user_dir), "a", encoding="utf-8") as f:
        f.write(json.dumps(record_, ensure_ascii=False) + "\n")


def recent(user_dir: str, limit: int = 100) -> list:
    return list(reversed(jsonl_cache.load_jsonl_cached(_log_path(user_dir))))[:limit]


def summary_counts(user_dir: str) -> dict:
    """대시보드용 - 전체 기록 기준 승인/차단(사유별)/오류 건수. gate_result가
    "approved"면 승인, "blocked_error"면 오류, 그 외 blocked_*는 차단으로 묶는다."""
    rows = jsonl_cache.load_jsonl_cached(_log_path(user_dir))
    approved = sum(1 for r in rows if r.get("gate_result") == "approved")
    errors = sum(1 for r in rows if r.get("gate_result") == "blocked_error")
    blocked_other = sum(
        1 for r in rows
        if isinstance(r.get("gate_result"), str)
        and r["gate_result"].startswith("blocked_")
        and r["gate_result"] != "blocked_error"
    )
    # [2026-09-16, 사용자 직접 지시 - Candidate C 전용 GPT ON/OFF] GPT를 아예
    # 안 부른(규칙 기반) 건은 별도로 센다 - approved/blocked 어느 쪽에도 안
    # 섞여야 승인율 통계가 왜곡되지 않는다.
    rule_based_no_gpt = sum(1 for r in rows if r.get("gate_result") == "rule_based_no_gpt_review")
    return {"approved": approved, "blocked": blocked_other, "error": errors,
            "rule_based_no_gpt": rule_based_no_gpt, "total": len(rows)}
