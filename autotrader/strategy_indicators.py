"""Phase 3 - Candidate A/B 전략이 쓰는 지표를 정규화 캔들 리스트(list[dict])에
인과적으로(미래 데이터 없이) 부착한다.

EMA/ATR/RSI/MACD는 기존 실거래 파이프라인(indicators.py)이 쓰는 것과 완전히
동일한 라이브러리(ta)·동일한 윈도우로 계산한다 - 백테스터가 실거래와 다른
지표 정의를 쓰면 안 되기 때문이다. Donchian 채널(돌파 전략용)만 이 모듈에서
새로 추가한다."""
from __future__ import annotations

import pandas as pd

import indicators


def _rows_to_df(bars: list[dict]) -> pd.DataFrame:
    return pd.DataFrame({
        "timestamp": [pd.Timestamp(b["open_time_ms"], unit="ms", tz="UTC") for b in bars],
        "open": [b["open"] for b in bars],
        "high": [b["high"] for b in bars],
        "low": [b["low"] for b in bars],
        "close": [b["close"] for b in bars],
        "volume": [b.get("volume_contracts") or 0.0 for b in bars],
    })


def augment_with_indicators(bars: list[dict], *, donchian_n: int) -> list[dict]:
    """bars: open_time_ms 오름차순 정규화 캔들(market_data_store 스키마, confirm=1만).
    반환: 각 bar 원본 필드 + ema_20/ema_50/atr_14/rsi_14/macd/donchian_high_prior_n/
    donchian_low_prior_n이 추가된 새 리스트(원본 불변).

    donchian_high_prior_n/low는 "이 bar 포함 이전 N개"가 아니라 "이 bar 직전
    N개"의 high/low 최댓값/최솟값이다(shift(1) 후 rolling) - 현재 bar 자신의
    고가/저가로 스스로의 돌파를 판정하는 자기참조를 피하기 위함(표준 Donchian
    breakout 정의)."""
    if not bars:
        return []
    if len(bars) < 14:
        # ta.AverageTrueRange는 최소 14개 미만이면 내부적으로 IndexError를 던진다
        # (라이브러리 자체의 최소 윈도우 요구사항) - 지표를 지어내지 않고 전부
        # None으로 남긴다. 실전 백테스트 범위(수천 개 bar)에서는 도달하지 않는다.
        return [dict(bar, ema_20=None, ema_50=None, atr_14=None, rsi_14=None, macd=None,
                     donchian_high_prior_n=None, donchian_low_prior_n=None) for bar in bars]
    df = _rows_to_df(bars)
    df = indicators.add_indicators(df)
    df["donchian_high_prior_n"] = df["high"].shift(1).rolling(window=donchian_n, min_periods=donchian_n).max()
    df["donchian_low_prior_n"] = df["low"].shift(1).rolling(window=donchian_n, min_periods=donchian_n).min()

    augmented = []
    for i, bar in enumerate(bars):
        row = df.iloc[i]
        merged = dict(bar)
        merged["ema_20"] = _none_if_nan(row["ema_20"])
        merged["ema_50"] = _none_if_nan(row["ema_50"])
        merged["atr_14"] = _none_if_nan(row["atr_14"])
        merged["rsi_14"] = _none_if_nan(row["rsi_14"])
        merged["macd"] = _none_if_nan(row["macd"])
        merged["donchian_high_prior_n"] = _none_if_nan(row["donchian_high_prior_n"])
        merged["donchian_low_prior_n"] = _none_if_nan(row["donchian_low_prior_n"])
        augmented.append(merged)
    return augmented


def _none_if_nan(value) -> float | None:
    if value is None or pd.isna(value):
        return None
    return float(value)
