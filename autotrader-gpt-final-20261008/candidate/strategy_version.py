"""전략 버전 메타데이터(2026-08-31 Phase 1.5A) - 모든 신규 signal/trade/shadow
event에 공통으로 기록해야 하는 필드를 한 곳에서 계산한다. 과거 기록에는 절대
소급 적용하지 않는다 - 이 필드들이 없는 레코드는 그 자체로 "이 필드 도입 이전
기록"이라는 뜻이고, 근거 없이 현재 버전을 끼워넣지 않는다."""
import functools
import hashlib
import json
import os
import subprocess

STRATEGY_VERSION = "legacy_ai_v1"

_REPO_ROOT = os.path.dirname(os.path.abspath(__file__))


@functools.lru_cache(maxsize=1)
def git_commit() -> str | None:
    """배포된 소스의 정확한 커밋을 알 수 없으면(예: git 없이 배포됐거나 dirty
    working tree) None을 반환한다 - 모르는 값을 추정해서 채우지 않는다."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=3, cwd=_REPO_ROOT,
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:
        pass
    return None


def config_hash(cfg) -> str:
    """리스크/전략 판단에 영향을 주는 설정값만 해시한다 - API 키/시크릿/패스프레이즈는
    절대 포함하지 않는다."""
    fields = {
        "POSITION_SIZE_MODE": getattr(cfg, "POSITION_SIZE_MODE", None),
        "LEVERAGE": getattr(cfg, "LEVERAGE", None),
        "MAX_LEVERAGE": getattr(cfg, "MAX_LEVERAGE", None),
        "STOP_LOSS_PCT": getattr(cfg, "STOP_LOSS_PCT", None),
        "TAKE_PROFIT_PCT": getattr(cfg, "TAKE_PROFIT_PCT", None),
        "RISK_PER_TRADE_PCT": getattr(cfg, "RISK_PER_TRADE_PCT", None),
        "MIN_CONFIDENCE": getattr(cfg, "MIN_CONFIDENCE", None),
        "MIN_HOLD_MINUTES": getattr(cfg, "MIN_HOLD_MINUTES", None),
        "REENTRY_COOLDOWN_MINUTES": getattr(cfg, "REENTRY_COOLDOWN_MINUTES", None),
        "AI_LIVE_CLOSE": getattr(cfg, "AI_LIVE_CLOSE", None),
        "EXECUTION_MODE": getattr(cfg, "EXECUTION_MODE", None),
        "GPT_ENTRY_GATE_ENABLED": getattr(cfg, "GPT_ENTRY_GATE_ENABLED", None),
        "HOLD_AUDIT_ENABLED": getattr(cfg, "HOLD_AUDIT_ENABLED", None),
    }
    blob = json.dumps(fields, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def prompt_hash() -> str:
    """Gemini/GPT 프롬프트 템플릿 문자열 자체를 해시한다 - 프롬프트 문구가 바뀌면
    이 값도 바뀌어서, 과거 거래가 어느 버전의 프롬프트로 판단됐는지 사후 구분할
    수 있다."""
    import gemini_analyzer
    import openai_analyzer
    blob = gemini_analyzer.PROMPT_TEMPLATE + "||" + openai_analyzer.PROMPT_TEMPLATE
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def version_fields(cfg) -> dict:
    """새로 기록하는 모든 signal/trade/shadow event에 그대로 풀어 넣을 공통 필드.
    short_level처럼 이벤트마다 달라지는 값은 호출부가 별도로 채운다."""
    return {
        "strategy_version": STRATEGY_VERSION,
        "git_commit": git_commit(),
        "config_hash": config_hash(cfg),
        "prompt_hash": prompt_hash(),
        "execution_mode": getattr(cfg, "EXECUTION_MODE", "OFF"),
        "ai_live_close": getattr(cfg, "AI_LIVE_CLOSE", False),
        "position_size_mode": getattr(cfg, "POSITION_SIZE_MODE", None),
        "leverage": getattr(cfg, "LEVERAGE", None),
        "sl_pct": getattr(cfg, "STOP_LOSS_PCT", None),
        "tp_pct": getattr(cfg, "TAKE_PROFIT_PCT", None),
        "gemini_model": getattr(cfg, "GEMINI_MODEL", None),
        "gpt_model": getattr(cfg, "OPENAI_MODEL", None),
    }
