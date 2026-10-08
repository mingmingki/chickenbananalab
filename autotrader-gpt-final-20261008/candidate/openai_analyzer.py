import datetime
import json
import logging
import threading
import time

import openai
from openai import OpenAI

import gpt_latency_log
import timeframes
from position_risk_context import format_position_risk_context
import usage_log

logger = logging.getLogger("trader.openai")

# GPT 검증은 항상 별도 스레드(Shadow Mode)에서 호출되지만, 그래도 명시적인 타임아웃 없이
# 무한정 기다리게 두면 API가 멈췄을 때 스레드가 계속 쌓일 수 있다 - 안전하게 잘라낸다.
REQUEST_TIMEOUT_SECONDS = 20.0


def _get_client(cfg) -> OpenAI:
    # 계정마다 OpenAI 키가 다르므로 클라이언트도 cfg 인스턴스에 붙여서 계정별로 캐싱한다.
    # Shadow Mode/Entry Gate가 공유해서 쓰는 client다 - Hold Audit은 아래
    # _get_hold_audit_client()로 완전히 별도 인스턴스를 쓴다.
    client = getattr(cfg, "_openai_client", None)
    if client is None:
        client = OpenAI(api_key=cfg.OPENAI_API_KEY, timeout=REQUEST_TIMEOUT_SECONDS)
        cfg._openai_client = client
    return client


def _get_hold_audit_client(cfg) -> OpenAI:
    """Hold Audit 전용 OpenAI 클라이언트. Shadow/Entry Gate가 쓰는 client(_get_client)와
    의도적으로 별도 인스턴스를 쓴다 - Hold Audit은 백그라운드 스레드에서, Entry Gate는
    매매 스레드에서 거의 동시에 처음 GPT를 호출할 수 있는 상황이 실제로 있었다(같은
    계정에서 여러 심볼이 동시에 hold+bullish 조건을 만족하는 경우). 신규 진입 여부를
    실제로 결정하는 Entry Gate 경로가 순수 관찰용인 Hold Audit과 리소스를 공유해 영향을
    받는 일이 없도록, OKX client를 매매/화면갱신/빗썸로거용으로 나눈 것과 같은 원칙으로
    분리한다."""
    client = getattr(cfg, "_openai_hold_audit_client", None)
    if client is None:
        client = OpenAI(api_key=cfg.OPENAI_API_KEY, timeout=REQUEST_TIMEOUT_SECONDS)
        cfg._openai_hold_audit_client = client
    return client


def reset_client(cfg) -> None:
    """API 키가 바뀌었을 때 캐시된 클라이언트를 버려서 다음 호출이 새 키를 쓰게 한다."""
    cfg._openai_client = None
    cfg._openai_hold_audit_client = None


# --- openai SDK(3.x) 응답 파싱 스레드 안전성 워크어라운드 ---
#
# openai SDK가 HTTP 응답을 pydantic 모델(ChatCompletion/ChatCompletionMessage 등)로
# 변환하는 내부 경로(openai._models.construct_type)는, 그 모델 타입이 프로세스에서
# "처음" 파싱될 때 pydantic 스키마(__pydantic_core_schema__)를 지연 생성한다. 이
# 지연 생성이 여러 스레드에서 동시에 처음 트리거되면 경합이 나서
#   AttributeError: type object 'ChatCompletionMessage' has no attribute
#   '__pydantic_core_schema__'
# 같은 예외가 난다 - 실제로 재현했다(openai 3.3.1 + pydantic 2.13.4 조합; 프로세스
# 시작 후 여러 스레드가 동시에 ChatCompletion을 처음 파싱하면 재현되고, 단일 스레드로
# 한 번 미리 파싱해두면 이후 동시 호출에서는 경합이 사라지는 것도 확인했다). 우리
# 프롬프트/전략 코드와는 무관한 SDK 자체의 스레드 안전성 문제다.
#
# Shadow 검증(백그라운드 스레드)·Hold Audit(백그라운드 스레드)·Entry Gate(매매 스레드)가
# 같은 프로세스 안에서 여러 심볼에 대해 GPT를 거의 동시에 처음 호출할 수 있어서, 실제
# API를 부르기 전에 가짜(canned) 응답으로 한 번 미리 모델을 "예열"해서 이 레이스가 아예
# 일어날 조건을 없앤다. 실제 네트워크 호출은 전혀 하지 않는다.
_warmup_lock = threading.Lock()
_warmed_up = False

_WARMUP_RESPONSE = {
    "id": "warmup", "object": "chat.completion", "created": 0, "model": "warmup",
    "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "{}"}}],
}


def _ensure_response_models_warmed_up() -> None:
    global _warmed_up
    if _warmed_up:
        return
    with _warmup_lock:
        if _warmed_up:
            return
        try:
            from openai._models import construct_type
            from openai.types.chat.chat_completion import ChatCompletion

            construct_type(type_=ChatCompletion, value=json.loads(json.dumps(_WARMUP_RESPONSE)))
        except Exception:
            logger.exception("OpenAI 응답 모델 예열 실패 - 이후 동시 호출에서 SDK 파싱 레이스가 재발할 수 있음")
        _warmed_up = True


def _classify_openai_error(exc: Exception) -> str:
    """openai SDK 호출 실패 원인을 4가지로 분류한다 - 예전에는 원인과 무관하게 로그에
    전부 "timeout"이라고 찍혀서(리뷰에서 지적됨: 실제로는 위 SDK 파싱 레이스였는데 로그만
    보고는 timeout으로 오인해 원인 파악이 늦어짐), 실제 원인별로 구분해서 남긴다.

    "timeout": 실제로 응답을 못 받고 시간 초과.
    "api": 연결 실패, 인증/요청 오류, 429/5xx 등 API 서버가 명시적으로 응답한 오류.
    "malformed_response": 응답이 SDK가 기대하는 스키마와 안 맞음(openai가
    pydantic.ValidationError를 잡아 APIResponseValidationError로 감싸는 경우).
    "sdk_parsing": 위 어디에도 안 속하는, openai/pydantic 내부에서 난 예상 못 한 오류
    (예: 위에서 재현한 동시 호출 시 pydantic 모델 스키마 생성 레이스)."""
    if isinstance(exc, openai.APITimeoutError):
        return "timeout"
    if isinstance(exc, openai.APIResponseValidationError):
        return "malformed_response"
    if isinstance(exc, (openai.APIConnectionError, openai.APIStatusError)):
        return "api"
    return "sdk_parsing"


# UI/JSONL에 남기는 외부 공개용 canonical 값 - 위 _classify_openai_error()의 내부 4분류를
# 그대로 노출하지 않고 여기서 한 번 더 정리한다(내부 exception 문자열/응답 전문은 절대
# 이 값에 안 실린다 - 딱 이 5개 문자열 중 하나뿐).
def _canonical_error_reason(internal_reason: str) -> str:
    return {
        "timeout": "timeout",
        "api": "api_error",
        "malformed_response": "parse_error",
        "sdk_parsing": "sdk_error",
    }.get(internal_reason, "unknown")


