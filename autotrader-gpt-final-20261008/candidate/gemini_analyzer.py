import json
import time
import logging
import os
import traceback

from google import genai
from google.genai import types

import timeframes
from position_risk_context import format_position_risk_context
import usage_log

logger = logging.getLogger("trader.gemini")
GEMINI_REQUEST_TIMEOUT_MS = 20_000

EXIT_PRICE_SCHEMA = {
    "type": "OBJECT",
    "nullable": True,
    "properties": {
        "stop_loss_price": {"type": "NUMBER", "nullable": True, "description": "Scenario invalidation price."},
        "take_profit_1_price": {"type": "NUMBER", "nullable": True, "description": "First meaningful profit-taking price."},
        "take_profit_2_price": {"type": "NUMBER", "nullable": True, "description": "Optional trend-extension target price."},
        "confidence": {"type": "NUMBER", "nullable": True, "minimum": 0, "maximum": 1},
        "reasoning": {"type": "STRING", "description": "Concise Korean price rationale in 1-2 sentences."},
    },
    "required": ["stop_loss_price", "take_profit_1_price", "take_profit_2_price", "confidence", "reasoning"],
}

GEMINI_DECISION_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "action": {"type": "STRING", "enum": ["long", "short", "close", "hold"]},
        "confidence": {"type": "NUMBER", "minimum": 0, "maximum": 1},
        "market_regime": {"type": "STRING", "enum": ["bullish", "bearish", "neutral", "transition"]},
        "regime_confidence": {"type": "NUMBER", "minimum": 0, "maximum": 1},
        "trade_alignment": {"type": "STRING", "enum": ["with_regime", "counter_regime", "neutral"]},
        "exit_plan": EXIT_PRICE_SCHEMA,
        "reasoning": {"type": "STRING", "description": "Concrete Korean decision rationale in 2-3 concise sentences."},
    },
    "required": ["action", "confidence", "market_regime", "regime_confidence", "trade_alignment", "exit_plan", "reasoning"],
}

ENTRY_EXIT_PLAN_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "confidence": {"type": "NUMBER", "minimum": 0, "maximum": 1},
        "exit_plan": EXIT_PRICE_SCHEMA,
        "reasoning": {"type": "STRING", "description": "Concise Korean overall price-plan rationale."},
    },
    "required": ["confidence", "exit_plan", "reasoning"],
}

POSITION_REVIEW_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "assessment": {"type": "STRING", "enum": ["thesis_intact", "weakening", "invalidated"]},
        "confidence": {"type": "NUMBER", "minimum": 0, "maximum": 1},
        "reasoning": {"type": "STRING", "description": "Concrete Korean thesis review in 2-3 concise sentences."},
    },
    "required": ["assessment", "confidence", "reasoning"],
}


def _generate_content_observed(cfg,symbol,purpose,**request):
    import httpx
    started=time.monotonic()
    error=None
    try:
        return _get_client(cfg).models.generate_content(**request)
    except Exception as exc:
        error=exc
        raise
    finally:
        usage_log.record_api_attempt(cfg.user_dir,symbol,'gemini',purpose,cfg.GEMINI_MODEL,
            response_ms=(time.monotonic()-started)*1000,
            timed_out=isinstance(error,(TimeoutError,httpx.TimeoutException)),
            error_type=type(error).__name__ if error is not None else None)


def _get_client(cfg) -> genai.Client:
    # 계정마다 Gemini 키가 다르므로 클라이언트도 cfg 인스턴스에 붙여서 계정별로 캐싱한다.
    client = getattr(cfg, "_gemini_client", None)
    if client is None:
        # 보유 포지션 손실 방어와 30초 위험 점검이 네트워크 무한 대기에 묶이지 않도록
        # 모든 Gemini 요청에 명시적 상한을 둔다.
        client = genai.Client(
            api_key=cfg.GEMINI_API_KEY,
            http_options=types.HttpOptions(timeout=GEMINI_REQUEST_TIMEOUT_MS),
        )
        cfg._gemini_client = client
    return client


def reset_client(cfg) -> None:
    """API 키가 바뀌었을 때 캐시된 클라이언트를 버려서 다음 호출이 새 키를 쓰게 한다."""
    cfg._gemini_client = None


