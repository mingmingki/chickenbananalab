"""LONG 신규진입 후보에 대한 Shadow veto 실험(수집 전용) - 계산만 하고 어떤 경우에도
주문을 막거나 청산하지 않는다. 전부 이미 계산된 market_structure/candle_finality 결과나
이미 fetch된 raw_dfs만 재사용하고, 새 Gemini/OpenAI/OKX 호출을 하지 않는다.

정의 원칙: forensic 분석(BTC/XRP stop_loss 2건 vs XRP take_profit 1건)에서 실제로 쓴
feature 정의를 그대로 재사용한다 - 여기서 새로 임의로 의미를 바꾸지 않는다.
"""
import market_structure

# VETO_FH의 1D RSI 임계값 - 수집 기간 동안 freeze. forensic 분석 결과에 맞춰 사후에
# 조정하지 않는다(overfitting 방지 - 사용자 지시).
VETO_FH_RSI_THRESHOLD = 75


def compute_4h_raw_flags(dfs: dict, closed_dfs: dict | None, indicators_by_tf: dict, prior_cf_indicators_4h: dict | None):
    """4H 관련 raw boolean 3개를 각각 따로 계산해 저장한다(합쳐서 하나의 "weakening"으로
    임의 정의하지 않는다).

    - 4h_macd_down: forensic Feature A와 동일한 정의 - "직전 cycle 대비 4H MACD 값이
      감소했는가"(단일 시점 값이 아니라 이전 cycle과의 비교). prior_cf_indicators_4h가
      없으면(이 심볼의 첫 cycle 등) None.
    - 4h_ema20_slope_nonpositive: market_structure.compute(dfs["4h"])의 ema20_slope_pct<=0.
      (이건 원래 제안했던 정의였지만 Feature A와 동일하지 않으므로 VETO_C에는 안 쓰고
      raw 필드로만 남긴다.)
    - 4h_rsi_down: 마찬가지로 직전 cycle 대비 4H RSI 감소 여부.
    """
    out = {"4h_macd_down": None, "4h_ema20_slope_nonpositive": None, "4h_rsi_down": None, "4h_ema20_slope_pct": None}
    live_4h = indicators_by_tf.get("4h", {}).get("live")
    if live_4h and prior_cf_indicators_4h:
        out["4h_macd_down"] = live_4h["macd"] < prior_cf_indicators_4h["macd"]
        out["4h_rsi_down"] = live_4h["rsi"] < prior_cf_indicators_4h["rsi"]

    if "4h" in dfs:
        try:
            struct_4h = market_structure.compute(dfs["4h"])
            slope = struct_4h.get("ema20_slope_pct")
            out["4h_ema20_slope_pct"] = slope
            if slope is not None:
                out["4h_ema20_slope_nonpositive"] = slope <= 0
        except Exception:
            pass
    return out


def compute_1h_not_recovered(indicators_1h_live: dict | None, structure_1h: dict | None) -> bool | None:
    """forensic feature B(1H price<EMA20) OR C(1H EMA20 slope<=0) OR D(1H low_structure==LL).
    셋 다 forensic 분석에서 이미 쓴 정의 그대로."""
    if not indicators_1h_live or not structure_1h:
        return None
    price_below_ema20 = indicators_1h_live["close"] < indicators_1h_live["ema20"]
    slope = structure_1h.get("ema20_slope_pct")
    slope_nonpositive = (slope is not None) and (slope <= 0)
    low_ll = structure_1h.get("low_structure") == "LL"
    return bool(price_below_ema20 or slope_nonpositive or low_ll)


def compute_5m_lh_or_ll(structure_5m: dict | None) -> bool | None:
    """forensic feature E(5m low==LL) OR F(5m high==LH)."""
    if not structure_5m:
        return None
    return structure_5m.get("high_structure") == "LH" or structure_5m.get("low_structure") == "LL"


def compute_3m_lh_or_ll(structure_3m: dict | None) -> bool | None:
    """compute_5m_lh_or_ll과 완전히 동일한 규칙을 3m에 적용한다(SHORT Shadow 조건의
    재료 중 하나 - 새로 해석하지 않고 5m과 같은 LH/LL 규칙을 그대로 씀)."""
    if not structure_3m:
        return None
    return structure_3m.get("high_structure") == "LH" or structure_3m.get("low_structure") == "LL"


def compute_short_shadow_condition(indicators_by_tf: dict, structures: dict, prior_cf_indicators_4h: dict | None) -> dict:
    """SHORT Shadow 후보 조건(frozen) - "1D/4H가 아직 bullish여도 4H 모멘텀 약화 +
    1H/5m/3m이 전부 약세로 정렬되면 counter-regime short를 검토할 만한가"를 READ ONLY로
    관찰하기 위한 순수 계산. 4개 구성요소 전부 위의 기존 LONG veto 원재료 함수를 그대로
    재사용한다 - 여기서 새 threshold를 만들거나 조정하지 않는다."""
    flags_4h = compute_4h_raw_flags({}, None, indicators_by_tf, prior_cf_indicators_4h)
    one_h_not_recovered = compute_1h_not_recovered(indicators_by_tf.get("1h", {}).get("live"), structures.get("1h"))
    five_m_bearish = compute_5m_lh_or_ll(structures.get("5m"))
    three_m_bearish = compute_3m_lh_or_ll(structures.get("3m"))

    condition = None
    if None not in (flags_4h["4h_macd_down"], one_h_not_recovered, five_m_bearish, three_m_bearish):
        condition = bool(flags_4h["4h_macd_down"] and one_h_not_recovered and five_m_bearish and three_m_bearish)

    return {
        "4h_macd_down": flags_4h["4h_macd_down"],
        "one_h_not_recovered": one_h_not_recovered,
        "five_m_bearish": five_m_bearish,
        "three_m_bearish": three_m_bearish,
        "short_shadow_condition": condition,
    }


