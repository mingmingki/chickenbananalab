"""CORE SHORT 4단계 공격 레벨(EARLY/TACTICAL/STRONG/FULL_BEARISH) + sizing(2026-08-29,
사용자 지시).

우선순위(항상 가장 높은 레벨 하나만): FULL_BEARISH > STRONG > TACTICAL > EARLY > NONE.
TACTICAL 이상은 core_tactical_short.evaluate()를 그대로 재사용한다(중복 재정의 없음,
기존 TACTICAL 동작/테스트 불변). LONG confirmation(core_long_confirmation.py)과는
완전히 독립적으로 동작한다. 반드시 완전히 종료된 캔들만 사용한다.

레벨 정의:
- EARLY: 두 경로 중 하나. (A) 기존 4H bearish + 1H bearish + 하위TF 약화 초기단계,
  또는 (B) post-runup correction mode가 active(최근 급등 + 고점 되돌림 + 1H 모멘텀
  약화 + 3m/5m bearish)인 counter-regime 조정 숏. B는 조정을 4H 완전 이탈까지
  기다렸다가 뒤늦게 추격하지 않기 위한 전술적 EARLY 경로다.
- TACTICAL: core_tactical_short.evaluate()의 confirmed==True(4H/1H/3m/5m bearish +
  confidence>=0.65). regime은 더 이상 이 조건에 관여하지 않는다(아래 참고).
- STRONG: TACTICAL 확정 + regime=="bearish"(transition 아님). 스펙이 요구하는 "1H
  close<EMA20/MACD<signal/hist<0/모멘텀 유지"는 TACTICAL의 1H 판정(core_tactical_short.
  _bearish_1h - macd<signal이면 구조적으로 hist<0)이 이미 전부 보장하므로 별도 재정의
  없이 그대로 재사용한다.
- FULL_BEARISH: STRONG 확정 + 1D CLOSED candle도 bearish(close<EMA20 AND (macd<signal
  OR hist<0)).

weakening(EARLY 전용, "약화 조짐" - bearish보다 약한 신호): close<EMA20 OR MACD
histogram이 직전 종료봉보다 나빠짐 OR MACD line<signal 중 하나 이상.

2026-08-31 수정 - EARLY/TACTICAL 판정에서 Gemini의 market_regime(주로 1D가 좌우)을
더 이상 게이트로 쓰지 않는다. "1D가 아직 bullish"라는 이유만으로 4H/1H/하위 TF가
전부 하락 정렬된 후보를 NONE으로 깔아뭉개는 실거래 문제(PI 07:25/08:01 등)가 반복
확인됐고, 애초에 gemini_analyzer.py 자신도 market_regime을 "정보용 참고 필드"라고
명시한다. STRONG/FULL_BEARISH만 여전히 regime(및 1D 캔들 자체)을 승격 조건으로
쓴다 - 이건 "더 강한 확신 등급"의 정의이지 "진입 자체를 막는 최초 게이트"가 아니라서
성격이 다르다."""
import core_tactical_short
import entry_veto_shadow

LEVEL_RATIOS = {"EARLY": 0.25, "TACTICAL": 0.50, "STRONG": 0.75, "FULL_BEARISH": 1.00}
LEVEL_PRIORITY = ["FULL_BEARISH", "STRONG", "TACTICAL", "EARLY", "NONE"]
_DOWNGRADE_MAP = {"FULL_BEARISH": "STRONG", "STRONG": "TACTICAL", "TACTICAL": "EARLY", "EARLY": "EARLY", "NONE": "NONE"}


def _weakening(closed_df) -> bool:
    if closed_df is None or len(closed_df) < 2:
        return False
    last = closed_df.iloc[-1]
    prev = closed_df.iloc[-2]
    close_below_ema20 = bool(last["close"] < last["ema_20"])
    hist_last = last["macd"] - last["macd_signal"]
    hist_prev = prev["macd"] - prev["macd_signal"]
    hist_worse = bool(hist_last < hist_prev)
    macd_below_signal = bool(last["macd"] < last["macd_signal"])
    return close_below_ema20 or hist_worse or macd_below_signal


def _full_bearish_1d(closed_1d_df) -> bool:
    if closed_1d_df is None or len(closed_1d_df) == 0:
        return False
    last = closed_1d_df.iloc[-1]
    if not bool(last["close"] < last["ema_20"]):
        return False
    macd_below_signal = bool(last["macd"] < last["macd_signal"])
    hist_negative = bool((last["macd"] - last["macd_signal"]) < 0)
    return macd_below_signal or hist_negative