def validate_key(api_key: str) -> tuple[bool, str]:
    """Gemini API 키가 실제로 인증되는지 확인한다."""
    try:
        import certifi

        logger.info(
            "Gemini 검증 진단: certifi.where()=%s exists=%s SSL_CERT_FILE=%s cwd=%s HOME=%s",
            certifi.where(),
            os.path.exists(certifi.where()),
            os.environ.get("SSL_CERT_FILE"),
            os.getcwd(),
            os.environ.get("HOME"),
        )
    except Exception:
        logger.exception("Gemini 검증 진단 정보 수집 실패")

    try:
        client = genai.Client(api_key=api_key)
        list(client.models.list())
        return True, ""
    except Exception as exc:
        logger.error("Gemini 키 검증 실패 전체 traceback:\n%s", traceback.format_exc())
        return False, str(exc)


PROMPT_TEMPLATE = """당신은 암호화폐 선물 트레이딩 분석가입니다.
아래는 {symbol}의 여러 타임프레임 캔들 데이터와 보조지표입니다 (검토 타임프레임: {timeframes_desc}).

역할: 거래 방향과 지금 진입할 시점을 따로 판단한다. 1D/4H는 시장 배경과 반대 위험,
1H는 중간 구조, 확정 5m/3m은 실제 진입 촉발 근거다. 느린 EMA의 완전 정렬을 기다리는
것을 진입 근거로 삼지 않는다. 1m 한 봉만으로 반전하지 않는다.

진입 판단:
- CORE_ENTRY_TIMING의 origin_closed_at, age_minutes, move_from_origin_atr, phase,
  setup_kind를 먼저 읽는다. first_range_break는 처음 확정된 30분 가격범위 이탈,
  pullback_resume는 실제 되돌림 뒤 직전 봉 범위 회복이다. 초기 근거이지 수익 보장은 아니다.
- early라면 일봉이 아직 반대 방향이더라도 확정 5m 범위 이탈/회복과 현재 1H 모멘텀,
  실제 무효화 가격·상대 지지저항까지 공간을 함께 검토해 long/short 후보를 지금 판단한다.
  1D/4H EMA의 완전 전환을 필수로 요구하거나 단순히 일봉 방향만으로 hold하지 않는다.
- established/extended의 오래된 움직임을 TF가 이제 모두 정렬됐다는 이유만으로 새
  초반 진입으로 설명하지 않는다. 이미 진행된 거리와 가까운 반대 가격대를 보며 새
  되돌림·재출발 근거가 없으면 hold한다. 하락 후 숏도 상승 후 롱과 완전히 대칭이다.
- RSI 과매수/과매도 하나는 자동 반전·관망 이유가 아니다. 가격이 구조를 계속 깨는지,
  실제 반등/되돌림이 있는지, 남은 공간과 손절이 합리적인지 검증한다.
- 최근 60분 최고/최저와 변화율은 이미 관찰한 값이며 다음 목표의 도달 가능성이나
  기대수익을 증명하지 않는다. 큰 TP 숫자로 손익비를 만들어내지 않는다. 첫 익절은
  현재 구조의 실제 가격대와 계획 보유시간에 맞추고, 비용 후 이익과 무효화를 함께 설명한다.
- CURRENT_POSITION이 있으면 신규진입의 early 조건을 보유/청산 조건에 적용하지 않는다.
  유효한 포지션은 유지하되 구조 무효화와 이익반납을 검토한다. 일봉이 반대라는 이유만으로
  이익 중인 추세 포지션을 정리하지 않는다.
- CORRECTION_ACTIVE=true는 추가 관찰자료다. 그것이 true여야만 초기 숏이 가능하다는 뜻이 아니다.
- 데이터 status=unknown이면 누락을 밝히고 available 차트로 판단한다. 없는 첫 신호나
  미래 가격경로를 만들어내지 않는다. reasoning에 진입 단계와 무효화/첫 이익 실현 근거를 적는다.

{candle_summary}

현재 포지션: {position_desc}

위 데이터를 바탕으로 다음 중 하나의 행동을 결정하세요:
- "long": 무포지션이면 롱 신규 진입. 현재 SHORT 보유 중이면 SHORT 청산 후 LONG 반전 의사표현
- "short": 무포지션이면 숏 신규 진입. 현재 LONG 보유 중이면 LONG 청산 후 SHORT 반전 의사표현
- "close": 현재 포지션은 청산하되 반대방향 신규진입 근거는 아직 부족함
- "hold": 아무 행동도 하지 않음

action이 long 또는 short이면 현재 멀티 타임프레임 구조를 바탕으로 실제 가격 기준의
exit_plan도 제안하세요. stop_loss_price는 현재 진입 시나리오가 무효화되는 가격,
take_profit_1_price는 첫 의미있는 익절 구간, take_profit_2_price는 추세 지속 시 최종
목표 구간입니다. 고정 퍼센트를 기계적으로 복사하지 말고 지지/저항·변동성·추세를
근거로 판단하세요. long이면 반드시 stop_loss_price < 현재가격 < take_profit_1_price 이고
take_profit_2_price가 있으면 take_profit_1_price 이하가 될 수 없습니다. short이면 반드시
stop_loss_price > 현재가격 > take_profit_1_price 이고 take_profit_2_price가 있으면
take_profit_1_price 이상이 될 수 없습니다. close/hold이면 세 가격은 null로 답하세요.

응답은 제공된 response schema와 일치하는 JSON만 반환하세요. reasoning은 지금 해당되는
근거를 2~3문장으로 구체적으로 쓰고, trade_alignment는 이번 행동이 시장 레짐과 같은
방향이면 with_regime, 반대면 counter_regime, 무포지션 hold처럼 방향성이 없으면 neutral로 답하세요.
"""


