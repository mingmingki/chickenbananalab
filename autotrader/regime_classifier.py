"""Market Regime Shadow 분류기(관찰 전용) - TREND/RANGE/BREAKOUT/TRANSITION 4분류.

핵심 원칙(사용자 지시):
1. authoritative 판정은 CLOSED candle 기준이다. LIVE 기준도 같이 계산하지만 비교/기록
   전용이고, 실제 regime 확정(hysteresis/episode)에는 절대 쓰지 않는다.
2. threshold는 Phase 0 실측 분포(BTC/ETH/XRP, 5m 36h n=1116, 1h 14d n=828, gap 0건
   확인됨)의 percentile을 그대로 얼린 것이다 - 임의 숫자가 아니고, 수집 기간 동안
   재조정하지 않는다(THRESHOLD_VERSION="v1"로 고정 기록).
3. 이미 계산된 dfs/closed_dfs/structures/closed_structures만 재사용한다. 새 OHLCV
   API 호출도, 새 Gemini/GPT 호출도 전혀 없다.
"""
import market_structure

THRESHOLD_VERSION = "v1"

# --- Phase 0 실측 percentile을 그대로 얼린 것 (RANGE=p25, TREND=p75) ---
# 5m (36h, n=1116, BTC/ETH/XRP)
BB_WIDTH_5M_RANGE_MAX = 0.692   # p25
BB_WIDTH_5M_TREND_MIN = 1.498   # p75

# 1h (14일, n=828, BTC/ETH/XRP)
EMA_SLOPE_1H_RANGE_MAX = 0.068  # p25 (|ema20_slope_pct|)
EMA_SLOPE_1H_TREND_MIN = 0.791  # p75
EMA_DIST_1H_RANGE_MAX = 0.178   # p25 (|ema20-ema50| / close * 100)
EMA_DIST_1H_TREND_MIN = 2.442   # p75
BB_WIDTH_1H_RANGE_MAX = 1.230   # p25
BB_WIDTH_1H_TREND_MIN = 5.723   # p75
ATR_1H_RANGE_MAX = 0.382        # p25
ATR_1H_TREND_MIN = 1.300        # p75

# --- 구조적 파라미터(percentile과 무관한 엔지니어링 상수 - 별도 표시) ---
RANGE_BOUNDARY_WINDOW_5M = 24     # 박스 상하단 계산용 5m 롤백 구간(2시간) - range_high/low 계산에만 쓰고 regime 점수에는 안 씀
BREAKOUT_LOOKBACK_5M = 6          # BB 스퀴즈->확장 비교 기준(30분 전 대비)
BREAKOUT_EXPANSION_RATIO_MIN = 1.5  # bb_width가 30분 전 대비 최소 이만큼 늘어나야 확장으로 인정
STRUCTURE_PERSISTENCE_WINDOW_1H = 6  # TREND 구조정렬 비율 계산에 쓰는 최근 1h 캔들 수


def _bb_metrics(df) -> dict:
    """df 마지막 행의 Bollinger Band 파생값(indicators.add_indicators가 이미 계산해둔
    bb_high/bb_low를 그대로 읽는다 - 새 계산 아님)."""
    row = df.iloc[-1]
    close = float(row["close"])
    bb_high, bb_low = row.get("bb_high"), row.get("bb_low")
    if bb_high is None or bb_low is None or _isnan(bb_high) or _isnan(bb_low) or not close:
        return {"bb_upper": None, "bb_lower": None, "bb_middle": None, "bb_width_pct": None, "bb_position": None}
    bb_upper, bb_lower = float(bb_high), float(bb_low)
    bb_middle = (bb_upper + bb_lower) / 2
    bb_width_pct = (bb_upper - bb_lower) / close * 100
    bb_position = (close - bb_lower) / (bb_upper - bb_lower) if bb_upper != bb_lower else None
    return {
        "bb_upper": bb_upper, "bb_lower": bb_lower, "bb_middle": bb_middle,
        "bb_width_pct": bb_width_pct, "bb_position": bb_position,
    }


def _isnan(x) -> bool:
    try:
        return x != x
    except Exception:
        return False


def _range_high_low(df, window: int) -> dict:
    """정보/로깅용 - regime 점수 계산에는 쓰지 않는다(Phase 2에서 RANGE structural
    outcome 설계할 때 필요하므로 미리 같이 기록만 해둔다)."""
    if len(df) < window:
        return {"range_high": None, "range_low": None, "range_width_pct": None}
    recent = df.tail(window)
    close = float(df["close"].iloc[-1])
    range_high = float(recent["high"].max())
    range_low = float(recent["low"].min())
    range_width_pct = (range_high - range_low) / close * 100 if close else None
    return {"range_high": range_high, "range_low": range_low, "range_width_pct": range_width_pct}