def validate_key(api_key: str) -> tuple[bool, str]:
    """OpenAI API 키가 실제로 인증되는지 확인한다."""
    try:
        client = OpenAI(api_key=api_key, timeout=REQUEST_TIMEOUT_SECONDS)
        list(client.models.list())
        return True, ""
    except Exception as exc:
        return False, str(exc)


PROMPT_TEMPLATE = """당신은 암호화폐 선물 트레이딩의 리스크/추세 검증자입니다.
아래는 다른 AI(단기 트레이더)가 {symbol}에 대해 이미 내린 판단입니다. 이 판단을 실제로
실행해도 좋을지 검증하세요.

타임프레임 우선순위(트레이더 AI와 동일한 기준을 적용하세요): 1순위 1일봉(1D)=시장 전체
레짐, 2순위 4시간봉(4H)=주 추세, 3순위 1시간봉(1H)=추세 유지 확인, 4순위 5분봉/3분봉=진입
타이밍, 5순위 1분봉=가장 낮은 우선순위(단기 확인용). 1일봉/4시간봉이 뚜렷한 상승(또는
하락) 구조인데, 트레이더 AI가 1분봉/3분봉의 단기 반대 움직임만 근거로 삼아 상위 추세에
역행하는 진입(예: 상승 레짐에서 숏)을 결정했다면, 1시간봉 구조 훼손 등 아주 강한 상위
타임프레임 근거가 없는 한 wait 또는 reject를 우선 검토하세요.

단, 1일봉(1D)이 bullish라는 사실 자체는 SHORT의 위험요소일 뿐 절대적인 veto가
아닙니다. 아래 SHORT_LEVEL이 EARLY/TACTICAL/STRONG/FULL_BEARISH 중 하나로 채워져
있다면 1D가 bullish라는 이유만으로 wait 또는 reject하지 마세요. TACTICAL 이상은 기존처럼
4H/1H 및 하위 구조의 하락 정렬을 뜻합니다. EARLY는 두 경우가 있습니다: 기존 4H/1H
약세 초기단계, 또는 candle_summary의 [급등 후 조정 모드] CORRECTION_ACTIVE=true가
확인되어 최근 급등 뒤 고점 되돌림 + 1H 모멘텀 약화 + 3m/5m 하락구조가 모두 확인된
조정 숏입니다. 후자의 EARLY는 4H가 아직 완전히 bearish가 아니어도 정상입니다. 이때는
바로 그 조정을 4H 붕괴까지 기다렸다가 뒤늦게 쫓아가는 대신 현재 short 타이밍 자체를
검증하세요. 다만 1D가 bullish이면 FULL_BEARISH 추세추종 숏보다 보수적으로 판단하고,
reasoning에 SHORT_LEVEL과 조정모드 여부를 명시하세요.

판단에 참고할 두 가지 대조 사례(예시일 뿐 기계적으로 따르지 말고 실제 데이터로
직접 판단하세요):
A. 1D bullish, 4H는 약화 신호(EMA20 이탈 또는 MACD 명확한 하락), 1H bearish, 5m/
   3m/1m 모두 하락 정렬, SHORT_LEVEL=TACTICAL(또는 STRONG/FULL_BEARISH) -> 이런
   경우는 4H/1H 구조 훼손이 이미 실질적이므로 approve_now도 합리적인 선택지입니다.
B. 1D bullish, 4H는 중립/혼조(뚜렷한 이탈이나 MACD 하락전환 없음), 1H만 약세,
   SHORT_LEVEL=NONE -> 이런 경우는 아직 4H 상위 추세가 훼손되지 않았으므로
   wait가 합리적인 선택지입니다.
핵심은 "1D가 bullish인가"가 아니라 현재 하락 조정이 얼마나 구체적으로 확인됐는가입니다.
TACTICAL 이상은 4H/1H 하락 정렬을, 조정모드 EARLY는 급등 후 되돌림·1H 약화·3m/5m
하락 정렬을 뜻합니다. 그 근거를 무시하고 1D만으로 wait/reject하지 마세요.

아래 MARGIN/SIZING 정보는 참고용 컨텍스트일 뿐입니다 - 증거금 크기 때문에 방향 판단을
바꾸지 마세요(판단 기준은 오직 시장 조건입니다). SHORT_LEVEL이 NONE이거나 이 섹션
자체가 없으면 위 원칙(강한 근거 없이는 wait/reject 우선)을 그대로 적용하세요.
{short_level_context}
검토 타임프레임: {timeframes_desc} (낮은 타임프레임=단기 모멘텀, 높은 타임프레임=전체 추세,
1일봉이 최상위 레짐)

{candle_summary}

현재 포지션: {position_desc}

트레이더 AI의 판단:
- action: {gemini_action}
- confidence: {gemini_confidence}
- market_regime(트레이더 AI가 판단한 시장 레짐): {gemini_regime} (확신도 {gemini_regime_confidence})
- trade_alignment(트레이더 AI 스스로 평가한 레짐 정합성): {gemini_trade_alignment}
- AI 제안 SL/TP: {gemini_exit_plan}
- reasoning: {gemini_reasoning}

{exit_price_contract}
진입 방향/타이밍 판단과 SL/TP 가격 검증은 분리해서 답하세요. Gemini의 exit_plan이
시장 구조상 합리적이고 위 실행 계약까지 모두 만족할 때만 approve하세요. 계약 밖 가격은 approve 금지이며,
방향은 맞지만 가격을 조정해야 하면 revise와 수정 가격을,
비정상적이거나 근거가 약하면 reject를 선택하세요. Gemini의 exit_plan이 null/누락인데
진입 자체는 approve_now라면, 반드시 exit_plan_decision=revise로 두고 당신이 직접 계산한
유효한 SL/TP 가격을 exit_plan에 채우세요. exit_plan을 reject해도 진입 decision을 반드시
reject할 필요는 없습니다. 가격 계획만 reject되면 시스템은 기존 Adaptive 계산으로 fallback합니다.

{execution_context} 그러니 애매하게 "조금 기다렸다가" "소규모로 나눠서" 같은 절충안을 고르지
말고, 아래 세 가지 중 트레이더 AI의 판단·타이밍에 대한 입장을 명확히 하나만 고르세요.

반드시 아래 JSON 형식으로만 답하세요. 다른 텍스트는 포함하지 마세요.
{{
  "decision": "approve_now" | "wait" | "reject",
  "confidence": 0.0에서 1.0 사이 숫자,
  "exit_plan_decision": "approve" | "revise" | "reject" | "not_applicable",
  "exit_plan": {{
    "stop_loss_price": 숫자 | null,
    "take_profit_1_price": 숫자 | null,
    "take_profit_2_price": 숫자 | null,
    "confidence": 0.0에서 1.0 사이 숫자 | null,
    "reasoning": "수정 가격의 근거"
  }} | null,
  "reasoning": "판단 근거를 1~2문장으로"
}}

- "approve_now": 방향과 지금 이 타이밍에 모두 동의합니다.
- "wait": 방향에는 대체로 동의하지만, 지금 당장의 타이밍에는 동의하지 않습니다
  (단기 노이즈일 가능성, 좀 더 확인이 필요함 등).
- "reject": 방향(트레이더 AI의 판단 자체)에 반대합니다.
"""