ENTRY_EXIT_PLAN_PROMPT_TEMPLATE = """당신은 암호화폐 선물의 보호주문 가격 분석가입니다.
Candidate C의 진입 방향은 규칙 엔진이 이미 확정했으며 당신은 방향을 바꾸거나 진입 여부를
판단하지 않습니다. 오직 {symbol} {side} 신규진입의 실제 가격 기준 SL/TP를 제안하세요.
검토 타임프레임: {timeframes_desc}

{candle_summary}

규칙 엔진 기준 보호가격(참고용): stop={baseline_stop}, target={baseline_target}
{exit_price_contract}
현재 구조/변동성/지지저항을 사용해 고정 퍼센트를 기계적으로 복사하지 말고 가격을 제안하세요.
응답은 제공된 response schema와 일치하는 JSON만 반환하고 가격 근거는 1~2문장으로 간결하게 쓰세요.
"""


def propose_entry_exit_plan(cfg, symbol: str, side: str, tf_list: list, candle_summary: str,
                            baseline_stop: float | None, baseline_target: float | None,
                            exit_price_contract: str = "", *, manual_core: bool = False) -> dict:
    """Candidate C direction is immutable; Gemini proposes exit prices only."""
    log = cfg.logger or logger
    if side not in ("long", "short") or not getattr(cfg, "GEMINI_API_KEY", None):
        return {"action": side, "confidence": None, "exit_plan": None,
                "reasoning": "Gemini exit-price proposal unavailable"}
    template = ENTRY_EXIT_PLAN_PROMPT_TEMPLATE
    if manual_core:
        template = template.replace(
            "Candidate C의 진입 방향은 규칙 엔진이 이미 확정했으며",
            "CORE 수동진입 방향은 사용자가 직접 확정했으며")
    purpose = 'core_manual_exit_plan' if manual_core else 'candidate_exit_plan'
    prompt = template.format(
        symbol=symbol, side=side, timeframes_desc=", ".join(timeframes.label(tf) for tf in tf_list),
        candle_summary=candle_summary, baseline_stop=baseline_stop, baseline_target=baseline_target,
        exit_price_contract=exit_price_contract or "",
    )
    client = _get_client(cfg)
    response = _generate_content_observed(cfg, symbol, purpose,
        model=cfg.GEMINI_MODEL, contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=ENTRY_EXIT_PLAN_SCHEMA,
            thinking_config=types.ThinkingConfig(thinking_level="low"),
        ),
    )
    usage = getattr(response, "usage_metadata", None)
    if usage is not None:
        try:
            usage_log.record_usage(
                cfg.user_dir, symbol, usage.prompt_token_count or 0,
                (usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0),
                purpose=purpose,
                **usage_log.response_metadata(response, model=cfg.GEMINI_MODEL, prompt=prompt),
            )
        except Exception:
            log.exception("Candidate C Gemini exit-plan 토큰 사용량 기록 실패")
    try:
        payload = json.loads((response.text or "").strip())
    except Exception:
        log.exception("[%s] Candidate C Gemini exit-plan 파싱 실패", symbol)
        return {"action": side, "confidence": None, "exit_plan": None,
                "reasoning": "Gemini exit-plan parse failure"}
    try:
        from adaptive_exit_engine import normalize_ai_price_plan
        plan = normalize_ai_price_plan(payload.get("exit_plan"))
    except Exception:
        plan = None
    confidence = payload.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= float(confidence) <= 1:
        confidence = None
    return {"action": side, "confidence": confidence, "exit_plan": plan,
            "reasoning": str(payload.get("reasoning") or "")[:1000]}


