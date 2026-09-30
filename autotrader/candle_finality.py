"""OKX OHLCV의 마지막 캔들이 확정(closed)인지 진행 중(live)인지 판정.

배경: ccxt의 okx.parse_ohlcv()는 OKX 원본 응답의 9번째 필드("candlestick state" -
확정 여부를 나타내는 confirm 플래그)를 버리고 [timestamp, open, high, low, close,
volume] 6개만 반환한다. 그래서 우리 코드는 ccxt를 쓰는 한 애초에 "이 캔들이 확정됐는지"를
응답에서 직접 알 수 없고, timestamp + 타임프레임 길이로 직접 계산해서 판정해야 한다.

Phase 1.5(Shadow): 여기서 만든 "확정봉만 남긴 df"는 로그 비교용이며, 실제 Gemini
프롬프트/매매 판단(LIVE 경로)에는 아직 쓰지 않는다."""
import datetime

from timeframes import LADDER

TF_SECONDS = {code: secs for code, _, secs in LADDER}

# 서버-거래소 간 시계 오차, 그리고 캔들 마감 시각이 지난 직후 거래소가 확정 데이터를
# 발행하기까지의 짧은 지연을 흡수하기 위한 안전 여유. 너무 크면 이미 확정된 봉도
# 미확정으로 오판해 최신성이 떨어지고, 너무 작으면 반대로 아직 안 끝난 봉을 확정으로
# 오판할 위험이 커진다 - 2~5초 사이의 절충값으로 3초를 쓴다.
GRACE_SECONDS = 3


def is_last_candle_closed(last_open: datetime.datetime, timeframe: str,
                           now: datetime.datetime | None = None,
                           grace_seconds: float = GRACE_SECONDS) -> bool:
    # pandas의 timestamp 컬럼(pd.to_datetime(..., unit="ms"))이 naive UTC라서, 여기서도
    # naive로 맞춰야 비교가 가능하다(aware datetime과 비교하면 TypeError).
    now = now or datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    duration = TF_SECONDS[timeframe]
    expected_close = last_open + datetime.timedelta(seconds=duration)
    return expected_close <= now - datetime.timedelta(seconds=grace_seconds)


def split_live_closed(df, timeframe: str, now: datetime.datetime | None = None,
                       grace_seconds: float = GRACE_SECONDS):
    """반환: (live_df, closed_df, meta).
    live_df: 원본 df 그대로(현재 production과 동일 - 이 함수를 거쳐도 값이 바뀌지 않는다).
    closed_df: 마지막 캔들이 미확정이면 그 행만 제거한 버전. 확정이면 live_df와 동일 내용."""
    last_open = df["timestamp"].iloc[-1].to_pydatetime()
    closed = is_last_candle_closed(last_open, timeframe, now, grace_seconds)
    closed_df = df if closed else df.iloc[:-1].reset_index(drop=True)
    return df, closed_df, {"last_open": last_open, "is_last_closed": closed}


# ---- divergence 판정(표시 전용) ----
# 아래 두 함수는 Shadow 로그에 "확정봉을 썼으면 판단이 달라질 여지가 있었는가"를
# 한눈에 보여주기 위한 것뿐이고, 실거래 판단에는 절대 쓰지 않는다.

def indicator_divergence(live_row: dict, closed_row: dict) -> bool:
    """live_row/closed_row: indicators.add_indicators()가 만든 df 마지막 row에서 뽑은 값.
    RSI 50 기준 방향, MACD 부호, EMA20 vs EMA50 대소관계 중 하나라도 다르면 True."""
    if (live_row["rsi_14"] >= 50) != (closed_row["rsi_14"] >= 50):
        return True
    if (live_row["macd"] >= 0) != (closed_row["macd"] >= 0):
        return True
    if (live_row["ema_20"] >= live_row["ema_50"]) != (closed_row["ema_20"] >= closed_row["ema_50"]):
        return True
    return False


def structure_divergence(live_struct: dict, closed_struct: dict) -> bool:
    """market_structure.compute() 결과 두 개를 비교. 핵심 필드 중 하나라도 다르면 True."""
    for field in ("low_structure", "high_structure", "swing_low_broken", "swing_high_broken"):
        if live_struct.get(field) != closed_struct.get(field):
            return True
    return False