# verify()의 실제 용도에 따라 GPT에게 자신의 답변이 실제로 어떤 힘을 갖는지 정확히
# 알려준다 - "당신의 답변은 결과에 영향 없다"는 문구를 신규진입 게이트에도 그대로
# 보내면 GPT가 실제 승인권자라는 걸 모른 채 가볍게 답할 위험이 있다.
SHORT_LEVEL_CONTEXT_TEMPLATE = """
[SHORT_LEVEL 판단 근거 - trader.core_short_level.classify() 결과]
SHORT_LEVEL: {level}
- 4H bearish: {h4_bearish}
- 1H bearish: {h1_bearish}
- 3m bearish: {m3_bearish}
- 5m bearish: {m5_bearish}
- regime: {regime} / gemini confidence: {confidence}
- post-runup correction active: {correction_active}
- recent run-up: {runup_24h_pct}% / peak retracement: {peak_retracement_pct}% / peak extension: {peak_extension_atr} ATR
[MARGIN/SIZING 정보 - 참고용, 방향 판단에 영향 주지 말 것]
SIZING_MODE: {sizing_mode}
SELECTED_MARGIN: {selected_margin} USDT (fixed_margin={fixed_margin}, max_margin={max_margin})
"""

# CORE SHORT 공격 레벨(2026-08-29, 사용자 지시 - 2026-08-28의 단일 tactical short를
# EARLY/TACTICAL/STRONG/FULL_BEARISH 4단계로 확장) - action=="short"이고 레벨이
# NONE이 아닐 때만 채워진다. LONG/close 판단이나 openai_analyzer.verify()가
# short_level_ctx를 안 받는 다른 호출부(Shadow verification, Hold Audit)에서는
# 항상 빈 문자열이라 프롬프트가 이전과 완전히 동일하다.
EXECUTION_CONTEXT = {
    "shadow": (
        "이 트레이더 AI의 판단은 이미 그대로 실행되었습니다 (당신의 답변은 사후 기록용일 뿐 "
        "이 주문을 취소하거나 되돌리지 않습니다 - 지금은 그 판단이 맞았는지 평가하는 단계입니다)."
    ),
    "entry_gate": (
        "이 판단은 아직 실행되지 않았습니다 - 당신이 \"approve_now\"를 선택해야만 실제로 주문이 "
        "나갑니다. \"wait\" 또는 \"reject\"를 선택하면 이번 사이클에는 주문이 나가지 않습니다 "
        "(다음 검토 주기에 Gemini가 다시 판단합니다). 당신의 답변이 실거래 자금의 실제 진입 "
        "여부를 결정하니 신중하게 판단하세요."
    ),
    "exit_plan_only": (
        "Candidate C의 진입 방향과 진입 허용 여부는 4H 추세+Donchian 규칙으로 이미 확정되어 있으며 "
        "당신은 진입을 차단하거나 방향을 바꿀 권한이 없습니다. decision 필드는 스키마 호환용일 뿐이고, "
        "오직 Gemini가 제안한 SL/TP 가격을 approve/revise/reject 하세요. 가격계획이 부적절하면 reject하면 "
        "시스템이 기존 Adaptive SL/TP로 fallback합니다."
    ),
}


def _condensed_tactical_candle_summary(candle_summary: str) -> str:
    """SHORT_LEVEL 프롬프트 축약(2026-08-29, 사용자 지시) - 4H/1H/3m/5m bearish
    여부·regime·confidence는 이미 SHORT_LEVEL_CONTEXT_TEMPLATE로 요약해서 넘기므로,
    원시 멀티타임프레임 요약(candle_summary)에서는 "1D bias" 판단에 필요한 1D 줄만
    남기고 나머지(4H/1H/5m/3m/1m 원시 요약)는 제거해 latency/token을 줄인다. 1D 줄을
    못 찾으면(포맷이 예상과 다름) 판단 품질 저하보다 축약 포기를 택해 원본 그대로
    돌려준다."""
    label = timeframes.label("1d")
    prefix = f"[{label}]"
    for line in candle_summary.split("\n"):
        if line.startswith(prefix):
            import market_context
            import core_entry_timing
            return (line + market_context.preserve_prompt_block(candle_summary)
                    + core_entry_timing.preserve_prompt_block(candle_summary))
    return candle_summary


def _apply_exit_contract_to_verdict(verdict, gemini_plan, gpt_plan, exit_price_contract):
    from adaptive_exit_engine import normalize_ai_price_plan, validate_ai_price_plan_contract
    gemini_plan = normalize_ai_price_plan(gemini_plan)
    gpt_plan = normalize_ai_price_plan(gpt_plan)
    if not exit_price_contract:
        if verdict == "revise" and gpt_plan is None:
            return "reject", None, "gpt_revision_invalid"
        return verdict, (gpt_plan if verdict == "revise" else None), "contract_not_supplied"
    gemini_reason = validate_ai_price_plan_contract(gemini_plan, exit_price_contract)
    gpt_reason = validate_ai_price_plan_contract(gpt_plan, exit_price_contract) if gpt_plan is not None else "plan_invalid"
    if verdict == "approve":
        if gemini_reason == "ok":
            return "approve", None, "gemini_contract_ok"
        if gpt_reason == "ok":
            return "revise", gpt_plan, "gpt_recovery_contract_ok"
        return "reject", None, f"gemini_contract_{gemini_reason}"
    if verdict == "revise":
        if gpt_reason == "ok":
            return "revise", gpt_plan, "gpt_revision_contract_ok"
        return "reject", None, f"gpt_revision_contract_{gpt_reason}"
    return verdict, None, "not_applicable"


