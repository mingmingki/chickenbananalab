import json
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

TF 우선순위: 1D=시장 레짐 > 4H=주추세 > 1H=추세 확인 > 5m/3m=진입 타이밍 > 1m=미세 확인.
원칙:
- 1D/4H 추세와 1m/3m 단기 신호가 충돌하면 상위 TF를 우선한다 - 1m/3m 단기 신호
  하나만으로는 신규 숏이나 기존 롱의 성급한 청산을 결정하지 말고 눌림/단기 조정으로
  해석한다. 하지만 이것이 "1D가 bullish이면 항상 hold"를 뜻하지는 않는다 - 1D는
  중요한 위험요인이지 그 자체로 자동 결론이 아니다.
- 아래가 복합적으로(단일 지표 하나만으로는 안 됨) 나타나면, 1D가 아직 완전히 꺾이지
  않았어도 "상위 추세 속 눌림"이라는 표현만으로 hold를 정당화하지 말고 청산/반전을
  적극 검토해야 한다: 1H 가격이 EMA20 아래에서 지속, 1H MACD 하락전환이 여러 구간
  지속, 5m·3m 모두 역배열+하락 모멘텀 지속, 직전 고점 회복 실패, 4H 모멘텀의 명확한 약화.
  이 조건들이 겹쳐 나타나는데도 reasoning이 "상위 상승 추세 속 눌림"이라는
  말만 반복하고 있다면, 그건 답을 정당화한 게 아니라 회피한 것이다 - 이 5가지 중
  실제로 몇 개가 지금 해당되는지, 해당되지 않는다면 왜 아직 단기 조정으로 보는지를
  reasoning에 구체적으로 밝혀라.
- 1D/4H는 방향을 제한하는 필터일 뿐 그 자체가 진입 신호는 아니다 - 실제 진입 타이밍은
  1H 확인 + 5m/3m 눌림목·재상승 신호로 판단하고, 1m 급등 하나만 보고 고점을 추격하지 않는다.
- 하락 레짐에서는 위 원칙을 롱/숏 반대로 대칭 적용한다.
- candle_summary에 [급등 후 조정 모드] CORRECTION_ACTIVE=true가 있으면, 최근 급등 뒤
  고점 되돌림 + 1H 모멘텀 약화 + 3m/5m 하락구조가 동시에 확인된 상태다. 이때는
  1D bullish를 근거로 신규 long 또는 기존 long hold를 자동 선택하지 마라. 무포지션이면
  counter_regime short를 적극 검토하고, long 보유 중이면 short 반전(reversal)을 유효한
  선택지로 검토한다. 반대로 하위 구조가 회복되었다면 short를 억지로 선택하지 않는다.

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

반드시 아래 JSON 형식으로만 답하세요. 다른 텍스트는 포함하지 마세요.
{{
  "action": "long" | "short" | "close" | "hold",
  "confidence": 0.0에서 1.0 사이 숫자,
  "market_regime": "bullish" | "bearish" | "neutral" | "transition",
  "regime_confidence": 0.0에서 1.0 사이 숫자 (market_regime 판단에 대한 확신도),
  "trade_alignment": "with_regime" | "counter_regime" | "neutral",
  "exit_plan": {{
    "stop_loss_price": 숫자 | null,
    "take_profit_1_price": 숫자 | null,
    "take_profit_2_price": 숫자 | null,
    "confidence": 0.0에서 1.0 사이 숫자 | null,
    "reasoning": "왜 이 손절/익절 가격이 적절한지 1~2문장"
  }},
  "reasoning": "판단 근거를 2~3문장으로"
}}
trade_alignment: 이번 action이 market_regime과 같은 방향이면 "with_regime"(예: bullish
레짐에서 long, 또는 bullish 레짐에서 기존 long을 hold/유지), 반대 방향이면
"counter_regime"(예: bullish 레짐에서 short 또는 기존 long을 close), 방향성이 없는
행동(hold+무포지션 등)이면 "neutral"로 답하세요.
"""


ENTRY_EXIT_PLAN_PROMPT_TEMPLATE = """당신은 암호화폐 선물의 보호주문 가격 분석가입니다.
Candidate C의 진입 방향은 규칙 엔진이 이미 확정했으며 당신은 방향을 바꾸거나 진입 여부를
판단하지 않습니다. 오직 {symbol} {side} 신규진입의 실제 가격 기준 SL/TP를 제안하세요.
검토 타임프레임: {timeframes_desc}