def compute_vetoes(dfs, indicators_by_tf, structures, closed_dfs, structure_by_tf, prior_cf_indicators_4h):
    """4개 veto candidate를 계산한다. VETO_E는 아직 정의 미확정이라 raw feature만 반환하고
    boolean은 만들지 않는다(status="unresolved")."""
    flags_4h = compute_4h_raw_flags(dfs, closed_dfs, indicators_by_tf, prior_cf_indicators_4h)

    structure_1h_live = structures.get("1h")
    structure_5m_live = structures.get("5m")
    indicators_1h_live = indicators_by_tf.get("1h", {}).get("live")

    one_h_not_recovered_live = compute_1h_not_recovered(indicators_1h_live, structure_1h_live)
    five_m_lh_or_ll = compute_5m_lh_or_ll(structure_5m_live)

    veto_c = None
    if flags_4h["4h_macd_down"] is not None and structure_1h_live and structure_5m_live:
        veto_c = bool(
            flags_4h["4h_macd_down"]
            and (structure_1h_live.get("low_structure") == "LL" or structure_5m_live.get("low_structure") == "LL")
        )

    veto_d = None
    if one_h_not_recovered_live is not None and five_m_lh_or_ll is not None:
        veto_d = bool(one_h_not_recovered_live and five_m_lh_or_ll)

    veto_fh = None
    rsi_1d = indicators_by_tf.get("1d", {}).get("live", {}).get("rsi")
    if structure_5m_live and rsi_1d is not None:
        veto_fh = bool(structure_5m_live.get("high_structure") == "LH" and rsi_1d >= VETO_FH_RSI_THRESHOLD)

    # VETO_E: 아직 deterministic 정의 미확정 - boolean을 만들지 않고 raw feature만 저장한다.
    short_tf_raw = {}
    for tf in ("1m", "3m", "5m"):
        ind = indicators_by_tf.get(tf, {}).get("live")
        struct = structures.get(tf)
        if not ind or not struct:
            short_tf_raw[tf] = None
            continue
        short_tf_raw[tf] = {
            "close_gt_ema20": ind["close"] > ind["ema20"],
            "ema20_gt_ema50": ind["ema20"] > ind["ema50"],
            "rsi_ge_50": ind["rsi"] >= 50,
            "macd_ge_0": ind["macd"] >= 0,
            "ema20_slope_gt_0": (struct.get("ema20_slope_pct") or 0) > 0,
            "high_structure": struct.get("high_structure"),
            "low_structure": struct.get("low_structure"),
        }

    return {
        "flags_4h": flags_4h,
        "one_h_not_recovered_live": one_h_not_recovered_live,
        "five_m_lh_or_ll": five_m_lh_or_ll,
        "veto_c": veto_c,
        "veto_d": veto_d,
        "veto_fh": veto_fh,
        "veto_e_status": "unresolved",
        "veto_e_raw_features": short_tf_raw,
    }


# ---- 관찰 태그(Phase 1 뷰어 dashboard.html의 classifyStructureTag와 동일 규칙 - 이식본) ----
def classify_observation_tag(structures: dict, position_side: str | None) -> str:
    """dashboard.html의 classifyStructureTag(JS)와 반드시 같은 규칙을 유지한다.
    로직을 바꿀 일이 있으면 두 곳(여기와 dashboard.html) 모두 같이 수정해야 한다."""
    m1, m3, m5 = structures.get("1m", {}), structures.get("3m", {}), structures.get("5m", {})
    h1 = structures.get("1h", {})

    if position_side == "long" and (m5.get("swing_low_broken") or h1.get("swing_low_broken")):
        return "STRUCTURE_BREAK"
    if position_side == "short" and (m5.get("swing_high_broken") or h1.get("swing_high_broken")):
        return "STRUCTURE_BREAK"
    if position_side == "long" and (m3.get("low_structure") == "LL" or m5.get("low_structure") == "LL"):
        return "LONG_RISK"
    if position_side == "short" and (m3.get("high_structure") == "HH" or m5.get("high_structure") == "HH"):
        return "SHORT_RISK"
    h1_healthy = h1.get("high_structure") == "HH" or h1.get("low_structure") == "HL"
    lower_weak = any(x.get("low_structure") == "LL" or x.get("high_structure") == "LH" for x in (m1, m3, m5))
    if h1_healthy and lower_weak:
        return "MTF_CONFLICT"
    return "TREND_HEALTHY"
