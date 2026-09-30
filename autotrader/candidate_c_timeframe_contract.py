"""Phase 3.5 Task 3 - Candidate C의 시간봉 책임/as-of 계약.

candidate_c_preregistration_v2.json에 명시된 시간봉 책임을 그대로 코드화한다:
1D=telemetry전용, 4H=유일한 방향, 1H=구조약화, 10m=setup(확정 5분봉 2개로만
합성), 5m=유일한 action clock. 1m/3m는 이 모듈이 아예 다루지 않는다 - 함수
시그니처 자체에 파라미터가 없으므로 구조적으로 방향/action에 영향을 줄 수
없다(별도 안전감시 모듈에서만 다룸, Task 3 범위 밖).

미확정/미래/결측 데이터는 기존 mtf_asof.py(Phase 2.1)와 derived_candles.py
(Phase 2.1)를 그대로 재사용해 fail-closed로 처리한다 - 새 예외 타입을 만들지
않는다."""
from __future__ import annotations

from dataclasses import dataclass

import derived_candles as dc
import mtf_asof

STEP_5M = 5 * 60 * 1000
STEP_10M = 10 * 60 * 1000
DONCHIAN_LOOKBACK_10M = 20


class ActionTimestampNotConfirmedError(Exception):
    """action clock에 넘겨진 시점이 confirm==1인 5분봉이 아닐 때."""


@dataclass(frozen=True)
class AsOfSnapshot:
    as_of_ms: int
    bar_5m: dict
    bar_4h: dict
    bar_1h: dict
    bar_1d: dict | None  # telemetry 전용 - 없어도(None) action을 막지 않음
    bar_10m_current: dict
    bars_10m_prior_20: list  # 오래된 것 -> 최신 순, 정확히 20개(Donchian 채널 계산용, current 제외)


def confirmed_action_ticks(five_min_bars: list[dict]) -> list[dict]:
    """confirm==1인 5분봉만, open_time_ms 오름차순, 중복 timestamp는 1개로
    합친다(같은 timestamp가 몇 번 재수신되든 action은 정확히 1회)."""
    by_ts: dict[int, dict] = {}
    for bar in five_min_bars:
        if bar.get("confirm") != 1:
            continue
        by_ts[bar["open_time_ms"]] = bar
    return [by_ts[ts] for ts in sorted(by_ts)]


def build_as_of_snapshot(
    as_of_ms: int, *,
    bars_4h: list[dict], bars_1h: list[dict], bars_1d: list[dict],
    bars_5m_for_10m: list[dict],
) -> AsOfSnapshot:
    """as_of_ms는 반드시 confirmed 5m bar의 open_time_ms여야 한다(호출부가
    confirmed_action_ticks()로 걸러낸 값만 넘겨야 함 - 여기서도 방어적으로 재검증)."""
    bar_5m = next(
        (b for b in bars_5m_for_10m if b["open_time_ms"] == as_of_ms and b.get("confirm") == 1),
        None,
    )
    if bar_5m is None:
        raise ActionTimestampNotConfirmedError(f"as_of_ms={as_of_ms}는 확정 5분봉이 아님")
    # The open timestamp identifies the action bar; its values become
    # available only at its close. HTF/10m joins use that decision time.
    as_of_ms = bar_5m["close_time_ms"]

    bar_4h = mtf_asof.MultiTimeframeView({"4H": bars_4h}, as_of_ms).latest_confirmed("4H")
    bar_1h = mtf_asof.MultiTimeframeView({"1H": bars_1h}, as_of_ms).latest_confirmed("1H")

    try:
        bar_1d = mtf_asof.MultiTimeframeView({"1D": bars_1d}, as_of_ms).latest_confirmed("1D")
    except Exception:
        bar_1d = None  # telemetry 전용이므로 없어도 fail-closed하지 않는다.

    visible_5m = [b for b in bars_5m_for_10m if b.get("confirm") == 1 and b["close_time_ms"] <= as_of_ms]
    ten_min_rows, _ = dc.derive_10m_from_5m(visible_5m)
    view_10m = mtf_asof.MultiTimeframeView({"10m": ten_min_rows}, as_of_ms)
    bar_10m_current = view_10m.latest_confirmed("10m")

    prior_open_times = [
        bar_10m_current["open_time_ms"] - STEP_10M * i
        for i in range(DONCHIAN_LOOKBACK_10M, 0, -1)
    ]
    bars_10m_prior_20 = [view_10m.bar_covering("10m", ts) for ts in prior_open_times]

    return AsOfSnapshot(
        as_of_ms=as_of_ms, bar_5m=bar_5m, bar_4h=bar_4h, bar_1h=bar_1h, bar_1d=bar_1d,
        bar_10m_current=bar_10m_current, bars_10m_prior_20=bars_10m_prior_20,
    )


def donchian_setup_condition(snapshot: AsOfSnapshot, side: str) -> bool:
    """candidate_c_preregistration_v2.json 5.3 - 현재 확정 10분봉을 제외한 이전
    20개 확정 10분봉으로 Donchian boundary를 계산한다(현재 10분봉은 채널
    계산에서 제외 - Task 4 요구사항). side는 'long' 또는 'short'."""
    prior_highs = [b["high"] for b in snapshot.bars_10m_prior_20]
    prior_lows = [b["low"] for b in snapshot.bars_10m_prior_20]
    if side == "long":
        return snapshot.bar_10m_current["close"] > max(prior_highs)
    if side == "short":
        return snapshot.bar_10m_current["close"] < min(prior_lows)
    raise ValueError(f"알 수 없는 side: {side}")


def check_staleness(snapshot: AsOfSnapshot, *, max_staleness_ms_by_tf: dict[str, int]) -> dict:
    """snapshot.as_of_ms 기준, 지정된 각 타임프레임의 "최신 확정 bar가 이미 얼마나
    오래됐는지"를 확인한다. 신규진입 차단 여부는 이 함수를 호출하는 쪽(Task 5)의
    정책이고, 이 함수는 순수하게 staleness 사실만 보고한다."""
    bar_by_tf = {
        "4H": snapshot.bar_4h, "1H": snapshot.bar_1h,
        "10m": snapshot.bar_10m_current, "1D": snapshot.bar_1d,
    }
    stale_timeframes = []
    ages_ms = {}
    for tf, max_age in max_staleness_ms_by_tf.items():
        bar = bar_by_tf.get(tf)
        if bar is None:
            stale_timeframes.append(tf)
            ages_ms[tf] = None
            continue
        age = snapshot.as_of_ms - bar["close_time_ms"]
        ages_ms[tf] = age
        if age < 0 or age > max_age:
            stale_timeframes.append(tf)
    return {"stale": len(stale_timeframes) > 0, "stale_timeframes": stale_timeframes, "ages_ms": ages_ms}