{candle_summary}

규칙 엔진 기준 보호가격(참고용): stop={baseline_stop}, target={baseline_target}
{exit_price_contract}
현재 구조/변동성/지지저항을 사용해 고정 퍼센트를 기계적으로 복사하지 말고 가격을 제안하세요.
반드시 JSON만 반환하세요:
{{
  "confidence": 0.0에서 1.0 사이 숫자,
  "exit_plan": {{
    "stop_loss_price": 숫자,
    "take_profit_1_price": 숫자,
    "take_profit_2_price": 숫자 | null,
    "confidence": 0.0에서 1.0 사이 숫자,
    "reasoning": "가격 근거 1~2문장"
  }},
  "reasoning": "전체 가격계획 근거 1~2문장"
}}
"""


def propose_entry_exit_plan(cfg, symbol: str, side: str, tf_list: list, candle_summary: str,
                            baseline_stop: float | None, baseline_target: float | None,
                            exit_price_contract: str = "") -> dict:
    """Candidate C direction is immutable; Gemini proposes exit prices only."""
    log = cfg.logger or logger
    if side not in ("long", "short") or not getattr(cfg, "GEMINI_API_KEY", None):
        return {"action": side, "confidence": None, "exit_plan": None,
                "reasoning": "Gemini exit-price proposal unavailable"}
    prompt = ENTRY_EXIT_PLAN_PROMPT_TEMPLATE.format(
        symbol=symbol, side=side, timeframes_desc=", ".join(timeframes.label(tf) for tf in tf_list),
        candle_summary=candle_summary, baseline_stop=baseline_stop, baseline_target=baseline_target,
        exit_price_contract=exit_price_contract or "",
    )
    client = _get_client(cfg)
    response = client.models.generate_content(
        model=cfg.GEMINI_MODEL, contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            thinking_config=types.ThinkingConfig(thinking_level="low"),
        ),
    )
    usage = getattr(response, "usage_metadata", None)
    if usage is not None:
        try:
            usage_log.record_usage(
                cfg.user_dir, symbol, usage.prompt_token_count or 0,
                (usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0),
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
    response = client.models.generate_content(
        model=cfg.GEMINI_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
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
            usage_log.record_usage(cfg.user_dir, symbol, input_tokens, output_tokens)
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

반드시 아래 JSON 형식으로만 답하세요. 다른 텍스트는 포함하지 마세요.
{{
  "assessment": "thesis_intact" | "weakening" | "invalidated",
  "confidence": 0.0에서 1.0 사이 숫자,
  "reasoning": "판단 근거를 2~3문장으로"
}}
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
    response = client.models.generate_content(
        model=cfg.GEMINI_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            thinking_config=types.ThinkingConfig(thinking_level="low"),
        ),
    )

    log = cfg.logger or logger

    usage = getattr(response, "usage_metadata", None)
    if usage is not None:
        input_tokens = usage.prompt_token_count or 0
        output_tokens = (usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0)
        try:
            usage_log.record_usage(cfg.user_dir, symbol, input_tokens, output_tokens, purpose=purpose)
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
    response = client.models.generate_content(
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
            )
        except Exception:
            (cfg.logger or logger).exception('Gemini strategy review usage logging failed')
    result = json.loads((response.text or '').strip())
    if not isinstance(result, dict):
        raise ValueError('strategy_review_not_object')
    return result
