"""가격 구조(swing high/low, HH/HL/LH/LL 등) 분석 - EMA/RSI/MACD 같은 지표(indicators.py)와
성격이 달라 별도 모듈로 둔다.

Phase 1(Feature Shadow): 여기서 계산한 값은 로그로만 남기고 실거래 판단(Gemini 프롬프트,
주문 로직)에는 아직 쓰지 않는다. "상위 타임프레임이 강세라 하위 타임프레임 하락을 계속
눌림으로 해석"하는 경우와 "실제로 저점 구조가 깨진" 경우를 구분하는 데 필요한 재료를
쌓는 단계다."""
import pandas as pd

CORE_FIELDS = [
    "swing_high", "swing_high_bars_ago", "swing_high_broken",
    "swing_low", "swing_low_bars_ago", "swing_low_broken",
    "high_structure", "low_structure",
    "ema20_slope_pct", "ema50_slope_pct", "atr_pct", "volume_ratio",
]


def _find_swing_points(df: pd.DataFrame, k: int) -> tuple[list[int], list[int]]:
    """프랙탈 방식: 인덱스 i의 high/low가 [i-k, i+k] 구간에서 최댓값/최솟값이면 swing
    포인트로 확정한다. 마지막 k개 봉은 그 뒤로 봉이 더 있어야 판정 가능하므로 아직 확정할
    수 없다(그래서 range(k, n-k)로 끝을 잘라낸다) - 이게 "미확정 봉을 swing으로 잘못
    잡는" 실수를 막는 핵심이다."""
    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    n = len(df)
    swing_high_idx, swing_low_idx = [], []
    for i in range(k, n - k):
        if highs[i] == highs[i - k:i + k + 1].max():
            swing_high_idx.append(i)
        if lows[i] == lows[i - k:i + k + 1].min():
            swing_low_idx.append(i)
    return swing_high_idx, swing_low_idx


def compute(df: pd.DataFrame, swing_lookback: int = 2, ema_slope_lookback: int = 5, volume_lookback: int = 20) -> dict:
    """df는 indicators.add_indicators()를 거쳐 ema_20/ema_50/atr_14가 이미 있어야 한다.
    데이터가 부족하면 해당 필드는 None으로 둔다(예외를 던지지 않음 - 이 모듈의 결과는
    로그 전용이라 계산 실패로 매매 사이클 자체가 죽으면 안 된다)."""
    result = {field: None for field in CORE_FIELDS}
    n = len(df)
    if n < swing_lookback * 2 + 3:
        return result

    swing_high_idx, swing_low_idx = _find_swing_points(df, k=swing_lookback)
    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    last_close = float(df["close"].iloc[-1])

    if swing_high_idx:
        last_sh = swing_high_idx[-1]
        result["swing_high"] = float(highs[last_sh])
        result["swing_high_bars_ago"] = n - 1 - last_sh
        result["swing_high_broken"] = bool(last_close > highs[last_sh])
        if len(swing_high_idx) >= 2:
            prev_sh = swing_high_idx[-2]
            result["high_structure"] = "HH" if highs[last_sh] > highs[prev_sh] else "LH"

    if swing_low_idx:
        last_sl = swing_low_idx[-1]
        result["swing_low"] = float(lows[last_sl])
        result["swing_low_bars_ago"] = n - 1 - last_sl
        result["swing_low_broken"] = bool(last_close < lows[last_sl])
        if len(swing_low_idx) >= 2:
            prev_sl = swing_low_idx[-2]
            result["low_structure"] = "HL" if lows[last_sl] > lows[prev_sl] else "LL"

    if "ema_20" in df.columns and n > ema_slope_lookback:
        ema20 = df["ema_20"].to_numpy()
        prev, cur = ema20[-1 - ema_slope_lookback], ema20[-1]
        if pd.notna(prev) and pd.notna(cur) and prev:
            result["ema20_slope_pct"] = float((cur - prev) / prev * 100)

    if "ema_50" in df.columns and n > ema_slope_lookback:
        ema50 = df["ema_50"].to_numpy()
        prev, cur = ema50[-1 - ema_slope_lookback], ema50[-1]
        if pd.notna(prev) and pd.notna(cur) and prev:
            result["ema50_slope_pct"] = float((cur - prev) / prev * 100)

    if "atr_14" in df.columns:
        atr = df["atr_14"].iloc[-1]
        if pd.notna(atr) and last_close:
            result["atr_pct"] = float(atr / last_close * 100)

    if n > volume_lookback:
        volumes = df["volume"].to_numpy()
        recent_avg = volumes[-1 - volume_lookback:-1].mean()
        if recent_avg:
            result["volume_ratio"] = float(volumes[-1] / recent_avg)

    return result


def compute_multi_timeframe(dfs: dict, timeframes_wanted: list[str]) -> dict:
    """dfs: {"1m": df, "3m": df, ...} (indicators.add_indicators를 거친 것). timeframes_wanted에
    해당하는 것만 계산한다 - 새 OHLCV 요청 없이 이미 가져온 타임프레임 중 일부만 쓴다."""
    return {tf: compute(dfs[tf]) for tf in timeframes_wanted if tf in dfs}
