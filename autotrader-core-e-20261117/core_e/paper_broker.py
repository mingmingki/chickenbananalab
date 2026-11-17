"""모의 체결기. 실제 거래소에는 아무 것도 보내지 않는다.

체결 규칙은 일부러 '불리하게' 잡는다 - 모의에서 벌어도 실전에서 잃는 일을 줄이려고.
- 지정가 매수(가격 P)는 P보다 '낮은' 체결이 찍혀야 잡힌 걸로 본다(P와 같은 가격은 줄 서 있다가
  못 잡혔을 수 있으므로 인정하지 않음). 매도는 반대.
- 새 주문은 판단 시각 + order_latency_ms 이후에 시작하는 봉부터만 체결될 수 있다.
- 손절은 거래소에 미리 걸어둔 주문이라고 보고 진입 즉시 유효. 가격이 손절가를 건너뛰어
  열리면(갭) 그 봉의 시가로 나간다. 손절·시간청산은 시장가라 taker 수수료 + 미끄러짐.
- 한 봉 안에서 손절가와 익절가가 둘 다 닿으면 손절로 처리한다.
- 진입이 잡힌 바로 그 봉에서 손절가까지 내려갔으면 그 봉에서 손절로 처리한다.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .bars import Bar
from .config import CoreEConfig


def round_down_tick(price: float, tick: float) -> float:
    return round(math.floor(price / tick + 1e-9) * tick, 10)


def round_up_tick(price: float, tick: float) -> float:
    return round(math.ceil(price / tick - 1e-9) * tick, 10)


@dataclass
class EntryOrder:
    side: str  # "long" | "short"
    price: float
    contracts: float
    placed_ts: int
    active_ts: int
    expire_ts: int
    signal: dict = field(default_factory=dict)


@dataclass
class Position:
    side: str
    entry_price: float
    contracts: float
    qty: float  # 코인 수량 = 계약 수 x ct_val
    open_ts: int
    stop_price: float
    tp_price: float
    tp_active_ts: int
    entry_fee: float
    market_exit_ts: int  # 이 시각 이후 첫 체결에서 시장가로 정리
    market_exit_reason: str
    signal: dict = field(default_factory=dict)


class PaperBroker:
    def __init__(self, cfg: CoreEConfig):
        self.cfg = cfg
        self.entry: EntryOrder | None = None
        self.position: Position | None = None

    @property
    def is_flat(self) -> bool:
        return self.entry is None and self.position is None

    def contracts_for(self, price: float) -> float:
        c = self.cfg
        raw = c.notional_usdt / (price * c.ct_val)
        lots = math.floor(raw / c.lot_size + 1e-9)
        return lots * c.lot_size

    def place_entry(self, side: str, price: float, now_ts: int, signal: dict | None = None) -> EntryOrder | None:
        if not self.is_flat:
            raise RuntimeError("이미 주문이나 포지션이 있는데 새 진입을 넣으려 함")
        if side not in ("long", "short"):
            raise ValueError(f"side는 long/short: {side!r}")
        contracts = self.contracts_for(price)
        if contracts <= 0:
            return None
        active = now_ts + self.cfg.order_latency_ms
        self.entry = EntryOrder(
            side=side,
            price=price,
            contracts=contracts,
            placed_ts=now_ts,
            active_ts=active,
            expire_ts=active + int(self.cfg.entry_timeout_sec * 1000),
            signal=dict(signal or {}),
        )
        return self.entry

    def cancel_entry(self, ts: int, reason: str) -> dict | None:
        e = self.entry
        if e is None:
            return None
        self.entry = None
        return {"type": "entry_cancelled", "ts": ts, "side": e.side, "price": e.price, "reason": reason}

    def schedule_market_exit(self, now_ts: int, reason: str) -> None:
        """포지션을 다음 체결에서 시장가로 정리하도록 예약(이미 더 빠른 예약이 있으면 유지)."""
        p = self.position
        if p is None:
            return
        at = now_ts + self.cfg.order_latency_ms
        if at < p.market_exit_ts:
            p.market_exit_ts = at
            p.market_exit_reason = reason

    def close_at_price(self, ts: int, price: float, reason: str) -> dict | None:
        """더 이상 체결 데이터가 오지 않을 때(종료 등) 마지막 가격으로 시장가 정리."""
        p = self.position
        if p is None:
            return None
        return self._close(p, ts, self._slipped(p, price), reason, self.cfg.taker_fee_bp, "taker")

    def on_bar(self, bar: Bar) -> list[dict]:
        events: list[dict] = []
        e = self.entry
        if e is not None:
            if bar.start >= e.expire_ts:
                events.append(self.cancel_entry(e.expire_ts, "timeout"))
            elif bar.start >= e.active_ts and _crosses(e.side, e.price, bar):
                self.entry = None
                p = self._open(e, bar)
                events.append({
                    "type": "entry_filled", "ts": bar.end, "side": p.side, "price": p.entry_price,
                    "contracts": p.contracts, "stop_price": p.stop_price, "tp_price": p.tp_price,
                })
                if _stop_hit(p, bar):
                    events.append(self._close(p, bar.end, self._slipped(p, p.stop_price), "stop_loss",
                                              self.cfg.taker_fee_bp, "taker"))
                return events
        if self.position is not None:
            closed = self._manage(self.position, bar)
            if closed is not None:
                events.append(closed)
        return events

    # --- 내부 ---------------------------------------------------------------

    def _open(self, e: EntryOrder, bar: Bar) -> Position:
        c = self.cfg
        qty = e.contracts * c.ct_val
        if e.side == "long":
            stop = round_down_tick(e.price * (1 - c.stop_loss_bp / 1e4), c.tick_size)
            tp = round_up_tick(e.price * (1 + c.take_profit_bp / 1e4), c.tick_size)
        else:
            stop = round_up_tick(e.price * (1 + c.stop_loss_bp / 1e4), c.tick_size)
            tp = round_down_tick(e.price * (1 - c.take_profit_bp / 1e4), c.tick_size)
        open_ts = bar.end
        self.position = Position(
            side=e.side,
            entry_price=e.price,
            contracts=e.contracts,
            qty=qty,
            open_ts=open_ts,
            stop_price=stop,
            tp_price=tp,
            tp_active_ts=open_ts + c.order_latency_ms,
            entry_fee=e.price * qty * c.maker_fee_bp / 1e4,
            market_exit_ts=open_ts + int(c.max_hold_sec * 1000) + c.order_latency_ms,
            market_exit_reason="time_stop",
            signal=e.signal,
        )
        return self.position

    def _manage(self, p: Position, bar: Bar) -> dict | None:
        c = self.cfg
        if _stop_hit(p, bar):
            if p.side == "long":
                trigger = bar.open if bar.open < p.stop_price else p.stop_price
            else:
                trigger = bar.open if bar.open > p.stop_price else p.stop_price
            return self._close(p, bar.end, self._slipped(p, trigger), "stop_loss", c.taker_fee_bp, "taker")
        if bar.start >= p.tp_active_ts:
            exit_side = "short" if p.side == "long" else "long"
            if _crosses(exit_side, p.tp_price, bar):
                return self._close(p, bar.end, p.tp_price, "take_profit", c.maker_fee_bp, "maker")
        if bar.start >= p.market_exit_ts:
            return self._close(p, bar.end, self._slipped(p, bar.open), p.market_exit_reason,
                               c.taker_fee_bp, "taker")
        return None

    def _slipped(self, p: Position, price: float) -> float:
        slip = self.cfg.taker_slippage_bp / 1e4
        return price * (1 - slip) if p.side == "long" else price * (1 + slip)

    def _close(self, p: Position, ts: int, exit_price: float, reason: str, fee_bp: float, liquidity: str) -> dict:
        direction = 1.0 if p.side == "long" else -1.0
        gross = (exit_price - p.entry_price) * p.qty * direction
        exit_fee = exit_price * p.qty * fee_bp / 1e4
        fees = p.entry_fee + exit_fee
        net = gross - fees
        entry_notional = p.entry_price * p.qty
        trade = {
            "symbol": self.cfg.symbol,
            "side": p.side,
            "entry_ts": p.open_ts,
            "exit_ts": ts,
            "hold_sec": round((ts - p.open_ts) / 1000.0, 3),
            "entry_price": p.entry_price,
            "exit_price": round(exit_price, 10),
            "contracts": p.contracts,
            "qty": round(p.qty, 10),
            "notional": round(entry_notional, 6),
            "gross": round(gross, 6),
            "fees": round(fees, 6),
            "net": round(net, 6),
            "net_bp": round(net / entry_notional * 1e4, 4) if entry_notional else 0.0,
            "exit_reason": reason,
            "exit_liquidity": liquidity,
            "signal": p.signal,
        }
        self.position = None
        return {"type": "closed", "ts": ts, "trade": trade}


def _crosses(side: str, price: float, bar: Bar) -> bool:
    """side 방향 지정가(price)가 이 봉에서 '건너서' 체결됐는지."""
    if side == "long":
        return bar.low < price
    return bar.high > price


def _stop_hit(p: Position, bar: Bar) -> bool:
    if p.side == "long":
        return bar.low <= p.stop_price
    return bar.high >= p.stop_price