def classify(
    gemini_action: str, gemini_confidence: float | None, regime: str | None,
    closed_1d_df, closed_4h_df, closed_1h_df, closed_3m_df, closed_5m_df,
    structure_3m: dict | None, structure_5m: dict | None, min_confidence: float = 0.65,
    correction_ctx: dict | None = None,
) -> dict:
    """SHORT_LEVEL을 FULL_BEARISH > STRONG > TACTICAL > EARLY > NONE 순서로 판정한다."""
    tactical = core_tactical_short.evaluate(
        gemini_action=gemini_action, gemini_confidence=gemini_confidence, regime=regime,
        closed_4h_df=closed_4h_df, closed_1h_df=closed_1h_df,
        structure_3m=structure_3m, structure_5m=structure_5m, min_confidence=min_confidence,
    )
    reasons = dict(tactical["reasons"])

    if tactical["confirmed"]:
        strong_ok = regime == "bearish"
        reasons["strong_regime_bearish"] = strong_ok
        if strong_ok:
            full_ok = _full_bearish_1d(closed_1d_df)
            reasons["full_1d_bearish"] = full_ok
            level = "FULL_BEARISH" if full_ok else "STRONG"
        else:
            level = "TACTICAL"
        return {"level": level, "reasons": reasons, "regime": regime, "confidence": gemini_confidence}

    # TACTICAL 미확정 - EARLY 후보. 2026-09-22부터 급등 후 조정 모드가 active면
    # 4H가 아직 EMA20 아래로 완전히 꺾이지 않았더라도, "최근 급등 -> 고점 되돌림 ->
    # 1H 모멘텀 약화 -> 3m/5m LH/LL"이 모두 확정된 전술적 correction short를 EARLY로
    # 허용한다. Gemini short + confidence는 기존과 똑같이 필수이고 GPT 최종 승인도
    # trader 쪽에서 그대로 필요하다.
    base_ok = reasons["action_is_short"] and reasons["confidence_ok"]
    correction_active = bool(correction_ctx and correction_ctx.get("active"))
    reasons["post_runup_correction_active"] = correction_active
    if base_ok and correction_active:
        reasons["correction_1h_weakening"] = bool(correction_ctx.get("one_h_weakening"))
        reasons["correction_3m_bearish"] = bool(correction_ctx.get("three_m_bearish"))
        reasons["correction_5m_bearish"] = bool(correction_ctx.get("five_m_bearish"))
        return {
            "level": "EARLY", "reasons": reasons, "regime": regime,
            "confidence": gemini_confidence, "correction_mode": True,
        }

    # 기존 EARLY 경로는 그대로 유지한다. regime은 base 조건이 아니다.
    if not base_ok or not (reasons["4h_bearish"] and reasons["1h_bearish"]):
        return {"level": "NONE", "reasons": reasons, "regime": regime, "confidence": gemini_confidence}

    weak_3m = _weakening(closed_3m_df)
    weak_5m = _weakening(closed_5m_df)
    reasons["3m_weakening"] = weak_3m
    reasons["5m_weakening"] = weak_5m
    early_ok = (reasons["3m_bearish"] and weak_5m) or (reasons["5m_bearish"] and weak_3m)
    level = "EARLY" if early_ok else "NONE"
    return {"level": level, "reasons": reasons, "regime": regime, "confidence": gemini_confidence}


def compute_margin(sizing_mode: str, fixed_margin: float, max_margin: float, level: str) -> float:
    """sizing_mode=="variable_max"면 max_margin x LEVEL_RATIOS[level], 그 외(기본값
    "fixed" 포함 - 알 수 없는 mode는 안전하게 fixed로 폴백)는 level과 무관하게
    fixed_margin 그대로. 부동소수점 오차로도 절대 max_margin을 넘지 않도록 clamp한다."""
    if sizing_mode != "variable_max":
        return fixed_margin
    ratio = LEVEL_RATIOS.get(level, 0.0)
    margin = max_margin * ratio
    return min(margin, max_margin)


def downgrade_level(level: str) -> str:
    """FULL_BEARISH->STRONG->TACTICAL->EARLY->EARLY(더 안 내려감). NONE은 그대로."""
    return _DOWNGRADE_MAP.get(level, level)
