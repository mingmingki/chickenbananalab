"""Shadow candidate의 향후 가격 경로(TP/SL 중 무엇을 먼저 찍는지, MAE/MFE)를 추적한다.
새 OKX API 호출을 하지 않고, 매 cycle 이미 fetch되는 raw_dfs["1m"]만 재사용한다.

핵심 안전장치 두 가지:
1. look-ahead 오염 방지: candidate가 생성된 순간 "그때 이미 진행 중이던" 1분봉은 그
   앞부분(candidate 발생 전 가격)을 포함하므로 결과 평가에서 완전히 제외한다. 그 다음
   "완전히 새로 시작한" 1분봉부터만 TP/SL/MAE/MFE를 본다.
2. data gap을 절대 추측하지 않는다: 마지막으로 처리한 캔들과 이번에 새로 받은 캔들 사이에
   시간 연속성이 끊겼으면(예: 서비스 재시작으로 그 사이 캔들을 못 봄) TP_FIRST/SL_FIRST/
   NEITHER_24H 중 아무것도 단정하지 않고 DATA_GAP으로만 표시한다.
"""
import datetime

CANDLE_MS = 60_000
HORIZON_MS = 24 * 60 * 60 * 1000
CHECKPOINTS_MIN = (15, 30, 60, 120)


def floor_to_minute_ms(ts_ms: int) -> int:
    return (ts_ms // CANDLE_MS) * CANDLE_MS


def new_candidate_state(candidate_id: str, symbol: str, candidate_dt: datetime.datetime,
                         reference_price: float, side: str, sl_pct: float, tp_pct: float) -> dict:
    candidate_ts_ms = int(candidate_dt.timestamp() * 1000)
    if side == "long":
        sl_price = reference_price * (1 - sl_pct / 100)
        tp_price = reference_price * (1 + tp_pct / 100)
    else:
        sl_price = reference_price * (1 + sl_pct / 100)
        tp_price = reference_price * (1 - tp_pct / 100)
    return {
        "candidate_id": candidate_id,
        "symbol": symbol,
        "side": side,
        "candidate_ts": candidate_dt.isoformat(timespec="seconds"),
        "candidate_ts_ms": candidate_ts_ms,
        "reference_price": reference_price,
        "sl_price": sl_price,
        "tp_price": tp_price,
        "first_partial_candle_open_ms": floor_to_minute_ms(candidate_ts_ms),
        "first_partial_candle_skipped": False,
        "last_processed_candle_open_ms": None,
        "outcome_status": "OPEN",
        "data_gap": False,
        "mae_pct": 0.0,
        "mfe_pct": 0.0,
        "checkpoints": {str(m): None for m in CHECKPOINTS_MIN},
        "resolved_at_ms": None,
    }


def _excursion_pct(state: dict, low: float, high: float) -> tuple[float, float]:
    ref = state["reference_price"]
    if state["side"] == "long":
        mae = max(state["mae_pct"], (ref - low) / ref * 100)
        mfe = max(state["mfe_pct"], (high - ref) / ref * 100)
    else:
        mae = max(state["mae_pct"], (high - ref) / ref * 100)
        mfe = max(state["mfe_pct"], (ref - low) / ref * 100)
    return mae, mfe


def _hits(state: dict, low: float, high: float) -> tuple[bool, bool]:
    if state["side"] == "long":
        return (low <= state["sl_price"]), (high >= state["tp_price"])
    return (high >= state["sl_price"]), (low <= state["tp_price"])


def advance_candidate(state: dict, candles_1m: list) -> dict:
    """candles_1m: [[ts_ms, o, h, l, c, v], ...] 오름차순. 이미 처리된 구간을 포함해서
    넘겨도 안전하다(함수 내부에서 first_partial/last_processed 기준으로 걸러낸다).
    OPEN이 아닌 candidate는 그대로 반환(더 이상 갱신하지 않음)."""
    if state["outcome_status"] != "OPEN":
        return state
    if not candles_1m:
        return state

    candles = sorted(candles_1m, key=lambda c: c[0])

    # 1) candidate 발생 시점이 걸쳐있던 첫 1분봉은 확실히 스킵 표시만 하고 평가 대상에서 제외.
    usable = [c for c in candles if c[0] > state["first_partial_candle_open_ms"]]
    if any(c[0] == state["first_partial_candle_open_ms"] for c in candles):
        state["first_partial_candle_skipped"] = True
    if not usable:
        return state

    # 2) 이미 처리한 것보다 새로운 캔들만.
    last_processed = state["last_processed_candle_open_ms"]
    if last_processed is not None:
        usable = [c for c in usable if c[0] > last_processed]
    if not usable:
        return state

    # 3) 연속성 체크 - 다음에 와야 할 캔들과 실제로 받은 첫 캔들 사이에 빈틈이 있으면
    # 절대 추측하지 않고 DATA_GAP만 표시하고 이번 배치는 처리하지 않는다.
    expected_next = (last_processed + CANDLE_MS) if last_processed is not None else (state["first_partial_candle_open_ms"] + CANDLE_MS)
    if usable[0][0] > expected_next:
        state["data_gap"] = True
        return state

    for c in usable:
        ts_ms, o, h, l, close, v = c
        mae, mfe = _excursion_pct(state, l, h)
        state["mae_pct"], state["mfe_pct"] = mae, mfe

        hit_sl, hit_tp = _hits(state, l, h)
        if hit_sl and hit_tp:
            state["outcome_status"] = "AMBIGUOUS"
            state["resolved_at_ms"] = ts_ms
            state["last_processed_candle_open_ms"] = ts_ms
            return state
        if hit_tp:
            state["outcome_status"] = "TP_FIRST"
            state["resolved_at_ms"] = ts_ms
            state["last_processed_candle_open_ms"] = ts_ms
            return state
        if hit_sl:
            state["outcome_status"] = "SL_FIRST"
            state["resolved_at_ms"] = ts_ms
            state["last_processed_candle_open_ms"] = ts_ms
            return state

        state["last_processed_candle_open_ms"] = ts_ms

        elapsed_min = (ts_ms - state["candidate_ts_ms"]) / 60_000
        for m in CHECKPOINTS_MIN:
            key = str(m)
            if state["checkpoints"][key] is None and elapsed_min >= m:
                state["checkpoints"][key] = {"mae_pct": mae, "mfe_pct": mfe}

        if ts_ms - state["candidate_ts_ms"] >= HORIZON_MS:
            state["outcome_status"] = "NEITHER_24H"
            state["resolved_at_ms"] = ts_ms
            return state

    return state
