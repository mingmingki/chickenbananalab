"""Phase 2.1 항목4 - 다중 타임프레임 as-of join, look-ahead 원천 차단(2026-09-01).

여러 타임프레임(1m/3m/5m/10m/15m/1H/4H/1D)을 한 판단 시점(as_of_ms)에 정렬할 때,
"그 시점에 이미 확정된 봉"만 보이게 강제한다. 상위 타임프레임(예: 15m/4H)의
아직 안 끝난 봉이 하위 타임프레임(예: 5m/1m) 시점의 판단에 새어 들어가는
look-ahead 편향을 코드 수준에서 막는 게 유일한 목적이다 - 전략 판단 로직 자체는
전혀 포함하지 않는다.

판단 기준은 각 bar의 close_time_ms뿐이다(open_time_ms + 해당 타임프레임 step - 1,
market_data_store.TIMEFRAME_STEP_MS 기준) - "그 시각 이전에 실제로 종가가
확정됐는가"만 본다."""
from __future__ import annotations

import market_data_store as mds


class FutureBarAccessError(Exception):
    """요청한 bar가 as_of_ms 시점에는 아직 확정(종가 마감)되지 않았을 때."""

    def __init__(self, timeframe: str, open_time_ms: int, as_of_ms: int):
        self.timeframe = timeframe
        self.open_time_ms = open_time_ms
        self.as_of_ms = as_of_ms
        super().__init__(
            f"{timeframe} bar(open_time_ms={open_time_ms})는 as_of={as_of_ms} 시점에 아직 확정되지 않음"
        )


class MissingConfirmedBarError(Exception):
    """요청한 시점까지 해당 타임프레임에 확정된 bar가 하나도 없거나(데이터 결측),
    특정 open_time_ms의 bar 자체가 존재하지 않을 때(하위봉 결측)."""

    def __init__(self, timeframe, reference):
        self.timeframe = timeframe
        self.reference = reference
        super().__init__(f"{timeframe}에 확정된 bar 없음(reference={reference})")


class MultiTimeframeView:
    """bars_by_timeframe: {"1m": [...], "5m": [...], ...} - 각 리스트는 confirm 필드를
    포함한 정규화 캔들(market_data_store 스키마) 리스트, 정렬 여부는 무관(내부에서
    open_time_ms로 인덱싱)."""

    def __init__(self, bars_by_timeframe: dict[str, list[dict]], as_of_ms: int):
        self._by_tf = {
            tf: {row["open_time_ms"]: row for row in rows}
            for tf, rows in bars_by_timeframe.items()
        }
        self._as_of_ms = as_of_ms

    def latest_confirmed(self, timeframe: str) -> dict:
        """as_of 시점에 이미 확정된 이 타임프레임의 가장 최근 bar. 확정된 게 하나도
        없으면(그 타임프레임 데이터가 아직 시작 전이거나 전부 결측) 실패한다 -
        가장 가까운 값으로 대충 채우지 않는다."""
        rows = self._by_tf.get(timeframe, {})
        candidates = [
            row for row in rows.values()
            if row.get("confirm") == 1 and row["close_time_ms"] <= self._as_of_ms
        ]
        if not candidates:
            raise MissingConfirmedBarError(timeframe, self._as_of_ms)
        return max(candidates, key=lambda r: r["open_time_ms"])

    def bar_covering(self, timeframe: str, open_time_ms: int) -> dict:
        """open_time_ms에서 시작하는 그 타임프레임의 특정 bar를 요청한다. 아직
        확정 시각(close_time_ms)이 as_of_ms를 넘지 않았으면(=아직 안 끝났으면)
        FutureBarAccessError - "미래 상위봉을 조회하려는 시도" 자체를 여기서 막는다."""
        step_ms = mds.TIMEFRAME_STEP_MS[timeframe]
        expected_close_ms = open_time_ms + step_ms - 1
        if expected_close_ms > self._as_of_ms:
            raise FutureBarAccessError(timeframe, open_time_ms, self._as_of_ms)
        row = self._by_tf.get(timeframe, {}).get(open_time_ms)
        if row is None or row.get("confirm") != 1:
            raise MissingConfirmedBarError(timeframe, open_time_ms)
        return row

    def snapshot(self, timeframes: list[str], *, required: list[str] | None = None) -> dict:
        """지정한 타임프레임들 각각의 "as_of 시점 최신 확정 bar"를 한 번에 모은다.
        확정된 게 없는 타임프레임은 None으로 채운다 - required에 나열된 타임프레임
        중 하나라도 None이면 전체를 fail-closed(MissingConfirmedBarError)한다.

        순수 함수(같은 bars_by_timeframe+as_of_ms 입력이면 항상 같은 결과) - wall
        clock/random 의존 없음."""
        result = {}
        for tf in timeframes:
            try:
                result[tf] = self.latest_confirmed(tf)
            except MissingConfirmedBarError:
                result[tf] = None
        if required:
            missing = [tf for tf in required if result.get(tf) is None]
            if missing:
                raise MissingConfirmedBarError(missing, self._as_of_ms)
        return result