def verify(
    cfg, symbol: str, tf_list: list, candle_summary: str, position: dict | None, gemini_decision: dict,
    timeout: float | None = None, purpose: str = "shadow", short_level_ctx: dict | None = None,
    max_retries: int | None = None, exit_price_contract: dict | None = None,
) -> dict | None:
    """Gemini가 이미 내린 판단(long/short/close)을 GPT가 승인/거절만 짧게 검증한다.

    두 가지 용도로 쓰인다: (1) purpose="shadow" - 로그에만 남고 실제 주문에는 영향 없음,
    (2) purpose="entry_gate" - 이 결과가 실제로 주문을 막을 수 있어서, 호출부
    (trader._gpt_entry_gate)가 보통 더 짧은 timeout을 넘겨서 쓴다. 두 경우 프롬프트에서
    GPT에게 알려주는 "당신의 답변이 실제로 어떤 효과가 있는지" 설명(EXECUTION_CONTEXT)이
    다르다 - Shadow용 문구를 entry_gate에도 그대로 쓰면 GPT가 자기 답변에 실제 결정권이
    있다는 걸 모른 채 가볍게 판단할 위험이 있다.

    OPENAI_API_KEY가 없으면(선택 기능이라 안 넣어도 됨) 조용히 None을 반환한다 - 실제로는
    호출부가 이 경우 애초에 verify()를 부르기 전에 이미 걸러내므로 도달할 일이 거의 없다.

    API 호출/응답 파싱이 실패하면 {"decision": None, "confidence": None, "reasoning": "",
    "error_reason": "timeout"|"api_error"|"parse_error"|"sdk_error"|"unknown"}을 반환한다
    (성공 시엔 "error_reason": None이 추가된 정상 결과를 반환) - 호출부는 "decision이
    None인지"로 실패 여부를 판단하고, error_reason으로 관측/로그용 원인만 구분한다.
    실패 시 실제 동작(Shadow는 그냥 넘어감, 진입 게이트는 fail-closed)은 이 변경으로
    전혀 달라지지 않는다.

    max_retries(2026-08-29, 사용자 지시): None이면 openai 클라이언트의 기존 동작(SDK
    기본값 2 - 실측 회귀 원인, 아래 참고) 그대로다. trader._gpt_entry_gate는 반드시
    0을 명시적으로 넘긴다 - 이전에는 max_retries를 아무도 지정하지 않아 SDK 기본값
    2(최대 3회 시도)가 그대로 적용됐고, timeout=10초 x 최대 3회 시도가 겹쳐 실제
    호출이 30초 이상 걸려 fail-closed로 취소되는 사고가 실거래에서 발생했다(2026-08-29
    실측: TACTICAL_SHORT_CONFIRMED=true까지 통과한 ETH/BTC short가 GPT timeout으로
    막힘). purpose="entry_gate"일 때만 latency(gpt_latency_log)를 기록한다 - Shadow/
    Hold Audit은 이번 작업 범위 밖이라 관측 대상에 넣지 않는다."""
    if not cfg.OPENAI_API_KEY:
        return None

    log = cfg.logger or logger

    if position:
        position_desc = (
            f"{position['side']} {position['contracts']} 계약, "
            f"진입가 {position['entry_price']:.2f}, "
            f"미실현손익 {position['unrealized_pnl']:.2f} USDT"
        )
    else:
        position_desc = "없음"

    short_level_context = ""
    effective_candle_summary = candle_summary
    if short_level_ctx is not None:
        r = short_level_ctx.get("reasons", {})
        short_level_context = SHORT_LEVEL_CONTEXT_TEMPLATE.format(
            level=short_level_ctx.get("level"),
            h4_bearish=r.get("4h_bearish"), h1_bearish=r.get("1h_bearish"),
            m3_bearish=r.get("3m_bearish"), m5_bearish=r.get("5m_bearish"),
            regime=short_level_ctx.get("regime"), confidence=short_level_ctx.get("confidence"),
            correction_active=bool(short_level_ctx.get("correction_active")),
            runup_24h_pct=short_level_ctx.get("runup_24h_pct"),
            peak_retracement_pct=short_level_ctx.get("peak_retracement_pct"),
            peak_extension_atr=short_level_ctx.get("peak_extension_atr"),
            sizing_mode=short_level_ctx.get("sizing_mode"),
            selected_margin=short_level_ctx.get("selected_margin"),
            fixed_margin=short_level_ctx.get("fixed_margin"),
            max_margin=short_level_ctx.get("max_margin"),
        )
        if purpose != "entry_gate":
            effective_candle_summary = _condensed_tactical_candle_summary(candle_summary)

    prompt = PROMPT_TEMPLATE.format(
        symbol=symbol,
        timeframes_desc=", ".join(timeframes.label(tf) for tf in tf_list),
        candle_summary=effective_candle_summary,
        position_desc=position_desc,
        gemini_action=gemini_decision.get("action"),
        gemini_confidence=gemini_decision.get("confidence"),
        gemini_regime=gemini_decision.get("market_regime") or "-",
        gemini_regime_confidence=gemini_decision.get("regime_confidence") or 0.0,
        gemini_trade_alignment=gemini_decision.get("trade_alignment") or "-",
        gemini_exit_plan=json.dumps(gemini_decision.get("exit_plan"), ensure_ascii=False),
        gemini_reasoning=gemini_decision.get("reasoning", ""),
        execution_context=EXECUTION_CONTEXT.get(purpose, EXECUTION_CONTEXT["shadow"]),
        short_level_context=short_level_context,
        exit_price_contract=(__import__("adaptive_exit_engine").format_ai_price_contract(exit_price_contract) if exit_price_contract else ""),
    )

    if purpose == "entry_gate":
        prompt += (
            "\n[CORE 초기 진입 검토 우선 규칙]\n"
            "이번 entry_gate에서는 앞의 TF 우선순위 예시보다 이 규칙을 우선합니다. "
            "1D/4H는 배경 위험이고 완전 EMA 정렬은 초기 진입의 필수조건이 아닙니다. "
            "CORE_ENTRY_TIMING의 최초 확정 시각·진행 거리·실제 되돌림을 Gemini와 동일하게 검토합니다. "
            "early의 확정 5m 범위 이탈/회복과 1H 모멘텀·무효화·비용 후 첫 익절 공간이 타당하면 "
            "일봉이 반대여도 approve_now를 검토합니다. SHORT_LEVEL=NONE 자체는 wait 이유가 아닙니다. "
            "extended/established인데 새 되돌림/재출발 근거가 없으면 모든 TF 정렬만으로 승인하지 마세요. "
            "RSI 극단값 하나로 초기 추세를 놓치거나 실제 반등을 무시하지 마세요. 롱/숏에 대칭 적용합니다. "
            "보호가격 계약을 만족시키려고 도달 근거가 없는 TP를 멀리 늘려 기대수익으로 제시하지 마세요. "
            "첫 익절 가격대와 계획 보유시간이 실제 구조에 맞는지 확인합니다. "
            "approve_now 뒤 새 시장조건 veto는 없습니다.\n"
        )
        prompt += (
            "\n\n[CORE 진입 관찰값 - GPT 최종 시장판단용]\n"
            "Gemini 후보와 당신의 approve_now가 CORE 진입 시장판단을 확정합니다. "
            "아래 로컬 조건은 참고정보이며 자동 진입차단 조건이 아닙니다. "
            "SHORT_LEVEL=NONE, 혼조 TF, 시간대, 과거 손실그룹만으로 결론을 강제하지 마세요. "
            "현재 방향과 즉시 진입 타이밍을 직접 검증하세요. "
            "approve_now 뒤 추가 0.80 확신도 조건은 없습니다.\n"
            + json.dumps(gemini_decision.get("_core_entry_observations") or {},
                         ensure_ascii=False, default=str)
        )

    market_meta = gemini_decision.get("_market_context") or {}
    if market_meta.get("snapshot_id"):
        log.info("[%s] MARKET_CONTEXT_INPUT model=gpt purpose=%s snapshot=%s status=%s", symbol, purpose,
                 market_meta["snapshot_id"], market_meta.get("status"))
    _ensure_response_models_warmed_up()
    client = _get_client(cfg)
    client_options = {}
    if timeout is not None:
        client_options["timeout"] = timeout
    if max_retries is not None:
        client_options["max_retries"] = max_retries
    if client_options:
        client = client.with_options(**client_options)

    request_start_dt = datetime.datetime.now()
    request_start_iso = request_start_dt.isoformat(timespec="milliseconds")
    _start_monotonic = time.monotonic()

    def _log_latency(timed_out: bool, error_reason: str | None,
                      prompt_tokens: int | None = None, completion_tokens: int | None = None) -> float:
        # 반환값(elapsed_ms)은 호출부가 result["response_ms"]에 그대로 실어서 trader.py의
        # 진입 로그(section 17: "GPT_RESPONSE_MS")에 쓸 수 있게 한다 - purpose와
        # 무관하게 항상 계산한다(값 하나 계산일 뿐, 별도 동작을 추가하는 게 아니다).
        elapsed_ms = (time.monotonic() - _start_monotonic) * 1000
        # 디스크 기록(gpt_latency_log)은 purpose="entry_gate"일 때만 한다(2026-08-29,
        # 사용자 지시 - 이번 작업 범위는 Entry Gate뿐, Shadow/Hold Audit은 건드리지
        # 않음). fail-open: 로깅 자체가 실패해도 실제 검증 결과 반환에는 영향 없음.
        if purpose == "entry_gate":
            try:
                gpt_latency_log.record_call(
                    cfg.user_dir, symbol=symbol, purpose=purpose, model=cfg.OPENAI_MODEL,
                    request_start=request_start_iso, response_ms=elapsed_ms,
                    timed_out=timed_out, retry_count=None,
                retry_limit=(max_retries if max_retries is not None else getattr(client, "max_retries", None)),
                    error_reason=error_reason, prompt_chars=len(prompt),
                    prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                )
            except Exception:
                log.exception("[%s] GPT latency 로그 기록 실패", symbol)
        return elapsed_ms

    try:
        response = client.chat.completions.create(
            model=cfg.OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
        )
    except Exception as exc:
        internal_reason = _classify_openai_error(exc)
        canonical_reason = _canonical_error_reason(internal_reason)
        log.warning("[%s] GPT 검증 API 호출 실패 reason=%s error_type=%s timeout=%s",
                    symbol,canonical_reason,type(exc).__name__,timeout)
        elapsed_ms = _log_latency(timed_out=(internal_reason == "timeout"), error_reason=canonical_reason)
        body = getattr(exc,"body",None)
        error = body.get("error",body) if isinstance(body,dict) else {}
        code = error.get("code") if isinstance(error,dict) else None
        category = ("timeout" if isinstance(exc,openai.APITimeoutError) else
                    "authentication_error" if isinstance(exc,openai.AuthenticationError) else
                    "billing_error" if code in ("insufficient_quota","billing_hard_limit_reached") else
                    "rate_limit" if isinstance(exc,openai.RateLimitError) else
                    "server_error" if isinstance(exc,openai.InternalServerError) else canonical_reason)
        return {"decision": "TIMEOUT" if internal_reason == "timeout" and purpose == "entry_gate" else None,
                "confidence":None,"reasoning":"","error_reason":canonical_reason,
                "error_type":type(exc).__name__,"error_category":category,
                "http_status":getattr(exc,"status_code",None),"response_ms":elapsed_ms,
                "request_purpose":purpose,"timeout_confirmed":isinstance(exc,openai.APITimeoutError)}

    usage = getattr(response, "usage", None)
    usage_prompt_tokens = usage_completion_tokens = None
    if usage is not None:
        usage_prompt_tokens = usage.prompt_tokens
        usage_completion_tokens = usage.completion_tokens
        try:
            usage_log.record_usage(
                cfg.user_dir,
                symbol,
                usage.prompt_tokens or 0,
                usage.completion_tokens or 0,
                provider="openai", purpose=purpose,
                **usage_log.response_metadata(response, model=cfg.OPENAI_MODEL, prompt=prompt,
                                              retry_limit=getattr(client, "max_retries", None)),
            )
        except Exception:
            log.exception("[%s] GPT 토큰 사용량 기록 실패", symbol)

    try:
        text = (response.choices[0].message.content or "").strip()
    except (AttributeError,IndexError,TypeError):
        text = ""
    if not text:
        elapsed_ms=_log_latency(timed_out=False,error_reason="empty_response",
                               prompt_tokens=usage_prompt_tokens,completion_tokens=usage_completion_tokens)
        return {"decision":None,"confidence":None,"reasoning":"","error_reason":"empty_response",
                "error_type":"EmptyResponse","request_purpose":purpose,"timeout_confirmed":False,
                "response_ms":elapsed_ms}
    try:
        result = json.loads(text)
        if not isinstance(result,dict):
            raise ValueError("response_object_required")
    except (json.JSONDecodeError,ValueError):
        log.error("[%s] GPT 검증 응답 파싱 실패", symbol)
        elapsed_ms = _log_latency(timed_out=False, error_reason="parse_error",
                                   prompt_tokens=usage_prompt_tokens, completion_tokens=usage_completion_tokens)
        return {"decision": None, "confidence": None, "reasoning": "", "error_reason": "parse_error", "response_ms": elapsed_ms}

    decision = result.get("decision")
    if decision not in ("approve_now", "wait", "reject"):
        log.error("[%s] GPT 검증 응답에 유효하지 않은 decision 값: %s", symbol, result)
        elapsed_ms = _log_latency(timed_out=False, error_reason="parse_error",
                                   prompt_tokens=usage_prompt_tokens, completion_tokens=usage_completion_tokens)
        return {"decision": None, "confidence": None, "reasoning": "", "error_reason": "parse_error", "response_ms": elapsed_ms}
    result["decision"] = decision

    # confidence는 지금 approve_now/wait/reject 판정 자체를 바꾸지는 않지만(그 판단은
    # decision 필드가 결정), 그대로 두면 "high"/None/1.5/True 같은 값이 로그 포맷팅
    # (%.2f)이나 Shadow 기록에 그대로 흘러들어가 예외를 일으키거나 통계를 오염시킬 수
    # 있다. Gemini의 confidence를 검증하는 _confidence_ok()와 동일한 기준으로 걸러서
    # 유효하지 않으면 None으로 안전하게 대체한다.
    confidence = result.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not (0.0 <= confidence <= 1.0):
        log.warning("[%s] GPT 검증 응답의 confidence 값이 유효하지 않음(%r) - None으로 대체", symbol, confidence)
        confidence = None
    result["confidence"] = confidence

    verdict = result.get("exit_plan_decision")
    # exit_plan_only is intentionally independent from entry timing/direction opinion.
    # Candidate C deterministic rules already own admission; GPT only reviews prices.
    if purpose != "exit_plan_only" and decision != "approve_now":
        verdict = "not_applicable"
    elif verdict not in ("approve", "revise", "reject", "not_applicable"):
        verdict = "not_applicable"
    try:
        from adaptive_exit_engine import normalize_ai_price_plan
        normalized_exit_plan = normalize_ai_price_plan(result.get("exit_plan"))
        gemini_exit_plan = normalize_ai_price_plan((gemini_decision or {}).get("exit_plan"))
    except Exception:
        normalized_exit_plan = None
        gemini_exit_plan = None
    # Price approval is executable only when it satisfies the same deterministic bounds used later.
    if exit_price_contract:
        verdict, normalized_exit_plan, contract_reason = _apply_exit_contract_to_verdict(
            verdict, gemini_exit_plan, normalized_exit_plan, exit_price_contract)
        result["exit_plan_contract_reason"] = contract_reason
    else:
        if verdict == "approve" and gemini_exit_plan is None and normalized_exit_plan is not None:
            verdict = "revise"
        if verdict == "revise" and normalized_exit_plan is None:
            verdict = "reject"
        if verdict != "revise":
            normalized_exit_plan = None
    result["exit_plan_decision"] = verdict
    result["exit_plan"] = normalized_exit_plan
    result["error_reason"] = None

    result["response_ms"] = _log_latency(
        timed_out=False, error_reason=None,
        prompt_tokens=usage_prompt_tokens, completion_tokens=usage_completion_tokens,
    )
    return result


