"""보유 포지션 AI 관리(2026-09-11) 구조화 로그.

Gemini가 hold라고 판단해 그대로 유지하기로 한 포지션을, Gemini 재검토 -> GPT 최종
HOLD/REDUCE_50/CLOSE_ALL 게이트를 거쳐 기록한다. gpt_hold_audit.py(항상 무포지션,
절대 실주문 없음)와는 목적이 완전히 다르다 - 이 로그는 실제로 포지션을 감축/청산할 수
있는 경로(trader._handle_position_ai_review, AI_LIVE_CLOSE=True일 때만)를 기록한다.

event_type 종류(스펙): position_ai_review_started, gemini_position_review,
gpt_position_gate, position_ai_decision, position_ai_hold, position_ai_reduce_50,
position_ai_close_all, position_ai_timeout, position_ai_error,
position_ai_reconciliation, position_ai_partial_fill."""

import datetime
import json
import os

import jsonl_cache


def _log_path(user_dir: str) -> str:
    return os.path.join(user_dir, "position_ai_log.jsonl")


def record_event(user_dir: str, event_type: str, symbol: str, review_id: str, **fields) -> None:
    """공통 event_type + symbol + review_id(같은 리뷰 사이클의 여러 이벤트를 묶는 id)
    envelope에 이벤트별 필드를 얹어서 한 줄(JSONL)로 남긴다. API 키/시크릿 등은 이
    로그가 다루는 필드(action/confidence/reasoning/가격/수량) 어디에도 없으므로 별도
    redaction이 필요 없다(candidate_c_event_log와 다른 점 - 그쪽은 details가 임의
    dict라 redaction이 필요했지만, 여기는 필드가 고정돼 있다)."""
    os.makedirs(user_dir, exist_ok=True)
    record = {
        "event_type": event_type,
        "symbol": symbol,
        "review_id": review_id,
        "time": datetime.datetime.now().isoformat(timespec="seconds"),
        **fields,
    }
    with open(_log_path(user_dir), "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _load_all(user_dir: str) -> list:
    return jsonl_cache.load_jsonl_cached(_log_path(user_dir))


def recent(user_dir: str, limit: int = 100) -> list:
    """최신순으로 최근 이벤트를 반환한다."""
    return list(reversed(_load_all(user_dir)))[:limit]


def last_review_time(user_dir: str, symbol: str) -> str | None:
    """서버 재시작 후 쿨다운을 복구할 때 쓰는, 이 심볼의 마지막 리뷰 시작 시각."""
    last = None
    for rec in _load_all(user_dir):
        if rec.get("symbol") == symbol and rec.get("event_type") == "position_ai_review_started":
            last = rec.get("time")
    return last


def latest_decision(user_dir: str, symbol: str, since: datetime.datetime | None = None) -> dict | None:
    """대시보드용 - 이 심볼의 가장 최근 최종 판단(position_ai_decision) 기록.

    since(보통 현재 보유 포지션의 entry_time)가 주어지면 그보다 이전 기록은 무시한다.
    외부청산 후 새 포지션이 열렸는데, 이미 청산된 이전 포지션에 대한 옛날 판단이
    새 포지션 카드에 그대로 남아 표시되는 걸 막는다 - 2026-09-11 실거래에서 ETH가
    22:47에 REDUCE_50 판단을 받고 22:52에 외부청산됐는데, 23:13에 새로 연 포지션의
    카드에도 그 22:47 판단이 그대로 남아있던 것을 발견해 추가."""
    latest = None
    for rec in _load_all(user_dir):
        if rec.get("symbol") != symbol or rec.get("event_type") != "position_ai_decision":
            continue
        if since is not None:
            try:
                rec_time = datetime.datetime.fromisoformat(rec.get("time", ""))
            except ValueError:
                continue
            if rec_time < since:
                continue
        latest = rec
    return latest
