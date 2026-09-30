import datetime
import json
import os

import jsonl_cache

# 모델별 대략적인 단가(USD, 100만 토큰당). 실제 요금은 시기/모델에 따라 바뀔 수 있으니
# 참고용 추정치이고, 정확한 청구액은 각 제공사 콘솔에서 확인해야 한다.
PRICING = {
    "gemini": {"input": 0.75, "output": 3.75},  # Gemini 3.8 Flash introductory 2026 pricing
    "openai": {"input": 0.25, "output": 2.00},  # GPT-5 mini급 (검증용 - 참고용 추정치)
}


def _log_path(user_dir: str) -> str:
    return os.path.join(user_dir, "token_usage.jsonl")


def record_usage(
    user_dir: str, symbol: str, input_tokens: int, output_tokens: int, provider: str = "gemini",
    purpose: str | None = None,
) -> None:
    """purpose: 같은 provider 안에서도 호출 목적을 구분하고 싶을 때만 넘긴다(예: openai의
    "shadow"/"entry_gate"/"hold_audit"). 넘기지 않으면 기존 기록과 동일하게 provider별
    집계에만 잡힌다 - 하위 호환을 위해 선택 필드로 둔다."""
    pricing = PRICING.get(provider, PRICING["gemini"])
    total_tokens = input_tokens + output_tokens
    cost_usd = (
        input_tokens / 1_000_000 * pricing["input"]
        + output_tokens / 1_000_000 * pricing["output"]
    )
    os.makedirs(user_dir, exist_ok=True)
    with open(_log_path(user_dir), "a", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {
                    "symbol": symbol,
                    "provider": provider,
                    "purpose": purpose,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "total_tokens": total_tokens,
                    "cost_usd": cost_usd,
                    "time": datetime.datetime.now().isoformat(timespec="seconds"),
                },
                ensure_ascii=False,
            )
            + "\n"
        )


def summary(user_dir: str) -> dict:
    """누적/오늘 토큰 사용량과 추정 비용(USD)을 반환한다. 제공사(gemini/openai)별
    비용도 별도로 나눠서 준다 - GPT 검증(Shadow Mode)이 실제로 얼마씩 나가는지
    따로 확인할 수 있어야 붙일 가치가 있는지 판단할 수 있다."""
    path = _log_path(user_dir)
    empty_by_provider = {"call_count": 0, "total_tokens": 0, "total_cost_usd": 0.0}
    empty = {
        "call_count": 0,
        "total_tokens": 0,
        "total_cost_usd": 0.0,
        "today_tokens": 0,
        "today_cost_usd": 0.0,
        "by_provider": {},
        "by_purpose": {},
    }
    if not os.path.exists(path):
        return empty

    today = datetime.date.today().isoformat()
    call_count = total_tokens = today_tokens = 0
    total_cost = today_cost = 0.0
    by_provider: dict = {}
    by_purpose: dict = {}
    for rec in jsonl_cache.load_jsonl_cached(path):
        call_count += 1
        rec_tokens = rec.get("total_tokens", 0)
        rec_cost = rec.get("cost_usd", 0.0)
        total_tokens += rec_tokens
        total_cost += rec_cost
        if (rec.get("time") or "").startswith(today):
            today_tokens += rec_tokens
            today_cost += rec_cost

        provider = rec.get("provider") or "gemini"
        p = by_provider.setdefault(provider, dict(empty_by_provider))
        p["call_count"] += 1
        p["total_tokens"] += rec_tokens
        p["total_cost_usd"] += rec_cost

        # purpose는 선택 필드(예: openai의 shadow/entry_gate/hold_audit 구분용) - 없는
        # 기록(기존 데이터 포함)은 굳이 "unknown" 등으로 억지로 채우지 않고 그냥 뺀다.
        purpose = rec.get("purpose")
        if purpose:
            pp = by_purpose.setdefault(purpose, dict(empty_by_provider))
            pp["call_count"] += 1
            pp["total_tokens"] += rec_tokens
            pp["total_cost_usd"] += rec_cost

    return {
        "call_count": call_count,
        "total_tokens": total_tokens,
        "total_cost_usd": total_cost,
        "today_tokens": today_tokens,
        "today_cost_usd": today_cost,
        "by_provider": by_provider,
        "by_purpose": by_purpose,
    }