def analyze(cfg, symbol: str, tf_list: list, candle_summary: str, position: dict | None) -> dict:
    if position:
        position_desc = (
            f"{position['side']} {position['contracts']} 계약, "
            f"진입가 {position['entry_price']:.2f}, "
            f"미실현손익 {position['unrealized_pnl']:.2f} USDT"
        )
    else:
        position_desc = "없음"

    prompt = PROMPT_TEMPLATE.format(
        symbol=symbol,
        timeframes_desc=", ".join(timeframes.label(tf) for tf in tf_list),
        candle_summary=candle_summary,
        position_desc=position_desc,
    )

    client = _get_client(cfg)
    response = _generate_content_observed(cfg, symbol, 'core_primary_decision',
        model=cfg.GEMINI_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=GEMINI_DECISION_SCHEMA,
            # Gemini 3.8 Flash는 thinking_budget 대신 thinking_level을 사용한다.
            # 실시간 매매 판단은 지연을 억제하면서도 3.8의 추론을 쓰도록 LOW로 고정한다.
            thinking_config=types.ThinkingConfig(thinking_level="low"),
        ),
    )

    log = cfg.logger or logger

    usage = getattr(response, "usage_metadata", None)
    if usage is not None:
        input_tokens = usage.prompt_token_count or 0
        # "생각(thinking)" 토큰도 실제로는 출력 토큰과 같이 과금되므로 합산한다.
        output_tokens = (usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0)
        try:
            usage_log.record_usage(
                cfg.user_dir, symbol, input_tokens, output_tokens, purpose="core_primary_decision",
                **usage_log.response_metadata(response, model=cfg.GEMINI_MODEL, prompt=prompt),
            )
        except Exception:
            log.exception("토큰 사용량 기록 실패")

    text = response.text.strip()
    try:
        decision = json.loads(text)
    except json.JSONDecodeError:
        log.error("Gemini 응답 파싱 실패: %s", text)
        return {"action": "hold", "confidence": 0.0, "reasoning": "파싱 실패로 대기"}

    action = decision.get("action", "hold")
    if action not in ("long", "short", "close", "hold"):
        action = "hold"
    decision["action"] = action

    # 신규진입용 AI SL/TP 제안은 실행가격 후보일 뿐이다. 여기서는 숫자 스키마만 엄격히
    # 정규화하고, 방향/ATR/R:R/리스크 검증은 Adaptive 엔진이 실제 진입 직전에 한다.
    try:
        from adaptive_exit_engine import normalize_ai_price_plan
        decision["exit_plan"] = normalize_ai_price_plan(decision.get("exit_plan"))
    except Exception:
        decision["exit_plan"] = None

    # market_regime은 정보용(참고) 필드라 매매 게이트에는 쓰지 않는다 - 응답에 없거나
    # 알 수 없는 값이어도 매매 로직 자체는 전혀 영향받지 않아야 한다(하위 호환).
    regime = decision.get("market_regime")
    if regime not in ("bullish", "bearish", "neutral", "transition"):
        regime = None
    decision["market_regime"] = regime

    # trade_alignment도 market_regime과 동일하게 참고/기록용이다 - 나중에 "역추세 거래가
    # 실제로 손실이었는지" 통계를 내기 위한 것이지 매매 게이트에는 쓰지 않는다.
    alignment = decision.get("trade_alignment")
    if alignment not in ("with_regime", "counter_regime", "neutral"):
        alignment = None
    decision["trade_alignment"] = alignment

    log.info(
        "[%s] Gemini 판단: %s (확신도 %.2f) / regime=%s (%.2f) - %s",
        symbol,
        action,
        decision.get("confidence", 0),
        regime or "-",
        decision.get("regime_confidence") or 0.0,
        decision.get("reasoning", ""),
    )
    return decision