def _breakout_gate(df, boundary_window: int, squeeze_lookback: int, expansion_ratio_min: float) -> dict:
    """CLOSED candle 기준으로만 호출해야 한다(호출부 책임). false breakout 방지를 위해
    "가격이 직전 구간의 박스를 실제로 이탈" AND "BB width가 squeeze_lookback 이전 대비
    유의미하게 확장" 둘 다 요구한다 - 미완성 캔들의 wick 하나로는 확정 안 되도록,
    애초에 CLOSED df만 넘겨받는 구조로 강제한다."""
    if len(df) < boundary_window + squeeze_lookback + 1:
        return {"breakout_gate": False, "breakout_direction": None, "expansion_ratio": None}

    close = float(df["close"].iloc[-1])
    prior = df.iloc[-(boundary_window + 1):-1]
    prior_high, prior_low = float(prior["high"].max()), float(prior["low"].min())

    now_bb = _bb_metrics(df)
    then_bb = _bb_metrics(df.iloc[:-squeeze_lookback]) if len(df) > squeeze_lookback else None
    now_width, then_width = now_bb.get("bb_width_pct"), (then_bb or {}).get("bb_width_pct")
    expansion_ratio = (now_width / then_width) if (now_width and then_width) else None

    breakout_up = close > prior_high
    breakout_down = close < prior_low
    expanded = bool(expansion_ratio and expansion_ratio >= expansion_ratio_min)

    gate = bool((breakout_up or breakout_down) and expanded)
    direction = "up" if (gate and breakout_up) else "down" if (gate and breakout_down) else None
    return {"breakout_gate": gate, "breakout_direction": direction, "expansion_ratio": expansion_ratio}


def _structure_alignment_ratio(df, window: int) -> float | None:
    """최근 window개 CLOSED 1h 캔들 각각에서 시점을 뒤로 옮겨가며 market_structure.compute()를
    다시 돌려서, "이 방향(HH/HL 또는 LH/LL)이 최근 몇 개 캔들에서 유지됐는지" 비율을 낸다.
    새 API 호출 없음 - 이미 fetch된 df 안에서만 계산."""
    if len(df) < window + 10:
        return None
    current = market_structure.compute(df)
    cur_high, cur_low = current.get("high_structure"), current.get("low_structure")
    if cur_high is None and cur_low is None:
        return None
    matches = 0
    total = 0
    for back in range(window):
        sub = df.iloc[: len(df) - back] if back > 0 else df
        if len(sub) < 10:
            break
        s = market_structure.compute(sub)
        if s.get("high_structure") is None and s.get("low_structure") is None:
            continue
        total += 1
        if s.get("high_structure") == cur_high or s.get("low_structure") == cur_low:
            matches += 1
    return (matches / total) if total else None


def _normalize(value, low, high):
    """value<=low -> 0.0, value>=high -> 1.0, 그 사이는 선형보간. low/high는 각각
    RANGE_MAX/TREND_MIN(고정된 percentile 경계)."""
    if value is None:
        return None
    if high == low:
        return 0.5
    return max(0.0, min(1.0, (value - low) / (high - low)))


