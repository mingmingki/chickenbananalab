"""okx_client.fetch_ohlcv_df()가 반환하는 pandas DataFrame을
candidate_c_decision_engine.decide()가 요구하는 확정봉 dict 리스트로 변환한다.

candle_finality.split_live_closed()를 재사용한다 - CORE 자신의 LIVE 의사결정에는
아직 쓰이지 않지만(그 모듈 자체 docstring에 명시됨, Shadow 관찰용으로 도입됨),
"타임스탬프 + 타임프레임 길이로 마지막 캔들이 확정됐는지 판정"하는 로직 자체는
독립적으로 올바르고 이미 테스트돼 있다. Candidate C의 decide()는 100% 결정론적
전략이라 확정봉만 쓰는 게 선택이 아니라 필수 요건이다 - 진행 중인 봉을 섞으면
같은 입력에 대해 재현 불가능한 신호(look-ahead/자기참조 위험)가 나올 수 있다."""
import candle_finality


def confirmed_bars_from_df(df, timeframe: str, now=None, *, symbol: str = 'UNKNOWN') -> list:
    """반환: decide()가 요구하는 dict 리스트(open_time_ms/close_time_ms/open/high/
    low/close/confirm), open_time_ms 오름차순, 확정된 캔들만 포함한다."""
    _, closed_df, _ = candle_finality.split_live_closed(df, timeframe, now=now)
    duration_ms = candle_finality.TF_SECONDS[timeframe] * 1000
    bars = []
    for _, row in closed_df.iterrows():
        # timestamp 컬럼은 naive UTC로 취급된다(candle_finality.py 자체 문서화 -
        # "pandas의 timestamp 컬럼이 naive UTC라서"). Timestamp.timestamp()는 naive
        # 값을 로컬 타임존으로 해석해서 변환하므로(서버가 KST면 9시간 어긋남), 반드시
        # .value(naive wall-clock을 그대로 UTC epoch 나노초로 보는 내부 표현)를 써야
        # 서버 타임존과 무관하게 항상 올바른 epoch ms가 나온다.
        open_time_ms = int(row["timestamp"].value // 1_000_000)
        bars.append({
            "inst_id": symbol, "timeframe": timeframe, "price_type": "trade",
            "open_time_ms": open_time_ms,
            "close_time_ms": open_time_ms + duration_ms,
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "confirm": 1,
        })
    return bars