# Hold Audit 전용 프롬프트 - verify()의 EXECUTION_CONTEXT/PROMPT_TEMPLATE과 의도적으로
# 완전히 분리한다. verify()는 "Gemini의 판단을 검증"하는 역할이라 프롬프트에 Gemini의
# action/confidence/reasoning을 그대로 넣지만, Hold Audit은 "독립적인 두 번째 분석가"
# 역할이라 Gemini가 뭐라고 판단했는지 자체를 몰라야 한다. 이 템플릿에는 Gemini 관련
# placeholder가 아예 없다 - verify_hold_audit()도 gemini_decision 파라미터를 받지 않으므로
# 구조적으로 Gemini의 판단을 프롬프트에 흘려보낼 방법이 없다.
HOLD_AUDIT_PROMPT_TEMPLATE = """당신은 암호화폐 선물 트레이딩의 독립적인 두 번째 분석가입니다.
다른 AI의 판단을 검토하는 역할이 아니라, 아래 시장 데이터만 보고 완전히 독립적으로 지금
이 심볼에 신규 진입할 가치가 있는지 스스로 평가하세요. 다른 AI의 판단은 제공되지
않습니다 - 당신 자신의 분석만으로 결론을 내리세요.

{symbol} 현재 포지션: 없음 (신규 진입 여부만 검토하면 됩니다 - 청산/보유 판단은 대상이 아닙니다)

검토 타임프레임 우선순위: 1순위 1일봉(1D)=시장 전체 레짐, 2순위 4시간봉(4H)=주 추세,
3순위 1시간봉(1H)=추세 유지 확인, 4순위 5분봉/3분봉=진입 타이밍, 5순위 1분봉=미세 확인.

검토 타임프레임: {timeframes_desc}

{candle_summary}

위 데이터만 보고 지금 즉시 신규 진입할 가치가 있는지 독립적으로 판단하세요. 방향이
맞아 보여도 지금 당장의 타이밍이 불충분하면(이미 급등/급락해서 늦었거나, 상위 추세와
하위 타이밍이 아직 정렬되지 않은 경우 등) no_entry를 선택하세요.

반드시 아래 JSON 형식으로만 답하세요. 다른 텍스트는 포함하지 마세요.
{{
  "action": "long" | "short" | "no_entry",
  "confidence": 0.0에서 1.0 사이 숫자,
  "reasoning": "판단 근거를 1~2문장으로"
}}
"""