def _compute_one_basis(dfs: dict, structures: dict) -> dict:
    """dfs/structures: 하나의 basis(live 또는 closed)에 대한 {"1m":..,"5m":..,"1h":..,"4h":..}
    dict. structures는 market_structure.compute_multi_timeframe() 결과(4h는 없으므로
    여기서 직접 compute한다 - entry_veto_shadow.py의 4h 처리 패턴과 동일)."""
    features = {}

    s_1h = structures.get("1h", {})
    s_5m = structures.get("5m", {})
    ema_slope_1h = s_1h.get("ema20_slope_pct")
    atr_1h = s_1h.get("atr_pct")

    df_1h = dfs.get("1h")
    df_5m = dfs.get("5m")
    df_4h = dfs.get("4h")

    ema_dist_1h = None
    if df_1h is not None and len(df_1h):
        row = df_1h.iloc[-1]
        close = float(row["close"])
        if close and row.get("ema_20") is not None and row.get("ema_50") is not None:
            ema_dist_1h = abs(float(row["ema_20"]) - float(row["ema_50"])) / close * 100

    bb_1h = _bb_metrics(df_1h) if df_1h is not None and len(df_1h) else {}
    bb_5m = _bb_metrics(df_5m) if df_5m is not None and len(df_5m) else {}
    range_5m = _range_high_low(df_5m, RANGE_BOUNDARY_WINDOW_5M) if df_5m is not None else {}
    breakout = _breakout_gate(df_5m, RANGE_BOUNDARY_WINDOW_5M, BREAKOUT_LOOKBACK_5M, BREAKOUT_EXPANSION_RATIO_MIN) \
        if df_5m is not None else {"breakout_gate": False, "breakout_direction": None, "expansion_ratio": None}
    structure_alignment_1h = _structure_alignment_ratio(df_1h, STRUCTURE_PERSISTENCE_WINDOW_1H) if df_1h is not None else None

    features.update({
        "ema20_slope_1h_pct": ema_slope_1h, "atr_1h_pct": atr_1h, "ema_dist_1h_pct": ema_dist_1h,
        "bb_width_1h_pct": bb_1h.get("bb_width_pct"), "bb_width_5m_pct": bb_5m.get("bb_width_pct"),
        "bb_position_5m": bb_5m.get("bb_position"),
        "range_high_5m": range_5m.get("range_high"), "range_low_5m": range_5m.get("range_low"),
        "range_width_5m_pct": range_5m.get("range_width_pct"),
        "structure_alignment_1h": structure_alignment_1h,
        "high_structure_1h": s_1h.get("high_structure"), "low_structure_1h": s_1h.get("low_structure"),
        "high_structure_5m": s_5m.get("high_structure"), "low_structure_5m": s_5m.get("low_structure"),
        "expansion_ratio_5m": breakout.get("expansion_ratio"),
    })

    trend_parts = [
        _normalize(abs(ema_slope_1h) if ema_slope_1h is not None else None, EMA_SLOPE_1H_RANGE_MAX, EMA_SLOPE_1H_TREND_MIN),
        _normalize(ema_dist_1h, EMA_DIST_1H_RANGE_MAX, EMA_DIST_1H_TREND_MIN),
        structure_alignment_1h,
    ]
    trend_parts = [p for p in trend_parts if p is not None]
    trend_score = sum(trend_parts) / len(trend_parts) if trend_parts else None

    range_parts = []
    bw5 = bb_5m.get("bb_width_pct")
    if bw5 is not None:
        range_parts.append(1 - _normalize(bw5, BB_WIDTH_5M_RANGE_MAX, BB_WIDTH_5M_TREND_MIN))
    bw1h = bb_1h.get("bb_width_pct")
    if bw1h is not None:
        range_parts.append(1 - _normalize(bw1h, BB_WIDTH_1H_RANGE_MAX, BB_WIDTH_1H_TREND_MIN))
    if atr_1h is not None:
        range_parts.append(1 - _normalize(atr_1h, ATR_1H_RANGE_MAX, ATR_1H_TREND_MIN))
    range_score = sum(range_parts) / len(range_parts) if range_parts else None

    scores = {
        "trend_score": trend_score, "range_score": range_score,
        "breakout_gate": breakout.get("breakout_gate", False),
        "breakout_direction": breakout.get("breakout_direction"),
    }

    # 우선순위 gate(C) + score/margin(B) 하이브리드: BREAKOUT부터 확인 -> TREND vs RANGE -> 애매하면 TRANSITION.
    if scores["breakout_gate"]:
        raw_regime = "BREAKOUT"
    elif trend_score is None or range_score is None:
        raw_regime = "TRANSITION"
    else:
        margin = abs(trend_score - range_score)
        if max(trend_score, range_score) < 0.5 or margin < 0.15:
            raw_regime = "TRANSITION"
        elif trend_score > range_score:
            raw_regime = "TREND"
        else:
            raw_regime = "RANGE"

    return {"raw_regime": raw_regime, "scores": scores, "features": features}


def classify(dfs: dict, closed_dfs: dict, structures: dict, closed_structures: dict) -> dict:
    """LIVE와 CLOSED 양쪽을 계산하되, authoritative(=state machine/episode에 실제로
    쓰는) 값은 반드시 closed 쪽이어야 한다(호출부 책임 - 이 함수는 둘 다 반환만 한다).
    4h는 structures/closed_structures에 없으므로(MARKET_STRUCTURE_TIMEFRAMES에 4h가
    없음) 여기서 직접 market_structure.compute()를 불러 raw 필드만 참고용으로 추가한다."""
    structures_live = dict(structures)
    structures_closed = dict(closed_structures)
    if "4h" in dfs:
        try:
            structures_live = {**structures_live, "4h": market_structure.compute(dfs["4h"])}
        except Exception:
            pass
    if "4h" in closed_dfs:
        try:
            structures_closed = {**structures_closed, "4h": market_structure.compute(closed_dfs["4h"])}
        except Exception:
            pass

    live = _compute_one_basis(dfs, structures_live)
    closed = _compute_one_basis(closed_dfs, structures_closed)

    return {
        "threshold_version": THRESHOLD_VERSION,
        "regime_live": live["raw_regime"], "regime_closed": closed["raw_regime"],
        "live_closed_regime_divergence": live["raw_regime"] != closed["raw_regime"],
        "scores_live": live["scores"], "scores_closed": closed["scores"],
        "features_live": live["features"], "features_closed": closed["features"],
    }
