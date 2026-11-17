"""최근 체결 흐름 계산: 가격 변화, 매수/매도 쏠림, 평소 대비 거래량."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from .bars import Bar


class RollingWindow:
    """최근 span_ms 동안의 매수/매도 체결량 합계와, 그 구간 직전 가격(ref_price)."""

    def __init__(self, span_ms: float):
        self.span_ms = float(span_ms)
        self._q: deque = deque()
        self.buy = 0.0
        self.sell = 0.0
        self.ref_price: float | None = None
        self.first_end: int | None = None

    def push(self, bar: Bar) -> None:
        if self.first_end is None:
            self.first_end = bar.end
        self._q.append((bar.end, bar.buy_vol, bar.sell_vol, bar.close))
        self.buy += bar.buy_vol
        self.sell += bar.sell_vol
        cutoff = bar.end - self.span_ms
        q = self._q
        while q and q[0][0] <= cutoff:
            _, b, s, close = q.popleft()
            self.buy -= b
            self.sell -= s
            self.ref_price = close
        if self.buy < 1e-12:
            self.buy = 0.0
        if self.sell < 1e-12:
            self.sell = 0.0

    def covered(self, now_end: int) -> bool:
        """구간 전체를 덮을 만큼 데이터가 쌓였는지."""
        return self.first_end is not None and now_end - self.first_end >= self.span_ms

    @property
    def total(self) -> float:
        return self.buy + self.sell


@dataclass(frozen=True)
class FlowSnapshot:
    price: float
    move_bp: float
    imbalance: float
    volume_ratio: float


class FlowFeatures:
    def __init__(self, window_sec: float, baseline_sec: float):
        self.window_ms = window_sec * 1000.0
        self.baseline_ms = baseline_sec * 1000.0
        self.reset()

    def reset(self) -> None:
        self.window = RollingWindow(self.window_ms)
        self.baseline = RollingWindow(self.baseline_ms)
        self.last_close: float | None = None

    def push(self, bar: Bar) -> None:
        self.window.push(bar)
        self.baseline.push(bar)
        self.last_close = bar.close

    def snapshot(self, now_end: int) -> FlowSnapshot | None:
        """평소 거래량을 알 만큼(baseline_sec) 데이터가 쌓이기 전에는 None."""
        w, b = self.window, self.baseline
        if not b.covered(now_end) or w.ref_price is None or self.last_close is None:
            return None
        total = w.total
        expected = b.total * (self.window_ms / self.baseline_ms)
        if total <= 0 or expected <= 0:
            return None
        return FlowSnapshot(
            price=self.last_close,
            move_bp=(self.last_close / w.ref_price - 1.0) * 1e4,
            imbalance=(w.buy - w.sell) / total,
            volume_ratio=total / expected,
        )