def verify_hold_audit(
    cfg, symbol: str, tf_list: list, candle_summary: str, timeout: float | None = None,
) -> dict | None:
    """GPT Hold Audit(Shadow 전용 실험). Gemini가 hold라고 판단해 신규진입 게이트
    (verify())가 아예 호출되지 않는 상황에서, GPT가 Gemini의 판단을 전혀 보지 않고 시장
    데이터만으로 독립적으로 신규 진입 여부를 재검토한다.

    verify()와 의도적으로 완전히 분리된 함수다 - 이 함수는 gemini_decision이나 position
    파라미터 자체를 받지 않으므로(Hold Audit 후보는 항상 포지션 없음 상태) 구조적으로
    Gemini의 판단을 프롬프트에 전달할 방법이 없다.

    이 결과는 절대 실제 주문에 연결되지 않는다 - 호출부(trader._run_hold_audit)는 이
    결과를 gpt_hold_audit 로그에만 기록하고, _execute_entry를 비롯한 그 어떤 주문 함수도
    호출하지 않는다.

    OPENAI_API_KEY가 없으면 조용히 None을 반환한다(호출부가 이 경우 애초에 호출 전에
    이미 걸러냄). API 호출/응답 파싱이 실패하면 {"action": None, "confidence": None,
    "reasoning": "", "error_reason": "timeout"|"api_error"|"parse_error"|"sdk_error"|
    "unknown"}을 반환한다 - Shadow 전용 기능이라 어느 경우든 실거래에는 아무 영향이 없고,
    error_reason은 관측/로그용 원인 구분일 뿐이다."""
    if not cfg.OPENAI_API_KEY:
        return None

    log = cfg.logger or logger

    prompt = HOLD_AUDIT_PROMPT_TEMPLATE.format(
        symbol=symbol,
        timeframes_desc=", ".join(timeframes.label(tf) for tf in tf_list),
        candle_summary=candle_summary,
    )

    _ensure_response_models_warmed_up()
    client = _get_hold_audit_client(cfg)
    if timeout is not None:
        client = client.with_options(timeout=timeout)
    try:
        response = client.chat.completions.create(
            model=cfg.OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
        )
    except Exception as exc:
        internal_reason = _classify_openai_error(exc)
        log.exception(
            "[%s] GPT Hold Audit API 호출 실패(원인=%s, timeout=%s)", symbol, internal_reason, timeout,
        )
        return {"action": None, "confidence": None, "reasoning": "", "error_reason": _canonical_error_reason(internal_reason)}

    usage = getattr(response, "usage", None)
    if usage is not None:
        try:
            usage_log.record_usage(
                cfg.user_dir,
                symbol,
                usage.prompt_tokens or 0,
                usage.completion_tokens or 0,
                provider="openai",
                purpose="hold_audit",
                **usage_log.response_metadata(response, model=cfg.OPENAI_MODEL, prompt=prompt,
                                              retry_limit=getattr(client, "max_retries", None)),
            )
        except Exception:
            log.exception("[%s] GPT Hold Audit 토큰 사용량 기록 실패", symbol)

    text = (response.choices[0].message.content or "").strip()
    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        log.error("[%s] GPT Hold Audit 응답 파싱 실패: %s", symbol, text)
        return {"action": None, "confidence": None, "reasoning": "", "error_reason": "parse_error"}

    action = result.get("action")
    if action not in ("long", "short", "no_entry"):
        log.error("[%s] GPT Hold Audit 응답에 유효하지 않은 action 값: %s", symbol, result)
        return {"action": None, "confidence": None, "reasoning": "", "error_reason": "parse_error"}
    result["action"] = action

    confidence = result.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not (0.0 <= confidence <= 1.0):
        # verify()(Shadow/Entry Gate)와 다르게, Hold Audit은 action만 살리고 confidence만
        # None으로 대체해서 돌려주지 않는다 - 그렇게 하면 gpt_hold_audit 로그에
        # gpt_action="long"/gpt_confidence=None으로 남아 summary()의 long/short/no_entry
        # 집계에 "GPT가 사실상 답을 못 한" 응답이 정상 판단인 것처럼 섞여 들어가, 나중에
        # "GPT가 놓친 기회를 얼마나 잘 찾았는지" 분석할 표본을 오염시킨다. Hold Audit은
        # 실거래에 영향이 없는 순수 관찰 실험이라 confidence가 무효면 응답 전체를 실패로
        # 보고 None을 반환한다(호출부가 error로 기록해 no_verdict/error_count에 잡히게 함).
        log.warning(
            "[%s] GPT Hold Audit 응답의 confidence 값이 유효하지 않음(%r) - 응답 전체를 무효 처리",
            symbol, confidence,
        )
        return {"action": None, "confidence": None, "reasoning": "", "error_reason": "parse_error"}
    result["confidence"] = confidence
    result["error_reason"] = None

    return result


