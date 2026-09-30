"""GPT Entry Gate latency 관측 로그(2026-08-29, 사용자 지시).

실거래 문제: ETH short candidate가 TACTICAL_SHORT_CONFIRMED=true까지 정상 통과했는데
GPT Entry Gate가 timeout(설정 10초)인데도 실제로는 30초 이상 걸려 fail-closed로
취소됐다. 원인은 openai 클라이언트가 SDK 기본 max_retries(2, 최대 3회 시도)를 그대로
써서 timeout이 최대 3번 겹친 것 - openai_analyzer.py에서 max_retries=0으로 고정.

이 로그는 그 회귀가 다시 생기지 않는지(응답 시간 분포, timeout 빈도) 관측하기 위한
것으로, mode="entry_gate" 호출에만 기록된다(Shadow/Hold Audit은 이번 작업 범위 밖이라
건드리지 않음). 실거래 판단에는 전혀 영향을 주지 않는 순수 관측 로그다."""
import datetime
import json
import os

import jsonl_cache


def _log_path(user_dir: str) -> str:
    return os.path.join(user_dir, "gpt_latency_log.jsonl")


def record_call(
    user_dir: str,
    symbol: str,
    purpose: str,
    model: str,
    request_start: str,
    response_ms: float,
    timed_out: bool,
    retry_count: int,
    error_reason: str | None = None,
    prompt_chars: int | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
) -> None:
    """request_start: 요청 시작 시각(ISO). response_ms: 응답까지 걸린 시간(성공/실패 무관).
    retry_count: 이 호출에 실제로 허용된 SDK 재시도 횟수(호출부가 openai 클라이언트에
    명시적으로 설정한 값 그대로 - SDK가 응답에 실제 재시도 횟수를 노출하지 않으므로,
    "설정값 이상 재시도가 절대 발생할 수 없다"는 걸 보장하는 설정값 자체를 기록한다).
    prompt_chars: 토큰 계산 라이브러리 의존성 없이 쓸 수 있는 프롬프트 크기 근사치."""
    os.makedirs(user_dir, exist_ok=True)
    with open(_log_path(user_dir), "a", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {
                    "symbol": symbol,
                    "purpose": purpose,
                    "model": model,
                    "request_start": request_start,
                    "response_ms": response_ms,
                    "timed_out": timed_out,
                    "retry_count": retry_count,
                    "error_reason": error_reason,
                    "prompt_chars": prompt_chars,
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "time": datetime.datetime.now().isoformat(timespec="seconds"),
                },
                ensure_ascii=False,
            )
            + "\n"
        )


def _load_all(user_dir: str) -> list:
    return jsonl_cache.load_jsonl_cached(_log_path(user_dir))


def recent(user_dir: str, limit: int = 200) -> list:
    """최신순으로 최근 기록을 반환한다."""
    return list(reversed(_load_all(user_dir)))[:limit]


def _percentile(sorted_values: list, pct: float) -> float | None:
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return sorted_values[0]
    k = (len(sorted_values) - 1) * pct
    lo = int(k)
    hi = min(lo + 1, len(sorted_values) - 1)
    if lo == hi:
        return sorted_values[lo]
    frac = k - lo
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * frac


def summary(user_dir: str, purpose: str = "entry_gate", limit: int = 500) -> dict:
    """최근 latency 분포(count/avg/p50/p95/max/timeout_count)를 요약한다. purpose로
    필터링해서 Entry Gate 호출만(기본값) 본다 - Shadow/Hold Audit은 지연이 실거래에
    영향이 없어 같은 기준으로 섞어 볼 이유가 없다."""
    records = [r for r in recent(user_dir, limit=limit) if r.get("purpose") == purpose]
    latencies = sorted(
        r["response_ms"] for r in records if isinstance(r.get("response_ms"), (int, float))
    )
    timeout_count = sum(1 for r in records if r.get("timed_out"))
    return {
        "count": len(records),
        "avg_ms": (sum(latencies) / len(latencies)) if latencies else None,
        "p50_ms": _percentile(latencies, 0.50),
        "p95_ms": _percentile(latencies, 0.95),
        "max_ms": latencies[-1] if latencies else None,
        "timeout_count": timeout_count,
    }
