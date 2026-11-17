"""Core E 엔진: 봉이 들어올 때마다 체결 처리 -> 흐름 계산 -> 진입 판단.

AI를 부르지 않는다. 모든 판단은 규칙으로 즉시 끝난다. 시간은 벽시계가 아니라 들어온
데이터의 시각만 쓴다 - 그래서 백테스트와 실시간 모의가 같은 코드를 탄다.
"""
from __future__ import annotations

from collections import Counter
from typing import Callable

from .bars import Bar
from .config import CoreEConfig
from .features import FlowFeatures, FlowSnapshot
from .paper_broker import PaperBroker, round_down_tick, round_up_tick

KST_OFFSET_MS = 9 * 3600 * 1000
DAY_MS = 86_400_000


def kst_day(ts_ms: int) -> int:
    return (int(ts_ms) + KST_OFFSET_MS) // DAY_MS


class RiskGuard:
    """하루 손실 한도, 하루 최대 거래 수, 연속 손실 시 일시 정지."""

    def __init__(self, cfg: CoreEConfig):
        self.cfg = cfg
        self.day: int | None = None
        self.day_net = 0.0
        self.day_trades = 0
        self.consecutive_losses = 0
        self.paused_until = 0

    def _roll(self, ts: int) -> None:
        d = kst_day(ts)
        if d != self.day:
            self.day = d
            self.day_net = 0.0
            self.day_trades = 0

    def can_enter(self, ts: int) -> tuple[bool, str]:
        self._roll(ts)
        if ts < self.paused_until:
            return False, "loss_pause"
        if self.day_net <= -self.cfg.daily_loss_limit_usdt:
            return False, "daily_loss_limit"
        if self.day_trades >= self.cfg.max_trades_per_day:
            return False, "max_trades_per_day"
        return True, ""

    def record(self, trade: dict) -> None:
        ts = int(trade["exit_ts"])
        self._roll(ts)
        self.day_net += float(trade["net"])
        self.day_trades += 1
        if float(trade["net"]) < 0:
            self.consecutive_losses += 1
            if self.consecutive_losses >= self.cfg.max_consecutive_losses:
                self.paused_until = ts + int(self.cfg.loss_pause_sec * 1000)
                self.consecutive_losses = 0
        else:
            self.consecutive_losses = 0

    def snapshot(self) -> dict:
        return {
            "kst_day": self.day,
            "day_net": round(self.day_net, 6),
            "day_trades": self.day_trades,
            "consecutive_losses": self.consecutive_losses,
            "paused_until": self.paused_until,
        }


