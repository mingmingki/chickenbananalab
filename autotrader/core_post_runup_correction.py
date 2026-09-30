"""CORE post-runup correction mode.

Purpose:
- Separate "1D bullish background" from "short-term correction after a sharp run-up".
- Uses only confirmed candles/structures already fetched by CORE.
- Does not place orders. It produces deterministic context consumed by the existing
  Gemini -> local gate -> GPT approval pipeline.

The detector is intentionally conservative. A correction is active only when:
1) the recent 24h move was materially extended (price % or ATR based),
2) price has retraced meaningfully from the recent peak,
3) confirmed 1H momentum is weakening,
4) both confirmed 3m and 5m structures are bearish (LH/LL).
"""

from __future__ import annotations

import math

RUNUP_24H_PCT = 4.0
PEAK_EXTENSION_ATR = 2.0
PEAK_RETRACE_PCT = 1.0


def _finite(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _structure_bearish(structure: dict | None) -> bool:
    if not structure:
        return False
    return structure.get("high_structure") == "LH" or structure.get("low_structure") == "LL"


def evaluate(
    closed_1h_df,
    closed_4h_df,
    closed_5m_df,
    structure_3m: dict | None,
    structure_5m: dict | None,
) -> dict:
    result = {
        "active": False,
        "reason": "data_unavailable",
        "runup_24h_pct": None,
        "peak_retracement_pct": None,
        "peak_extension_atr": None,
        "one_h_weakening": False,
        "one_h_below_ema20": False,
        "one_h_hist_worsening": False,
        "three_m_bearish": _structure_bearish(structure_3m),
        "five_m_bearish": _structure_bearish(structure_5m),
    }

    if (
        closed_1h_df is None or closed_4h_df is None or closed_5m_df is None
        or len(closed_1h_df) < 25 or len(closed_4h_df) < 1 or len(closed_5m_df) < 1
    ):
        return result

    try:
        one_h = closed_1h_df
        latest_1h = one_h.iloc[-1]
        latest_4h = closed_4h_df.iloc[-1]
        current_price = float(closed_5m_df.iloc[-1]["close"])
        reference_24h = float(one_h.iloc[-25]["close"])
        recent_peak = float(one_h.tail(24)["high"].max())
        ema20_1h = float(latest_1h["ema_20"])
        atr14_4h = float(latest_4h["atr_14"])

        hist = [
            float(row["macd"] - row["macd_signal"])
            for _, row in one_h.tail(3).iterrows()
        ]
    except Exception:
        return result

    values = (current_price, reference_24h, recent_peak, ema20_1h, atr14_4h, *hist)
    if not all(_finite(v) for v in values):
        return result
    if current_price <= 0 or reference_24h <= 0 or recent_peak <= 0 or atr14_4h <= 0:
        return result

    runup_pct = (recent_peak / reference_24h - 1.0) * 100.0
    retracement_pct = (recent_peak / current_price - 1.0) * 100.0
    peak_extension_atr = (recent_peak - ema20_1h) / atr14_4h

    one_h_below_ema20 = bool(float(latest_1h["close"]) < ema20_1h)
    one_h_hist_worsening = bool(hist[-1] < hist[-2] < hist[-3])
    one_h_weakening = one_h_below_ema20 or one_h_hist_worsening

    extended = runup_pct >= RUNUP_24H_PCT or peak_extension_atr >= PEAK_EXTENSION_ATR
    retraced = retracement_pct >= PEAK_RETRACE_PCT
    lower_bearish = result["three_m_bearish"] and result["five_m_bearish"]

    active = bool(extended and retraced and one_h_weakening and lower_bearish)
    reason = (
        "post_runup_correction_confirmed" if active
        else "runup_not_extended" if not extended
        else "peak_retracement_too_small" if not retraced
        else "1h_momentum_not_weakening" if not one_h_weakening
        else "3m_5m_not_both_bearish"
    )

    result.update(
        active=active,
        reason=reason,
        runup_24h_pct=runup_pct,
        peak_retracement_pct=retracement_pct,
        peak_extension_atr=peak_extension_atr,
        one_h_weakening=one_h_weakening,
        one_h_below_ema20=one_h_below_ema20,
        one_h_hist_worsening=one_h_hist_worsening,
    )
    return result


def prompt_context(ctx: dict | None) -> str:
    if not ctx or not ctx.get("active"):
        return ""
    return (
        "\n\n[급등 후 조정 모드 - 결정론적 CORE 컨텍스트]\n"
        "CORRECTION_ACTIVE=true\n"
        f"최근 24h 급등폭(고점 기준): {ctx.get('runup_24h_pct'):.2f}%\n"
        f"고점 대비 되돌림: {ctx.get('peak_retracement_pct'):.2f}%\n"
        f"고점의 1H EMA20 대비 4H ATR 확장: {ctx.get('peak_extension_atr'):.2f} ATR\n"
        f"1H 모멘텀 약화: {bool(ctx.get('one_h_weakening'))}\n"
        f"3m 하락구조: {bool(ctx.get('three_m_bearish'))}\n"
        f"5m 하락구조: {bool(ctx.get('five_m_bearish'))}\n"
        "해석: 1D가 bullish여도 이미 크게 오른 뒤 조정이 확인된 구간입니다. "
        "1D 강세만을 이유로 신규 LONG/기존 LONG hold를 정당화하지 마세요. "
        "무포지션이면 counter-regime SHORT를 적극 검토하고, LONG 보유 중이면 "
        "하락 정렬이 유지되는 한 SHORT 반전(reversal)을 유효한 선택지로 검토하세요. "
        "단, 3m/5m 하락구조가 회복되거나 1H 약화가 해소되면 이 조정 모드는 무효입니다."
    )
