"""Phase 3.3 - backtest_engine.py와 portfolio_mtm_engine.py가 공유하는 단일
실행비용 회계 계약.

핵심 불변식: `_apply_price_costs()`가 만든 effective_entry/effective_exit
가격에는 이미 spread+slippage가 불리한 방향으로 반영돼 있다. 따라서
effective 가격으로 계산한 execution_adjusted_pnl에는 왕복 spread+slippage
비용이 이미 포함돼 있고, spread_cost_usdt/slippage_cost_usdt는 그 내역을
보여주는 "설명용 attribution" 필드일 뿐이다 - net_pnl 계산에서 다시
차감하면 안 된다(그렇게 하면 이중차감이 된다 - Phase 3.2/3.3에서 실제로
발견된 버그).

net_pnl = execution_adjusted_pnl - entry_fee_usdt - exit_fee_usdt + funding_pnl_usdt
        = raw_market_pnl - spread_cost_usdt - slippage_cost_usdt
          - entry_fee_usdt - exit_fee_usdt + funding_pnl_usdt
"""
from __future__ import annotations

from dataclasses import dataclass

SIDE_SIGN = {"long": 1.0, "short": -1.0}


@dataclass(frozen=True)
class CostBreakdown:
    raw_market_pnl: float
    execution_adjusted_pnl: float
    spread_cost_usdt: float
    slippage_cost_usdt: float
    entry_fee_usdt: float
    exit_fee_usdt: float
    funding_pnl_usdt: float
    net_pnl: float


def compute_cost_breakdown(
    *,
    side: str,
    quantity: float,
    raw_entry_price: float,
    raw_exit_price: float,
    effective_entry_price: float,
    effective_exit_price: float,
    entry_spread_cost_per_unit: float,
    entry_slippage_cost_per_unit: float,
    exit_spread_cost_per_unit: float,
    exit_slippage_cost_per_unit: float,
    entry_fee_usdt: float,
    exit_fee_usdt: float,
    funding_pnl_usdt: float,
) -> CostBreakdown:
    """quantity는 청산되는 실제 수량(base_quantity 또는 contracts*ctVal, 부분청산이면
    그 청산 비율만큼 이미 축소된 값)이어야 한다 - 진입/청산 양쪽에 동일 quantity를
    적용한다(기존 두 엔진 모두 이 전제를 이미 따르고 있음)."""
    sign = SIDE_SIGN[side]
    raw_market_pnl = sign * quantity * (raw_exit_price - raw_entry_price)
    execution_adjusted_pnl = sign * quantity * (effective_exit_price - effective_entry_price)
    spread_cost_usdt = (entry_spread_cost_per_unit + exit_spread_cost_per_unit) * quantity
    slippage_cost_usdt = (entry_slippage_cost_per_unit + exit_slippage_cost_per_unit) * quantity
    net_pnl = execution_adjusted_pnl - entry_fee_usdt - exit_fee_usdt + funding_pnl_usdt
    return CostBreakdown(
        raw_market_pnl=raw_market_pnl,
        execution_adjusted_pnl=execution_adjusted_pnl,
        spread_cost_usdt=spread_cost_usdt,
        slippage_cost_usdt=slippage_cost_usdt,
        entry_fee_usdt=entry_fee_usdt,
        exit_fee_usdt=exit_fee_usdt,
        funding_pnl_usdt=funding_pnl_usdt,
        net_pnl=net_pnl,
    )