# 보유 포지션 AI 관리(2026-09-11) 전용 프롬프트 - verify()/HOLD_AUDIT_PROMPT_TEMPLATE
# 어느 쪽과도 다르다. verify()는 Gemini의 신규 진입/청산 "행동"을 approve_now/wait/
# reject로 검증하고, Hold Audit은 Gemini의 판단 자체를 보지 않은 완전 독립 재평가다.
# 이 프롬프트는 반대로 Gemini의 포지션 재점검 결과(gemini_analyzer.analyze_held_position)
# 를 GPT에게 그대로 보여주고, 그 위에서 실제로 포지션에 손댈지(HOLD/REDUCE_50/
# CLOSE_ALL) 최종 게이트 역할만 한다 - "이 포지션을 계속 들고 있어도 되는가"에 대한
# 마지막 검토자다.
POSITION_MANAGEMENT_PROMPT_TEMPLATE = """당신은 암호화폐 선물 트레이딩의 리스크 관리
최종 검토자입니다. 아래 포지션은 이미 보유 중이고, 트레이더 AI(Gemini)는 방금 이
포지션을 유지(hold)하기로 판단했습니다. 같은 AI가 그 판단의 근거를 스스로 재점검한
결과도 함께 제공됩니다. 당신은 이 포지션을 실제로 어떻게 처리할지 최종 결정합니다.

검토 타임프레임: {timeframes_desc}

{candle_summary}

보유 포지션: {position_desc}

트레이더 AI(Gemini)의 이번 hold 판단 근거: {gemini_reasoning}

트레이더 AI(Gemini)의 포지션 재점검 결과:
- assessment: {review_assessment} (확신도 {review_confidence})
- reasoning: {review_reasoning}

이 판단은 아직 실행되지 않았습니다 - 당신의 답변이 실제로 포지션을 얼마나 줄이거나
늘릴지 결정합니다. 신중하게 판단하세요. 어중간하게 "조금 더 지켜보자"는 의미로
confidence를 낮게 주지 말고, 아래 네 가지 중 지금 이 포지션에 대한 입장을 명확히
하나만 고르세요.

반드시 아래 JSON 형식으로만 답하세요. 다른 텍스트는 포함하지 마세요.
{{
  "action": {action_schema},
  "confidence": 0.0에서 1.0 사이 숫자,
  "reasoning": "판단 근거를 1~2문장으로"
}}

{action_guide}
"""


