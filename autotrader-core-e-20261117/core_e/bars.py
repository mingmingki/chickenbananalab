"""체결(trade)을 아주 짧은 시간 단위(기본 100ms) 묶음 봉으로 만든다.

백테스트와 실시간 모의 매매가 같은 Bucketer를 써서, 두 경로가 똑같은 입력을 보게 한다.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class Bar:
    start: int  # 이 봉이 덮는 구간 시작(ms, 포함)
    end: int  # 구간 끝(ms, 미포함) - 이 시각에 봉이 완성됐다고 본다
    open: float
    high: float
    low: float
    close: float
    buy_vol: float  # 시장가 매수 체결량(계약 수)
    sell_vol: float  # 시장가 매도 체결량(계약 수)
    count: int


class Bucketer:
    """체결을 하나씩 넣으면, 다음 구간의 체결이 들어오는 순간 이전 봉을 돌려준다."""

    def __init__(self, bar_ms: int):
        if bar_ms <= 0:
            raise ValueError("bar_ms는 0보다 커야 합니다")
        self.bar_ms = int(bar_ms)
        self._bar: Bar | None = None

    def add(self, ts: int, price: float, size: float, side: str) -> Bar | None:
        start = (int(ts) // self.bar_ms) * self.bar_ms
        current = self._bar
        if current is not None and start <= current.start:
            # 같은 구간(또는 거래소가 조금 늦게 보낸 체결)은 진행 중인 봉에 합친다
            _merge(current, price, size, side)
            return None
        self._bar = _new_bar(start, self.bar_ms, price, size, side)
        return current

    def flush(self) -> Bar | None:
        current, self._bar = self._bar, None
        return current


def _new_bar(start: int, bar_ms: int, price: float, size: float, side: str) -> Bar:
    buy = size if side == "buy" else 0.0
    sell = size if side != "buy" else 0.0
    return Bar(start, start + bar_ms, price, price, price, price, buy, sell, 1)


def _merge(bar: Bar, price: float, size: float, side: str) -> None:
    if price > bar.high:
        bar.high = price
    if price < bar.low:
        bar.low = price
    bar.close = price
    if side == "buy":
        bar.buy_vol += size
    else:
        bar.sell_vol += size
    bar.count += 1
