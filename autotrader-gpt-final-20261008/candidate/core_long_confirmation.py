"""CORE 신규 LONG 진입 전용 1H minimum confirmation gate.

2026-09-21 변경: 3m/5m 구조(HH/HL/LH/LL)는 더 이상 로컬 hard veto가 아니다.
Gemini가 LONG 후보를 만들고 아래 1H 최소 조건을 통과하면, 3m/5m 구조는 GPT Entry
Gate에 advisory context로 전달되어 GPT가 최종 타이밍을 approve_now/wait/reject로
판단한다. 이로써 1H 상위 구조는 보호하면서, 눌림목 중 일시적인 5m LH/LL 하나 때문에
GPT에게 판단 기회조차 주지 않는 문제를 제거한다.

하드 허용 조건:
    (1H 종가 >= 1H EMA20) OR (1H MACD 히스토그램이 최근 종료된 2개 캔들 연속 개선)

반드시 완전히 종료된(candle_finality.split_live_closed로 이미 분리된) 1H 캔들만
사용한다 - 진행 중인 캔들은 절대 쓰지 않는다."""
import pandas as pd


def macd_histogram_improved_for_last_n(closed_1h_df: pd.DataFrame, n: int = 2) -> bool | None:
    """MACD 히스토그램(macd - macd_signal)이 마지막 n개 캔들에서 매번 직전 값보다
    커졌는지(연속 개선). 판단에 필요한 n+1개 행이 없으면 None(호출부가 fail-closed로
    처리)."""
    if closed_1h_df is None or len(closed_1h_df) < n + 1:
        return None
    hist = (closed_1h_df["macd"] - closed_1h_df["macd_signal"]).tail(n + 1).tolist()
    return all(hist[i] > hist[i - 1] for i in range(1, len(hist)))


def _bullish_structure(structure: dict | None) -> bool:
    if not structure:
        return False
    return structure.get("high_structure") == "HH" or structure.get("low_structure") == "HL"


def check_long_confirmation(
    closed_1h_df: pd.DataFrame | None, structure_3m: dict | None, structure_5m: dict | None,
) -> tuple[bool, str]:
    """(허용여부, 사유코드)를 반환한다. 데이터 부족은 안전하게 차단(fail-closed)한다."""
    if closed_1h_df is None or len(closed_1h_df) == 0:
        return False, "insufficient_1h_data"

    last = closed_1h_df.iloc[-1]
    close_ge_ema20 = bool(last["close"] >= last["ema_20"])
    macd_improved = bool(macd_histogram_improved_for_last_n(closed_1h_df, n=2))

    if not (close_ge_ema20 or macd_improved):
        return False, "close_below_ema20 and macd_hist_not_improving"

    # 3m/5m structure is intentionally advisory only.  It is passed to GPT by
    # trader._long_entry_timing_context() and must not veto a Gemini LONG here.
    return True, "ok_1h_minimum_confirmed"