# 보유 포지션 AI 관리(2026-09-11) 전용 프롬프트 - 위 analyze()의 PROMPT_TEMPLATE(신규
# 진입/청산 판단용)과 의도적으로 분리한다. 이 프롬프트의 역할은 "새로운 판단"이 아니라
# "이미 hold로 유지하기로 한 포지션의 최초 근거(gemini_decision.reasoning)가 지금도
# 여전히 유효한지" 자기 자신의 판단을 재점검하는 것이다 - long/short/hold 같은 행동을
# 고르지 않고, thesis_intact/weakening/invalidated 3단계 평가만 반환한다(실제 청산/감축
# 여부는 이 평가를 받아본 GPT가 최종 게이트로 결정한다 - openai_analyzer.
# verify_position_management 참고).
POSITION_REVIEW_PROMPT_TEMPLATE = """당신은 암호화폐 선물 트레이딩 분석가입니다.
아래 포지션은 이미 보유 중이며, 당신(같은 AI)이 조금 전 이 포지션을 유지(hold)하기로
판단했습니다. 지금은 새로운 진입/청산을 결정하는 것이 아니라, 그 hold 판단의 근거가
지금 이 시점에도 여전히 유효한지를 스스로 재점검하는 것입니다.

검토 타임프레임: {timeframes_desc}

{candle_summary}

보유 포지션: {position_desc}

방금 전 당신의 hold 판단 근거: {prior_reasoning}
(market_regime={prior_regime}, regime_confidence={prior_regime_confidence})

아래 세 가지 중 지금 상태에 가장 가까운 평가를 하나만 고르세요:
- "thesis_intact": 처음 포지션을 유지한 근거가 그대로 유효하다 (추세/구조에 뜻있는
  악화 신호 없음).
- "weakening": 근거가 완전히 무너지지는 않았지만 뚜렷하게 약화되고 있다 (일부 되돌림,
  모멘텀 둔화, 상위 타임프레임 대비 하위 타임프레임의 역행 등).
- "invalidated": 처음 포지션을 유지한 근거가 사실상 무너졌다 (추세 반전, 구조 붕괴,
  명확한 역행 신호).

응답은 제공된 response schema와 일치하는 JSON만 반환하고 판단 근거는 2~3문장으로 구체적으로 쓰세요.
"""




def parse_adaptive_exit_assessment(raw):
    """Parse advisory state; reject all executable price/order authority."""
    if raw is None:
        return None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None
    if not isinstance(raw, dict):
        return None
    forbidden = {"stop_price","sl_price","tp_price","target_price","quantity","contracts","leverage","risk_budget_usdt"}
    if forbidden.intersection(raw):
        return None
    thesis = raw.get("thesis_state", raw.get("assessment"))
    thesis = {"thesis_intact":"intact","intact":"intact","weakening":"weakening","invalidated":"invalidated"}.get(thesis)
    if thesis is None:
        return None
    confidence = raw.get("confidence")
    if confidence is not None:
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0.0 <= float(confidence) <= 1.0:
            return None
        confidence = float(confidence)
    trend = raw.get("trend_persistence", "medium")
    vol = raw.get("volatility_risk", "medium")
    extension = raw.get("target_extension", "neutral")
    if trend not in ("low", "medium", "high") or vol not in ("low", "medium", "high") or extension not in ("deny", "neutral", "allow"):
        return None
    from adaptive_exit_engine import GeminiExitAssessment
    return GeminiExitAssessment(thesis, confidence, trend, vol, extension, str(raw.get("reasoning") or ""))