def verify_position_management(
    cfg, symbol: str, tf_list: list, candle_summary: str, position: dict, gemini_decision: dict,
    gemini_review: dict, timeout: float | None = None, max_retries: int | None = None,
    protection: dict | None = None,
    allowed_actions: tuple[str, ...] | None = None,
    purpose: str = "position_ai_review",
    review_path: str = "general",
) -> dict | None:
    """보유 포지션 AI 관리(2026-09-11) 최종 게이트 - Gemini의 hold 판단 + Gemini
    자신의 재점검(gemini_review) + 원본 시장 데이터를 GPT에게 보여주고, 실제로
    실행할 수 있는 HOLD/REDUCE_50/CLOSE_ALL/ADD_POSITION을 최종 결정하게 한다.

    OPENAI_API_KEY가 없으면 조용히 None을 반환한다(호출부가 이미 걸러냄). API 호출/
    파싱 실패 시 {"action": None, "confidence": None, "reasoning": "",
    "error_reason": "timeout"|"api_error"|"parse_error"|"sdk_error"|"unknown"}을
    반환한다 - 호출부(trader._handle_position_ai_review)는 action이 None이면 반드시
    HOLD로 fail-closed 처리하고 기존 거래소 SL/TP를 그대로 둔다.

    protection(2026-09-15, 사용자 직접 지시 - ADD_POSITION 기능 선행 작업) -
    gemini_analyzer.analyze_held_position()과 동일한 이유·동일한 계산으로 현재가·
    손절가·손절까지 남은 거리를 position_desc에 추가한다(둘 다 원래 이 정보를 전혀
    몰랐다 - 의도적으로 두 파일에 같은 계산을 따로 둔다, 이 파일의 다른 프롬프트들과
    같은 기존 관례)."""
    if not cfg.OPENAI_API_KEY:
        return None

    log = cfg.logger or logger

    position_desc = (
        f"{position['side']} {position['contracts']} 계약, "
        f"미실현손익 {position['unrealized_pnl']:.2f} USDT"
    )
    risk_context = format_position_risk_context(position, protection)
    if risk_context:
        position_desc += f", {risk_context}"

    if review_path == 'negative_guard' and allowed_actions is None:
        allowed_actions = ('HOLD', 'REDUCE_50')
        purpose = 'negative_guard'
    permitted_actions = tuple(allowed_actions or ("HOLD", "REDUCE_50", "CLOSE_ALL", "ADD_POSITION"))
    action_schema = " | ".join(f'"{action}"' for action in permitted_actions)
    action_guides = {
        "HOLD": '- "HOLD": keep the current position and existing exchange SL/TP.',
        "REDUCE_50": '- "REDUCE_50": reduce one stage, equal to 25% of the initial position size.',
        "CLOSE_ALL": '- "CLOSE_ALL": close the entire current position because its thesis is invalidated.',
        "ADD_POSITION": '- "ADD_POSITION": add exposure only in the separately permitted averaging path; never loosen the stop.',
    }
    action_guide = "\n".join(action_guides[action] for action in permitted_actions)
    if permitted_actions == ("HOLD", "REDUCE_50"):
        action_guide += "\nDo not choose CLOSE_ALL or ADD_POSITION in this review path."
    elif permitted_actions == ("HOLD", "REDUCE_50", "CLOSE_ALL"):
        action_guide += "\nThis is an exit-risk escalation. Do not choose ADD_POSITION."


    prompt = POSITION_MANAGEMENT_PROMPT_TEMPLATE.format(
        timeframes_desc=", ".join(timeframes.label(tf) for tf in tf_list),
        candle_summary=candle_summary,
        position_desc=position_desc,
        gemini_reasoning=gemini_decision.get("reasoning", ""),
        review_assessment=gemini_review.get("assessment"),
        review_confidence=gemini_review.get("confidence"),
        review_reasoning=gemini_review.get("reasoning", ""),
        action_schema=action_schema,
        action_guide=action_guide,
    )

    _ensure_response_models_warmed_up()
    client = _get_client(cfg)
    client_options = {}
    if timeout is not None:
        client_options["timeout"] = timeout
    if max_retries is not None:
        client_options["max_retries"] = max_retries
    if client_options:
        client = client.with_options(**client_options)

    request_start_dt = datetime.datetime.now()
    request_start_iso = request_start_dt.isoformat(timespec="milliseconds")
    _start_monotonic = time.monotonic()

    def _log_latency(timed_out: bool, error_reason: str | None,
                      prompt_tokens: int | None = None, completion_tokens: int | None = None) -> float:
        elapsed_ms = (time.monotonic() - _start_monotonic) * 1000
        try:
            gpt_latency_log.record_call(
                cfg.user_dir, symbol=symbol, purpose=purpose, model=cfg.OPENAI_MODEL,
                request_start=request_start_iso, response_ms=elapsed_ms,
                timed_out=timed_out, retry_count=None,
                retry_limit=(max_retries if max_retries is not None else getattr(client, "max_retries", None)),
                error_reason=error_reason, prompt_chars=len(prompt),
                prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
            )
        except Exception:
            log.exception("[%s] 보유 포지션 AI 관리(GPT) latency 로그 기록 실패", symbol)
        return elapsed_ms

    try:
        response = client.chat.completions.create(
            model=cfg.OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
        )
    except Exception as exc:
        internal_reason = _classify_openai_error(exc)
        canonical_reason = _canonical_error_reason(internal_reason)
        log.exception("[%s] 보유 포지션 AI 관리(GPT) 호출 실패(원인=%s, timeout=%s)", symbol, internal_reason, timeout)
        elapsed_ms = _log_latency(timed_out=(internal_reason == "timeout"), error_reason=canonical_reason)
        return {"action": None, "confidence": None, "reasoning": "", "error_reason": canonical_reason, "response_ms": elapsed_ms}

    usage = getattr(response, "usage", None)
    usage_prompt_tokens = usage_completion_tokens = None
    if usage is not None:
        usage_prompt_tokens = usage.prompt_tokens
        usage_completion_tokens = usage.completion_tokens
        try:
            usage_log.record_usage(
                cfg.user_dir, symbol, usage.prompt_tokens or 0, usage.completion_tokens or 0,
                provider="openai", purpose=purpose,
                **usage_log.response_metadata(response, model=cfg.OPENAI_MODEL, prompt=prompt,
                                              retry_limit=getattr(client, "max_retries", None)),
            )
        except Exception:
            log.exception("[%s] 보유 포지션 AI 관리(GPT) 토큰 사용량 기록 실패", symbol)

    text = (response.choices[0].message.content or "").strip()
    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        log.error("[%s] 보유 포지션 AI 관리(GPT) 응답 파싱 실패: %s", symbol, text)
        elapsed_ms = _log_latency(timed_out=False, error_reason="parse_error",
                                   prompt_tokens=usage_prompt_tokens, completion_tokens=usage_completion_tokens)
        return {"action": None, "confidence": None, "reasoning": "", "error_reason": "parse_error", "response_ms": elapsed_ms}

    action = result.get("action")
    if action not in permitted_actions:
        log.error("[%s] 보유 포지션 AI 관리(GPT) 응답에 유효하지 않은 action 값: %s", symbol, result)
        elapsed_ms = _log_latency(timed_out=False, error_reason="parse_error",
                                   prompt_tokens=usage_prompt_tokens, completion_tokens=usage_completion_tokens)
        return {"action": None, "confidence": None, "reasoning": "", "error_reason": "parse_error", "response_ms": elapsed_ms}
    result["action"] = action

    confidence = result.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not (0.0 <= confidence <= 1.0):
        log.warning("[%s] 보유 포지션 AI 관리(GPT) 응답의 confidence 값이 유효하지 않음(%r) - None으로 대체", symbol, confidence)
        confidence = None
    result["confidence"] = confidence
    result["error_reason"] = None

    result["response_ms"] = _log_latency(
        timed_out=False, error_reason=None,
        prompt_tokens=usage_prompt_tokens, completion_tokens=usage_completion_tokens,
    )
    return result

# Advisory strategy-learning review. This path has no execution authority.
STRATEGY_REVIEW_FIELDS = (
    'observations','positive_patterns','negative_risk_patterns','data_quality_warnings',
    'hypotheses_to_continue_watching','proposed_operator_actions','confidence',
)
STRATEGY_REVIEW_PROMPT_TEMPLATE = """You are reviewing historical crypto-futures trading performance.
This is advisory analysis only. Do not change trading settings, do not place or recommend an order command,
and do not claim causality from correlation. Review the supplied deterministic snapshot independently.
Return JSON only with these fields: observations, positive_patterns, negative_risk_patterns,
data_quality_warnings, hypotheses_to_continue_watching, proposed_operator_actions, confidence.
All fields except confidence are arrays of concise strings; confidence is 0.0..1.0.
Snapshot:\n{snapshot}\n"""


def review_strategy_report(cfg, snapshot: dict) -> dict:
    """Report-only GPT review; caller owns all scheduling and failure containment."""
    prompt = STRATEGY_REVIEW_PROMPT_TEMPLATE.format(
        snapshot=json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    )
    _ensure_response_models_warmed_up()
    client = _get_client(cfg)
    response = client.chat.completions.create(
        model=cfg.OPENAI_MODEL,
        messages=[{'role':'user','content':prompt}],
        response_format={'type':'json_object'},
    )
    usage = getattr(response, 'usage', None)
    if usage is not None:
        try:
            usage_log.record_usage(
                cfg.user_dir, 'STRATEGY_REVIEW', usage.prompt_tokens or 0,
                usage.completion_tokens or 0, provider='openai', purpose='strategy_review',
                **usage_log.response_metadata(response, model=cfg.OPENAI_MODEL, prompt=prompt,
                                              retry_limit=getattr(client, "max_retries", None)),
            )
        except Exception:
            (cfg.logger or logger).exception('OpenAI strategy review usage logging failed')
    result = json.loads((response.choices[0].message.content or '').strip())
    if not isinstance(result, dict):
        raise ValueError('strategy_review_not_object')
    return result
