import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator, MACD
from ta.volatility import AverageTrueRange, BollingerBands

import timeframes


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["rsi_14"] = RSIIndicator(df["close"], window=14).rsi()
    df["ema_20"] = EMAIndicator(df["close"], window=20).ema_indicator()
    df["ema_50"] = EMAIndicator(df["close"], window=50).ema_indicator()

    macd = MACD(df["close"])
    df["macd"] = macd.macd()
    df["macd_signal"] = macd.macd_signal()

    df["atr_14"] = AverageTrueRange(df["high"], df["low"], df["close"], window=14).average_true_range()

    bb = BollingerBands(df["close"], window=20)
    df["bb_high"] = bb.bollinger_hband()
    df["bb_low"] = bb.bollinger_lband()

    return df


def summarize_latest(df: pd.DataFrame, n: int = 30) -> str:
    """Gemini 프롬프트에 넣을 최근 n개 캔들 요약 텍스트."""
    recent = df.tail(n)
    lines = ["timestamp,close,rsi_14,ema_20,ema_50,macd,macd_signal,atr_14"]
    for _, row in recent.iterrows():
        lines.append(
            f"{row['timestamp']},{row['close']:.2f},{row['rsi_14']:.1f},"
            f"{row['ema_20']:.2f},{row['ema_50']:.2f},{row['macd']:.2f},"
            f"{row['macd_signal']:.2f},{row['atr_14']:.2f}"
        )
    return "\n".join(lines)


def summarize_multi_timeframe(dfs: dict, n: int = 20) -> str:
    """타임프레임별 요약을 이어붙인다. dfs: {"1m": df, "5m": df, ...}"""
    parts = []
    for tf, df in dfs.items():
        count = min(n, len(df))
        parts.append(f"[{timeframes.label(tf)} 캔들 최근 {count}개]")
        parts.append(summarize_latest(df, n=n))
    return "\n\n".join(parts)


def summarize_compact(df: pd.DataFrame, tf_label: str, lookback: int = 20) -> str:
    """원시 캔들을 그대로 보내는 대신, 현재 지표 상태 + 최근 변화율만 한 줄로 압축한다.
    원시 20행(타임프레임당) 대비 토큰을 크게 아끼면서도 추세 판단에 필요한 핵심 신호
    (RSI 방향, EMA 정배열/역배열, MACD 상태, 최근 변화율, 변동성)는 유지한다."""
    window = df.tail(lookback)
    last = window.iloc[-1]
    first = window.iloc[0]

    close, ema20, ema50 = last["close"], last["ema_20"], last["ema_50"]
    rsi, macd, macd_sig, atr = last["rsi_14"], last["macd"], last["macd_signal"], last["atr_14"]

    if close > ema20 > ema50:
        trend = "price>EMA20>EMA50(정배열/강세)"
    elif close < ema20 < ema50:
        trend = "price<EMA20<EMA50(역배열/약세)"
    else:
        above_ema20 = "위" if close > ema20 else "아래"
        ema_order = "EMA20>EMA50" if ema20 > ema50 else "EMA20<EMA50"
        trend = f"가격은 EMA20 {above_ema20}, {ema_order}(혼조)"

    macd_state = f"{'상승전환' if macd > macd_sig else '하락전환'}({'0선 위' if macd > 0 else '0선 아래'})"

    rsi_prev = window.iloc[max(0, len(window) - 6)]["rsi_14"]
    rsi_dir = "상승중" if rsi > rsi_prev else "하락중" if rsi < rsi_prev else "횡보"

    change_pct = (close - first["close"]) / first["close"] * 100 if first["close"] else 0.0
    atr_pct = (atr / close * 100) if close else 0.0

    return (
        f"[{tf_label}] 현재가 {close:.12g}, EMA20 {ema20:.12g}, EMA50 {ema50:.12g}, "
        f"RSI {rsi:.1f}({rsi_dir}), {trend}, MACD {macd_state}, "
        f"최근 {len(window)}개 구간 변화율 {change_pct:+.2f}%, ATR {atr:.12g} ({atr_pct:.2f}%)"
    )


def summarize_multi_timeframe_compact(dfs: dict, n: int = 20) -> str:
    """타임프레임별 압축 요약(타임프레임당 한 줄)을 이어붙인다. dfs: {"1m": df, ...}"""
    return "\n".join(summarize_compact(df, timeframes.label(tf), lookback=n) for tf, df in dfs.items())