def analyze_held_position(
    cfg, symbol: str, tf_list: list, candle_summary: str, position: dict, gemini_decision: dict,
    protection: dict | None = None, purpose: str = "position_ai_review",
) -> dict:
    """보유 포지션 AI 관리(2026-09-11) - Gemini가 방금 hold로 유지하기로 한 포지션의
    근거가 지금도 유효한지 스스로 재점검한다. 이 결과 자체는 실행 여부를 결정하지
    않는다 - GPT(openai_analyzer.verify_position_management)가 이 평가와 원본 데이터를
    함께 보고 최종 HOLD/REDUCE_50/CLOSE_ALL/ADD_POSITION을 판단한다.

    응답 파싱 실패 시 안전한 쪽(thesis_intact, confidence=0.0)으로 반환한다 - 이 함수
    자체가 실행을 막을 수 없으므로(GPT 게이트가 그 역할), 낮은 confidence로 GPT가
    참고하되 이 재점검이 뭔가 확신 있게 "무너졌다"고 말한 것처럼 오인되지 않게 한다.

    protection(2026-09-15, 사용자 직접 지시 - ADD_POSITION 기능 선행 작업) - 지금까지
    이 재점검은 진입가/미실현손익만 알고 현재가·손절가를 전혀 몰라서 "손절이 가까워지고
    있다" 같은 판단 자체가 원천적으로 불가능했다. client.fetch_current_protection()의
    결과를 그대로 넘기면(조회 실패 시 None - 기존처럼 그 정보 없이 진행, fail-open)
    현재가와 손절까지 남은 거리를 프롬프트에 추가한다."""
    position_desc = (
        f"{position['side']} {position['contracts']} 계약, "
        f"미실현손익 {position['unrealized_pnl']:.2f} USDT"
    )
    risk_context = format_position_risk_context(position, protection)
    if risk_context:
        position_desc += f", {risk_context}"

    prompt = POSITION_REVIEW_PROMPT_TEMPLATE.format(
        timeframes_desc=", ".join(timeframes.label(tf) for tf in tf_list),
        candle_summary=candle_summary,
        position_desc=position_desc,
        prior_reasoning=gemini_decision.get("reasoning", ""),
        prior_regime=gemini_decision.get("market_regime") or "-",
        prior_regime_confidence=gemini_decision.get("regime_confidence") or 0.0,
    )

    client = _get_client(cfg)
    response = _generate_content_observed(cfg, symbol, purpose,
        model=cfg.GEMINI_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=POSITION_REVIEW_SCHEMA,
            thinking_config=types.ThinkingConfig(thinking_level="low"),
        ),
    )

    log = cfg.logger or logger

    usage = getattr(response, "usage_metadata", None)
    if usage is not None:
        input_tokens = usage.prompt_token_count or 0
        output_tokens = (usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0)
        try:
            usage_log.record_usage(
                cfg.user_dir, symbol, input_tokens, output_tokens, purpose=purpose,
                **usage_log.response_metadata(response, model=cfg.GEMINI_MODEL, prompt=prompt),
            )
        except Exception:
            log.exception("[%s] 보유 포지션 AI 관리(Gemini 재점검) 토큰 사용량 기록 실패", symbol)

    text = response.text.strip()
    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        log.error("[%s] Gemini 포지션 재점검 응답 파싱 실패: %s", symbol, text)
        return {"assessment": "thesis_intact", "confidence": 0.0, "reasoning": "파싱 실패로 안전값 반환"}

    assessment = result.get("assessment")
    if assessment not in ("thesis_intact", "weakening", "invalidated"):
        log.error("[%s] Gemini 포지션 재점검 응답에 유효하지 않은 assessment 값: %s", symbol, result)
        return {"assessment": "thesis_intact", "confidence": 0.0, "reasoning": "유효하지 않은 응답으로 안전값 반환"}
    result["assessment"] = assessment

    confidence = result.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not (0.0 <= confidence <= 1.0):
        confidence = None
    result["confidence"] = confidence

    adaptive = parse_adaptive_exit_assessment({
        "thesis_state": assessment, "confidence": confidence,
        "trend_persistence": result.get("trend_persistence", "medium"),
        "volatility_risk": result.get("volatility_risk", "medium"),
        "target_extension": result.get("target_extension", "neutral"),
        "reasoning": result.get("reasoning", ""),
    })
    if adaptive is not None:
        result["trend_persistence"] = adaptive.trend_persistence
        result["volatility_risk"] = adaptive.volatility_risk
        result["target_extension"] = adaptive.target_extension

    log.info(
        "[%s] Gemini 포지션 재점검: %s (확신도 %s) - %s",
        symbol, assessment, confidence, result.get("reasoning", ""),
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
    """Report-only Gemini review; caller owns all scheduling and failure containment."""
    prompt = STRATEGY_REVIEW_PROMPT_TEMPLATE.format(
        snapshot=json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    )
    client = _get_client(cfg)
    response = _generate_content_observed(cfg, 'STRATEGY_REVIEW', 'strategy_review',
        model=cfg.GEMINI_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type='application/json',
            thinking_config=types.ThinkingConfig(thinking_level='low'),
        ),
    )
    usage = getattr(response, 'usage_metadata', None)
    if usage is not None:
        try:
            usage_log.record_usage(
                cfg.user_dir, 'STRATEGY_REVIEW', usage.prompt_token_count or 0,
                (usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0),
                purpose='strategy_review',
                **usage_log.response_metadata(response, model=cfg.GEMINI_MODEL, prompt=prompt),
            )
        except Exception:
            (cfg.logger or logger).exception('Gemini strategy review usage logging failed')
    result = json.loads((response.text or '').strip())
    if not isinstance(result, dict):
        raise ValueError('strategy_review_not_object')
    return result