class CoreEEngine:
    def __init__(self, cfg: CoreEConfig, event_sink: Callable[[dict], None] | None = None):
        self.cfg = cfg
        self.broker = PaperBroker(cfg)
        self.features = FlowFeatures(cfg.window_sec, cfg.baseline_sec)
        self.risk = RiskGuard(cfg)
        self.cooldown_until = 0
        self.closed_trades: list[dict] = []
        self.stats: Counter = Counter()
        self.last_bar: Bar | None = None
        self._sink = event_sink

    # --- 입력 ---------------------------------------------------------------

    def on_bar(self, bar: Bar) -> None:
        for ev in self.broker.on_bar(bar):
            self._handle(ev)
        self.features.push(bar)
        self.last_bar = bar
        self._maybe_enter(bar.end)

    def on_gap(self, ts: int, reason: str = "feed_gap") -> None:
        """데이터가 끊겼다 다시 이어질 때: 걸어둔 진입은 취소하고, 흐름 계산은 처음부터 다시
        쌓는다(평소 거래량을 다시 알 때까지 새 진입 없음). 열린 포지션의 손절/익절은 유지."""
        self.stats["gaps"] += 1
        ev = self.broker.cancel_entry(ts, reason)
        if ev is not None:
            self._handle(ev)
        self.features.reset()
        self._emit({"type": "gap", "ts": ts, "reason": reason})

    def shutdown(self, ts: int, reason: str = "shutdown") -> None:
        """종료: 걸어둔 진입 취소, 열린 포지션은 마지막 가격에 시장가로 정리(모의)."""
        ev = self.broker.cancel_entry(ts, reason)
        if ev is not None:
            self._handle(ev)
        if self.broker.position is not None and self.last_bar is not None:
            ev = self.broker.close_at_price(ts, self.last_bar.close, reason)
            if ev is not None:
                self._handle(ev)

    # --- 판단 ---------------------------------------------------------------

    def signal_side(self, snap: FlowSnapshot) -> str | None:
        c = self.cfg
        if snap.volume_ratio < c.min_volume_ratio:
            return None
        if c.min_move_bp <= snap.move_bp <= c.max_move_bp and snap.imbalance >= c.min_imbalance:
            burst = "up"
        elif -c.max_move_bp <= snap.move_bp <= -c.min_move_bp and snap.imbalance <= -c.min_imbalance:
            burst = "down"
        else:
            return None
        if c.style == "momentum":
            return "long" if burst == "up" else "short"
        return "short" if burst == "up" else "long"

    def entry_price(self, side: str, last_price: float) -> float:
        c = self.cfg
        off = c.entry_offset_bp / 1e4
        if side == "long":
            return round_down_tick(last_price * (1 - off), c.tick_size)
        return round_up_tick(last_price * (1 + off), c.tick_size)

    def _maybe_enter(self, now: int) -> None:
        if not self.broker.is_flat or now < self.cooldown_until:
            return
        snap = self.features.snapshot(now)
        if snap is None:
            return
        side = self.signal_side(snap)
        if side is None:
            return
        ok, why = self.risk.can_enter(now)
        if not ok:
            self.stats["blocked_" + why] += 1
            return
        self.stats["signals"] += 1
        signal = {
            "move_bp": round(snap.move_bp, 3),
            "imbalance": round(snap.imbalance, 4),
            "volume_ratio": round(snap.volume_ratio, 3),
            "price": snap.price,
        }
        order = self.broker.place_entry(side, self.entry_price(side, snap.price), now, signal)
        if order is None:
            self.stats["skipped_size_too_small"] += 1
            return
        self.stats["entries_placed"] += 1
        self._emit({
            "type": "entry_placed", "ts": now, "side": side, "price": order.price,
            "contracts": order.contracts, "expire_ts": order.expire_ts, "signal": signal,
        })

    # --- 이벤트 ---------------------------------------------------------------

    def _handle(self, ev: dict) -> None:
        kind = ev["type"]
        if kind == "entry_filled":
            self.stats["entries_filled"] += 1
        elif kind == "entry_cancelled":
            self.stats["entries_cancelled"] += 1
        elif kind == "closed":
            trade = ev["trade"]
            self.closed_trades.append(trade)
            self.risk.record(trade)
            self.cooldown_until = int(trade["exit_ts"]) + int(self.cfg.cooldown_sec * 1000)
            self.stats["closed_" + trade["exit_reason"]] += 1
        self._emit(ev)

    def _emit(self, ev: dict) -> None:
        if self._sink is not None:
            self._sink(ev)

    def status(self) -> dict:
        b = self.broker
        pos = b.position
        entry = b.entry
        return {
            "symbol": self.cfg.symbol,
            "mode": self.cfg.mode,
            "last_bar_end": self.last_bar.end if self.last_bar else None,
            "last_price": self.last_bar.close if self.last_bar else None,
            "warmed_up": self.features.snapshot(self.last_bar.end) is not None if self.last_bar else False,
            "state": "open" if pos else ("entry_pending" if entry else "flat"),
            "position": None if pos is None else {
                "side": pos.side, "entry_price": pos.entry_price, "contracts": pos.contracts,
                "stop_price": pos.stop_price, "tp_price": pos.tp_price, "open_ts": pos.open_ts,
            },
            "entry_order": None if entry is None else {
                "side": entry.side, "price": entry.price, "expire_ts": entry.expire_ts,
            },
            "risk": self.risk.snapshot(),
            "closed_trades": len(self.closed_trades),
            "total_net": round(sum(t["net"] for t in self.closed_trades), 6),
            "stats": dict(self.stats),
        }
