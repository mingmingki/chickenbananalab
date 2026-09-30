"""CORE 신규 SHORT 진입 전용 tactical short confirmation(2026-08-28, 사용자 지시).

실거래 문제: 1D가 bullish로 유지되는 동안 GPT Entry Gate가 "1D bullish -> 역추세
숏은 위험"이라는 이유로 4H/1H/3m/5m가 전부 하락 정렬돼도 SHORT를 사실상 전부
wait/reject해서, ETH LONG이 결국 손절(-7.92 USDT)까지 발생한 사례가 확인됐다. 1D
bullish는 SHORT의 위험요소이지 절대 veto가 아니다 - 이 게이트는 1D를 아예 보지
않고 4H/1H/3m/5m + confidence만으로 "지금 당장의 하락 전술적 기회"를 판단한다.

2026-08-31 추가 수정 - Gemini의 market_regime(주로 1D가 좌우)이 아직 "bullish"에
머물러 있으면(4H/1H/하위 TF가 전부 하락 정렬됐어도) confirmed 자체가 False로
막혀 SHORT_LEVEL이 통째로 NONE이 되는 실거래 사고가 실제로 반복 확인됐다(PI
07:25/08:01 등). gemini_analyzer.py 자신의 주석에도 "market_regime은 정보용
필드라 매매 게이트에는 쓰지 않는다"고 명시돼 있는데, 이 파일만 규칙을 어기고
regime을 confirmed의 하드 게이트로 쓰고 있었다 - 순수 버그다. regime_ok는 이제
reasons에만 남겨(로그/진단용, SHORT_LEVEL의 STRONG 승격 판단에는 여전히 쓰임)
confirmed 계산에서는 제외한다.

trader.run_cycle에서 action=="short"일 때(core_short_level.classify()를 통해)
evaluate()를 호출해 TACTICAL_SHORT_CONFIRMED 여부를 계산한다. [2026-08-29, 사용자
지시로 변경] 이 결과는 더 이상 GPT Entry Gate의 "wait"/"reject" 판정을 뒤집는 데
쓰이지 않는다 - 예전에는 wait일 때 이 값으로 override 여부를 결정했으나(실거래에서
ETH short가 SHORT_LEVEL=TACTICAL, GPT wait인데도 진입해 Net -0.26 USDT 손실
발생), 그 override 경로를 완전히 제거했다. 지금은 오직 SHORT_LEVEL 분류(EARLY/
TACTICAL/STRONG/FULL_BEARISH)·sizing 결정·로그/분석 용도로만 쓰인다 - GPT
Entry Gate(approve_now만 허용)가 CORE 신규 주문의 유일하고 최종적인 게이트다
(trader._gpt_entry_gate 참고). 반드시 완전히 종료된 캔들만 사용한다.

core_long_confirmation.py(LONG 전용)와 완전히 독립적으로 동작하며 서로 참조하지
않는다."""
import entry_veto_shadow


def macd_histogram_worsened_for_last_n(df, n: int = 2) -> bool | None:
    """MACD 히스토그램(macd - macd_signal)이 마지막 n개 캔들에서 매번 직전 값보다
    나빠졌는지(연속 악화). 판단에 필요한 n+1개 행이 없으면 None."""
    if df is None or len(df) < n + 1:
        return None
    hist = (df["macd"] - df["macd_signal"]).tail(n + 1).tolist()
    return all(hist[i] < hist[i - 1] for i in range(1, len(hist)))


def _bearish_4h(closed_4h_df) -> bool:
    """4H bearish: close < EMA20 AND (MACD hist<0 OR hist 2개 종료봉 연속 악화 OR
    MACD line이 signal 아래)."""
    if closed_4h_df is None or len(closed_4h_df) == 0:
        return False
    last = closed_4h_df.iloc[-1]
    if not bool(last["close"] < last["ema_20"]):
        return False
    macd_hist_negative = bool((last["macd"] - last["macd_signal"]) < 0)
    macd_hist_worsened = bool(macd_histogram_worsened_for_last_n(closed_4h_df, n=2))
    macd_below_signal = bool(last["macd"] < last["macd_signal"])
    return macd_hist_negative or macd_hist_worsened or macd_below_signal


def _bearish_1h(closed_1h_df) -> bool:
    """1H bearish using confirmed candles only.

    A fully bearish EMA stack is mandatory.  MACD may already be below signal,
    or a counter-trend rebound may still have MACD above signal while its
    histogram has just turned down.  The latter is the PI 2026-09-25 case that
    the old definition incorrectly classified as NONE despite 1H/4H price
    structure and lower timeframes all pointing down.
    """
    if closed_1h_df is None or len(closed_1h_df) < 2:
        return False
    last = closed_1h_df.iloc[-1]
    prev = closed_1h_df.iloc[-2]
    bearish_stack = bool(last["close"] < last["ema_20"] < last["ema_50"])
    if not bearish_stack:
        return False
    hist_last = last["macd"] - last["macd_signal"]
    hist_prev = prev["macd"] - prev["macd_signal"]
    histogram_turning_down = bool(hist_last < hist_prev)
    macd_below_signal = bool(last["macd"] < last["macd_signal"])
    return bool(macd_below_signal or histogram_turning_down)


def evaluate(
    gemini_action: str, gemini_confidence: float | None, regime: str | None,
    closed_4h_df, closed_1h_df, structure_3m: dict | None, structure_5m: dict | None,
    min_confidence: float = 0.65,
) -> dict:
    """TACTICAL_SHORT_CONFIRMED 여부와 각 세부 조건을 반환한다. 데이터 부족/조건
    미충족은 전부 안전하게 False(미확정)로 처리한다.

    regime_ok는 reasons에 포함되지만 confirmed 계산에는 관여하지 않는다(2026-08-31) -
    "1D bullish(-> regime="bullish")"라는 이유만으로 4H/1H/하위 TF가 전부 하락
    정렬된 tactical short 후보를 원천 차단하면 안 된다는 게 이 파일 전체의 핵심
    취지인데, regime을 confirmed의 AND 조건에 넣으면 그 취지를 스스로 어기게 된다.
    regime_ok는 core_short_level.classify()가 STRONG 승격 여부를 판단할 때(그리고
    로그/GPT 프롬프트의 진단 정보로) 계속 쓰인다."""
    confidence_ok = (
        isinstance(gemini_confidence, (int, float))
        and not isinstance(gemini_confidence, bool)
        and gemini_confidence >= min_confidence
    )
    reasons = {
        "action_is_short": gemini_action == "short",
        "confidence_ok": bool(confidence_ok),
        "regime_ok": regime in ("transition", "bearish"),
        "4h_bearish": _bearish_4h(closed_4h_df),
        "1h_bearish": _bearish_1h(closed_1h_df),
        "3m_bearish": bool(entry_veto_shadow.compute_3m_lh_or_ll(structure_3m)),
        "5m_bearish": bool(entry_veto_shadow.compute_5m_lh_or_ll(structure_5m)),
    }
    confirmed = (
        reasons["action_is_short"] and reasons["confidence_ok"]
        and reasons["4h_bearish"] and reasons["1h_bearish"]
        and reasons["3m_bearish"] and reasons["5m_bearish"]
    )
    return {
        "confirmed": confirmed,
        "reasons": reasons,
        "regime": regime,
        "confidence": gemini_confidence,
    }
