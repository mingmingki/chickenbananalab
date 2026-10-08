"""Phase 3.2 - equity 기반 포지션 사이징 + MTM 결합 포트폴리오 엔진(2026-09-02).

이 모듈은 전략(Candidate B-B3의 진입/청산 규칙, ATR 배수, regime 판정,
level-trigger 방식, 100% protective-stop 청산 설계, ranking_fn 공식)을 전혀
바꾸지 않는다 - candidate_b.py의 signal_fn을 그대로 재사용한다. 이 모듈이 새로
제공하는 것은 오직:

1. 그 순간의 실제 MTM equity와 실제 ATR 손절폭을 반영한 계약 수량 결정
   (backtest_engine.py의 "고정 1% 가상 손절폭" 버그를 대체 - Phase 3.1 감사에서
   확정한 root cause).
2. wallet_balance/equity/gross_notional을 선형 USDT 무기한선물 계약대로 계산하는
   단일 공유 계좌 원장(4종목이 하나의 원장을 공유).
3. 매 mark bar 확정 시점마다 기록하는 진짜 MTM 자본곡선(청산 시점에만 계단식으로
   기록하던 이전 방식과 다름).

용어 정의(사용자 지시 3번 - 데이터 흐름 감사 결과):
- wallet_balance: 시작자산 + 지금까지 확정된(실현) 손익 - 확정 수수료 + 확정 funding.
  미실현손익은 포함하지 않는다.
- unrealized_pnl(심볼별): side=="long"이면 contracts*ctVal*(mark-entry),
  short이면 contracts*ctVal*(entry-mark). entry/mark는 전부 effective(스프레드/
  슬리피지 반영 후) 가격이 아니라, entry는 실제 체결가(effective fill), mark는
  원시(raw) mark 캔들 종가(스프레드/슬리피지를 미실현손익 평가에 또 반영하지
  않는다 - 그건 오직 "체결"에만 적용되는 비용이다).
- equity: wallet_balance + 모든 열린 포지션의 unrealized_pnl 합.
- gross_notional: 모든 열린 포지션의 |contracts*ctVal*mark| 합(long/short 상계 없음).
- planned_initial_stop_loss(예약값): 최종 확정된 contracts 기준
  price_loss_per_contract + entry_fee_per_contract + expected_stop_exit_fee_per_contract
  를 contracts배 한 값 - "목표 위험액"이 아니라 "이 계약 수량이 실제로 손절되면
  발생할 계획된 손실"이다. trailing이 유리하게 움직여도 조기 반환하지 않는다
  (전략 변경 방지를 위한 의도적 보수화 - 사용자 지시)."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable

import backtest_engine as be
import cost_accounting
import execution_units as eu
import stop_contract

SIDE_LONG = be.SIDE_LONG
SIDE_SHORT = be.SIDE_SHORT
SIDE_FLAT = be.SIDE_FLAT

REJECTION_REASONS = (
    "invalid_equity", "invalid_entry_price", "invalid_stop_price", "invalid_stop_side",
    "zero_stop_distance", "remaining_risk_room_zero", "allowed_risk_zero",
    "gross_capacity_zero", "below_min_size", "risk_cap_drift",
)


def _reject(reason: str) -> dict:
    return {
        "accepted": False, "reason": reason, "contracts": 0, "planned_risk_usdt": None,
        "gross_notional_usdt": None, "binding_constraint": None,
    }


def compute_position_size(
    *, current_equity: float, per_trade_risk_pct: float, portfolio_risk_cap_pct: float,
    current_reserved_risk_usdt: float, current_gross_notional_usdt: float,
    side: str, entry_price: float, stop_price: float, fee_rate: float,
    max_gross_leverage: float, contract_size: float, lot_step: float | None, min_contracts: float | None,
    tick_size: float | None = None, max_contracts: float | None = None,
) -> dict:
    """사용자 지시 5번의 공식을 그대로 구현한다. 반환 dict의 "accepted"가 False면
    "contracts"는 반드시 0이다(고정 fallback/강제 최소수량 없음)."""
    if current_equity is None or not math.isfinite(current_equity) or current_equity <= 0:
        return _reject("invalid_equity")
    if entry_price is None or not math.isfinite(entry_price) or entry_price <= 0:
        return _reject("invalid_entry_price")
    if stop_price is None or not math.isfinite(stop_price) or stop_price <= 0:
        return _reject("invalid_stop_price")
    if side == "long" and stop_price >= entry_price:
        return _reject("invalid_stop_side")
    if side == "short" and stop_price <= entry_price:
        return _reject("invalid_stop_side")

    stop_distance = abs(entry_price - stop_price)
    if stop_distance <= 0:
        return _reject("zero_stop_distance")

    trade_risk_budget = max(0.0, current_equity * per_trade_risk_pct / 100.0)
    portfolio_risk_cap = max(0.0, current_equity * portfolio_risk_cap_pct / 100.0)
    remaining_risk_room = max(0.0, portfolio_risk_cap - current_reserved_risk_usdt)
    if remaining_risk_room <= 0:
        return _reject("remaining_risk_room_zero")
    allowed_risk = min(trade_risk_budget, remaining_risk_room)
    if allowed_risk <= 0:
        return _reject("allowed_risk_zero")

    price_loss_per_contract = stop_distance * contract_size
    entry_fee_per_contract = entry_price * contract_size * fee_rate
    expected_stop_exit_fee_per_contract = stop_price * contract_size * fee_rate
    planned_loss_per_contract = price_loss_per_contract + entry_fee_per_contract + expected_stop_exit_fee_per_contract
    if planned_loss_per_contract <= 0:
        return _reject("zero_stop_distance")

    contracts_by_risk = allowed_risk / planned_loss_per_contract

    notional_per_contract = entry_price * contract_size
    max_gross_notional = current_equity * max_gross_leverage
    remaining_gross_notional_capacity = max(0.0, max_gross_notional - current_gross_notional_usdt)
    if remaining_gross_notional_capacity <= 0:
        return _reject("gross_capacity_zero")
    contracts_by_leverage = remaining_gross_notional_capacity / notional_per_contract

    raw_contracts = min(contracts_by_risk, contracts_by_leverage)
    binding_constraint = "risk" if contracts_by_risk <= contracts_by_leverage else "leverage"
    final_contracts = eu.round_to_step(raw_contracts, lot_step, mode="down")

    if final_contracts <= 0 or (min_contracts and final_contracts < min_contracts):
        return _reject("below_min_size")

    final_planned_risk = final_contracts * planned_loss_per_contract
    final_notional = final_contracts * notional_per_contract

    # 방어적 재확인(사용자 지시) - floor로 인해 이론상 항상 만족해야 하지만, 부동소수점
    # 경계 사례를 대비해 명시적으로 재검증한다.
    if final_planned_risk > allowed_risk + 1e-9:
        return _reject("risk_cap_drift")
    if final_notional > remaining_gross_notional_capacity + 1e-9:
        return _reject("risk_cap_drift")

    return {
        "accepted": True, "reason": None, "contracts": final_contracts,
        "planned_risk_usdt": final_planned_risk, "gross_notional_usdt": final_notional,
        "binding_constraint": binding_constraint,
        "price_loss_per_contract": price_loss_per_contract,
        "planned_loss_per_contract": planned_loss_per_contract,
    }


STOP_EXIT_CATEGORIES = (
    "open_gap_through_stop", "intrabar_stop_touch", "execution_slippage_only",
    "cost_accounting_only", "other_or_ambiguous",
)


def classify_stop_exit_overshoot(trade) -> dict | None:
    """Phase 3.3 항목10 - protective_stop 청산 1건을 재분류한다. exit_reason이
    protective_stop이 아니면 None. overshoot_usdt/pct는 사용자 지시 그대로:
    overshoot_usdt = max(0, actual_net_loss - planned_initial_stop_risk)
    overshoot_pct = overshoot_usdt / planned_initial_stop_risk

    cost_tolerance_usdt(그 거래의 실제 왕복 수수료+spread+slippage 합)보다 작은
    초과분은 "execution_slippage_only"로 분류한다 - sizing 시점의
    planned_loss_per_contract는 raw stop_price 기준이라, 실제 체결이 effective
    가격(stop보다 spread/slippage만큼 불리한 가격)으로 이뤄지는 한 이 정도의
    작은 초과는 구조적으로 항상 발생하며 버그가 아니다."""
    if trade.exit_reason != "protective_stop":
        return None
    actual_net_loss = max(0.0, -(trade.net_pnl or 0.0))
    planned_risk = trade.planned_risk_usdt
    overshoot_usdt = max(0.0, actual_net_loss - planned_risk) if planned_risk > 0 else actual_net_loss
    overshoot_pct = (overshoot_usdt / planned_risk) if planned_risk > 0 else None
    cost_tolerance_usdt = trade.entry_fee_usdt + trade.exit_fee_usdt + trade.spread_cost + trade.slippage_cost

    if trade.exit_open_gap_through_stop:
        category = "open_gap_through_stop"
    elif overshoot_usdt <= 1e-6:
        category = "intrabar_stop_touch"
    elif overshoot_usdt <= cost_tolerance_usdt + 1e-9:
        category = "execution_slippage_only"
    else:
        category = "other_or_ambiguous"

    return {
        "symbol": trade.symbol, "exit_time_ms": trade.exit_time_ms,
        "category": category, "overshoot_usdt": overshoot_usdt, "overshoot_pct": overshoot_pct,
        "planned_risk_usdt": planned_risk, "actual_net_loss_usdt": actual_net_loss,
        "cost_tolerance_usdt": cost_tolerance_usdt,
    }


def compute_drawdown_from_equity_curve(equity_curve: list[dict]) -> dict:
    """equity_curve: [{"ts":, "equity":}, ...] 오름차순. 공식(사용자 지시 8번):
    drawdown_amount = rolling_peak - equity, drawdown_pct = amount/rolling_peak.
    peak/trough는 "그 낙폭이 실제로 발생한 시점"으로 정확히 페어링한다(Phase 3.1
    감사에서 발견한 페어링 버그의 재발 방지 - 최종 peak과 그 낙폭 당시의 peak을
    혼동하지 않는다)."""
    if not equity_curve:
        return {
            "initial_equity": None, "final_equity": None, "peak_equity": None, "peak_ts": None,
            "trough_equity": None, "trough_ts": None, "max_drawdown_usdt": 0.0, "max_drawdown_pct": 0.0,
            "recovered": None, "went_non_positive": False, "max_gross_leverage_seen": None,
        }

    initial_equity = equity_curve[0]["equity"]
    rolling_peak, rolling_peak_ts = equity_curve[0]["equity"], equity_curve[0]["ts"]
    max_dd_amount, max_dd_pct = 0.0, 0.0
    dd_peak_equity, dd_peak_ts, dd_trough_equity, dd_trough_ts = rolling_peak, rolling_peak_ts, rolling_peak, rolling_peak_ts
    went_non_positive = equity_curve[0]["equity"] <= 0

    for point in equity_curve:
        equity, ts = point["equity"], point["ts"]
        if equity <= 0:
            went_non_positive = True
        if equity > rolling_peak:
            rolling_peak, rolling_peak_ts = equity, ts
        dd_amount = rolling_peak - equity
        if dd_amount > max_dd_amount:
            max_dd_amount = dd_amount
            max_dd_pct = (dd_amount / rolling_peak * 100.0) if rolling_peak > 0 else None
            dd_peak_equity, dd_peak_ts = rolling_peak, rolling_peak_ts
            dd_trough_equity, dd_trough_ts = equity, ts

    final_equity = equity_curve[-1]["equity"]
    recovered = final_equity >= dd_peak_equity if max_dd_amount > 0 else True

    return {
        "initial_equity": initial_equity, "final_equity": final_equity,
        "peak_equity": dd_peak_equity, "peak_ts": dd_peak_ts,
        "trough_equity": dd_trough_equity, "trough_ts": dd_trough_ts,
        "max_drawdown_usdt": max_dd_amount, "max_drawdown_pct": max_dd_pct,
        "recovered": recovered, "went_non_positive": went_non_positive,
    }


# ---------------------------------------------------------------------------
# 결합 포트폴리오 이벤트 루프 - Candidate B-B3 신호(변경 없음)를 그대로 쓰되,
# 사이징만 위 compute_position_size()로 바꾸고, 단일 공유 wallet/equity 원장과
# 진짜 MTM 자본곡선을 추가한다.
# ---------------------------------------------------------------------------


@dataclass
class MtmTrade:
    """gross_pnl은 effective(spread+slippage 반영) 가격 기준 execution_adjusted_pnl의
    별칭이다(Phase 3.3 - 기존 필드 의미를 바꾸지 않음). spread_cost/slippage_cost는
    진입+청산 누적 attribution(설명용)일 뿐 net_pnl에서 다시 차감되지 않는다.
    raw_market_pnl/raw_entry_price/raw_exit_price/entry_fee_usdt/exit_fee_usdt/
    funding_pnl_usdt(양수=수취)는 Phase 3.3에서 추가된 신규 필드다."""
    symbol: str
    side: str
    entry_time_ms: int
    entry_price: float
    exit_time_ms: int | None = None
    exit_price: float | None = None
    contracts: float = 0.0
    contract_size: float = 0.0
    gross_pnl: float | None = None
    fee_cost: float = 0.0
    spread_cost: float = 0.0
    slippage_cost: float = 0.0
    funding_cost: float = 0.0
    net_pnl: float | None = None
    exit_reason: str | None = None
    planned_risk_usdt: float = 0.0
    raw_entry_price: float | None = None
    raw_exit_price: float | None = None
    raw_market_pnl: float | None = None
    entry_fee_usdt: float = 0.0
    exit_fee_usdt: float = 0.0
    funding_pnl_usdt: float = 0.0
    exit_trigger_stop_price: float | None = None  # protective_stop 청산 당시 발동시킨 실제 stop 값(재분류용)
    exit_open_gap_through_stop: bool | None = None  # True면 그 bar 시가 자체가 이미 stop을 통과(진짜 gap)
    position_id: str | None = None  # Phase 3.4 - f"{symbol}#{entry_time_ms}", 동일 입력 재실행 시 항상 동일
    entry_fill_id: str | None = None  # 이 엔진은 scale-in이 없어 position_id와 항상 동일(1 fill = 1 position)


@dataclass
class PortfolioDiagnosticResult:
    trades: list = field(default_factory=list)
    rejected_entries: list = field(default_factory=list)
    equity_curve: list = field(default_factory=list)
    stale_reservations_at_end: dict = field(default_factory=dict)  # Phase 3.2 이름 - 하위호환용, active_reservations_at_end의 별칭
    active_reservations_at_end: dict = field(default_factory=dict)  # 열린 포지션과 실제로 대응하는 예약(정상)
    true_stale_reservations_at_end: dict = field(default_factory=dict)  # 대응하는 포지션/pending 없이 남은 예약(누수) - 항상 {}이어야 함
    open_positions_at_end: dict = field(default_factory=dict)
    risk_cap_drift_events: list = field(default_factory=list)
    stop_gap_overshoot_events: list = field(default_factory=list)
    # Phase 3.4 항목2 - "entries"라는 모호한 필드 하나로 묶지 않고 세분화한 카운터.
    # 이 엔진은 scale-in이 없으므로(신규진입은 항상 symbol not in positions일 때만
    # 발생) entry_signals == entry_orders_created == entry_fills(성공) + rejected_entries(실패)가
    # 구조적으로 항상 성립하고, entry_fills는 항상 positions_opened와 1:1이다.
    entry_signals_count: int = 0
    entry_orders_created_count: int = 0
    entry_fills_count: int = 0
    positions_opened_count: int = 0
    positions_discarded_or_overwritten_count: int = 0
    partial_add_fills_count: int = 0  # scale-in 없음 - 항상 0(구조적으로 발생 불가)
    partial_close_events_count: int = 0  # 실제 이벤트 루프는 fill_ratio<1을 호출하지 않음 - 항상 0
    lifecycle_audit: list = field(default_factory=list)  # position_id별 전수 대사 레코드(항목2.2)


def _mark_lookup(mark_bars: list[dict]) -> dict:
    return {b["open_time_ms"]: b for b in mark_bars}


def _unrealized_pnl(position: dict, mark_price: float) -> float:
    if position["side"] == SIDE_LONG:
        return position["contracts"] * position["contract_size"] * (mark_price - position["entry_price"])
    return position["contracts"] * position["contract_size"] * (position["entry_price"] - mark_price)


def run_portfolio_mtm_backtest(
    trade_bars_by_symbol: dict[str, list[dict]],
    mark_bars_by_symbol: dict[str, list[dict]],
    signal_fns: dict[str, Callable],
    instrument_meta_by_symbol: dict[str, dict],
    *, starting_equity: float, per_trade_risk_pct: float, portfolio_risk_cap_pct: float,
    max_gross_leverage: float, fee_rate: float, spread_bps: float, slippage_bps: float,
    ranking_fn: Callable, funding_rate_fn_by_symbol: dict[str, Callable] | None = None,
) -> PortfolioDiagnosticResult:
    """단일 공유 wallet_balance/equity/gross_notional 원장으로 4종목을 동시에
    진행하는 결합 이벤트 루프. Candidate B-B3의 signal_fns는 손대지 않는다 -
    이 함수가 새로 하는 일은 오직 (1) 실제 equity/ATR 손절 기반 사이징,
    (2) 진짜 MTM 자본곡선 기록, (3) ranking_fn 순서로 처리되는 신규진입 체결이다.

    next-bar 실행 계약은 backtest_engine.py와 동일하게 유지한다 - 신호는 bar N에서
    나오고 체결은 N+1의 시가(+스프레드/슬리피지)에서, 사이징도 그 체결가 기준으로
    계산한다(신호 bar 종가 아님)."""
    funding_rate_fn_by_symbol = funding_rate_fn_by_symbol or {}
    for symbol, bars in trade_bars_by_symbol.items():
        for bar in bars:
            if bar.get("confirm") != 1:
                raise be.UnconfirmedCandleInputError(f"[{symbol}] confirm!=1 캔들이 섞여 있음")

    mark_lookup = {s: _mark_lookup(bars) for s, bars in mark_bars_by_symbol.items()}
    all_timestamps = sorted({b["open_time_ms"] for bars in trade_bars_by_symbol.values() for b in bars})
    index_by_symbol = {
        s: {b["open_time_ms"]: i for i, b in enumerate(bars)} for s, bars in trade_bars_by_symbol.items()
    }

    result = PortfolioDiagnosticResult()
    wallet_balance = starting_equity
    positions: dict[str, dict] = {}
    pending_orders: dict[str, dict] = {}

    def _mark_at(symbol: str, ts: int) -> float:
        bar = mark_lookup[symbol].get(ts)
        if bar is None:
            raise RuntimeError(
                f"[{symbol}] ts={ts}에 mark 캔들이 없음 - as-of 대체 없이 진단 실패(사용자 지시 8번)"
            )
        return bar["close"]

    def _current_equity(exclude_symbol: str | None = None) -> float:
        total = wallet_balance
        for sym, pos in positions.items():
            if sym == exclude_symbol:
                continue
            total += _unrealized_pnl(pos, _mark_at(sym, _last_seen_ts[sym]))
        return total

    def _current_gross_notional(exclude_symbol: str | None = None) -> float:
        total = 0.0
        for sym, pos in positions.items():
            if sym == exclude_symbol:
                continue
            total += abs(pos["contracts"] * pos["contract_size"] * _mark_at(sym, _last_seen_ts[sym]))
        return total

    def _current_reserved_risk(exclude_symbol: str | None = None) -> float:
        return sum(p["planned_risk_usdt"] for s, p in positions.items() if s != exclude_symbol)

    _last_seen_ts: dict[str, int] = {}

    def _close_position(symbol: str, bar: dict, exit_price: float, exit_reason: str, fill_ratio: float = 1.0) -> None:
        """exit_price는 raw(비용 미반영) 가격이다(_apply_price_costs가 여기서
        effective 가격으로 변환한다). Phase 3.3 - execution_adjusted_pnl은 이미
        effective 가격 차이로 spread+slippage를 반영하고 있으므로, wallet/trade
        net_pnl 계산에서 spread_cost/slippage_cost를 다시 차감하지 않는다
        (cost_accounting.compute_cost_breakdown 공통 계약 재사용). funding은 이미
        정산 시점마다 wallet_balance에 개별 반영돼 있으므로(위 "펀딩 정산" 블록),
        여기 wallet 증분에는 절대 다시 포함하지 않는다 - 거래 단위 net_pnl
        필드에만 완결된 total 값(entry_fee+exit_fee+funding 전부 포함)으로 기록한다."""
        nonlocal wallet_balance
        pos = positions[symbol]
        trade = pos["trade"]
        raw_exit_price = exit_price
        effective_price, spread_cost_pu, slippage_cost_pu = be._apply_price_costs(
            raw_exit_price, pos["side"], is_entry=False, spread_bps=spread_bps, slippage_bps=slippage_bps,
        )
        closing_contracts = pos["contracts"] * fill_ratio
        closing_quantity = closing_contracts * pos["contract_size"]
        fee_cost = effective_price * closing_quantity * fee_rate
        entry_quantity_original = trade.contracts * trade.contract_size  # 부분청산과 무관하게 진입 시점 고정값
        entry_spread_cost_pu = trade.spread_cost / entry_quantity_original
        entry_slippage_cost_pu = trade.slippage_cost / entry_quantity_original
        breakdown = cost_accounting.compute_cost_breakdown(
            side=pos["side"], quantity=closing_quantity,
            raw_entry_price=trade.raw_entry_price, raw_exit_price=raw_exit_price,
            effective_entry_price=trade.entry_price, effective_exit_price=effective_price,
            entry_spread_cost_per_unit=entry_spread_cost_pu, entry_slippage_cost_per_unit=entry_slippage_cost_pu,
            exit_spread_cost_per_unit=spread_cost_pu, exit_slippage_cost_per_unit=slippage_cost_pu,
            entry_fee_usdt=trade.entry_fee_usdt, exit_fee_usdt=fee_cost,
            funding_pnl_usdt=-trade.funding_cost,
        )
        spread_cost = spread_cost_pu * closing_quantity
        slippage_cost = slippage_cost_pu * closing_quantity
        # wallet에는 이 청산 이벤트가 실제로 발생시키는 현금흐름만 반영한다 -
        # entry_fee는 진입 시점에, funding은 정산 시점마다 이미 반영됐으므로 여기서
        # 또 넣지 않는다(넣으면 이중 반영이 된다 - 기존 버그였음).
        wallet_delta_this_close = breakdown.execution_adjusted_pnl - fee_cost

        if fill_ratio >= 1.0 - 1e-9:
            trade.exit_time_ms = bar["open_time_ms"]
            trade.exit_price = effective_price
            trade.raw_exit_price = raw_exit_price
            trade.raw_market_pnl = breakdown.raw_market_pnl
            trade.gross_pnl = breakdown.execution_adjusted_pnl
            trade.fee_cost += fee_cost
            trade.exit_fee_usdt = fee_cost
            trade.spread_cost += spread_cost
            trade.slippage_cost += slippage_cost
            trade.funding_pnl_usdt = -trade.funding_cost
            trade.net_pnl = breakdown.net_pnl
            trade.exit_reason = exit_reason
            result.trades.append(trade)
            wallet_balance += wallet_delta_this_close
            del positions[symbol]
            result.lifecycle_audit.append({
                "position_id": trade.position_id, "entry_fill_id": trade.entry_fill_id, "symbol": symbol,
                "side": trade.side, "entry_timestamp": trade.entry_time_ms, "entry_price": trade.entry_price,
                "terminal_status": "closed", "close_timestamp": trade.exit_time_ms, "close_reason": exit_reason,
            })
        else:
            # 부분청산 - 남은 계약 비율만큼 예약을 비례 축소하고, 청산된 부분만 실현.
            # 포지션이 아직 열려 있으므로 trade.net_pnl은 최종 전량청산 시점까지
            # 확정하지 않는다(기존 설계 그대로 유지).
            wallet_balance += wallet_delta_this_close
            pos["contracts"] -= closing_contracts
            pos["planned_risk_usdt"] *= (1 - fill_ratio)
            trade.fee_cost += fee_cost
            trade.spread_cost += spread_cost
            trade.slippage_cost += slippage_cost
            result.partial_close_events_count += 1

    for ts in all_timestamps:
        symbols_at_ts = sorted(s for s, idx in index_by_symbol.items() if ts in idx)
        for symbol in symbols_at_ts:
            _last_seen_ts[symbol] = ts

        def _check_and_apply_protective_stop(symbol: str, bar: dict) -> None:
            """기존 backtest_engine.py의 계약과 동일 - 방금 이 bar에서 막 체결된
            포지션이라도, 그 "같은 bar" 자신의 저가/고가가 이미 손절을 건드렸으면
            같은 bar 안에서 즉시 청산한다(다음 bar까지 미루지 않는다). 신규진입
            직후에도 반드시 이 함수를 호출해야 기존 next-bar 실행 계약(같은 bar
              내 손절 발동 포함)이 보존된다."""
            if symbol not in positions:
                return
            pos = positions[symbol]
            stop_price = pos["live_stop_price"]
            side = pos["side"]
            triggered = (side == SIDE_LONG and bar["low"] <= stop_price) or (side == SIDE_SHORT and bar["high"] >= stop_price)
            if not triggered:
                return
            # Phase 3.3 항목10 - "시가 자체가 이미 stop을 통과했는가"(진짜 gap)는
            # raw_fill이 그 gap 분기(=bar["open"])로 결정됐는지로 직접 판정해야 한다.
            # 이전 버전은 "bar['open'] != raw_fill"을 썼는데, 이는 실제로는 "정상적인
            # intrabar 터치"(raw_fill=stop_price, 거의 항상 open과 다른 값)에서 거의
            # 항상 참이 되고 "진짜 gap"(raw_fill=open, 항상 같은 값)에서는 오히려
            # 거짓이 되는 정반대 버그였다(기존 613/625라는 수치가 부풀려진 원인).
            gapped = (side == SIDE_LONG and bar["open"] <= stop_price) or (side == SIDE_SHORT and bar["open"] >= stop_price)
            if side == SIDE_LONG:
                raw_fill = min(stop_price, bar["open"]) if bar["open"] <= stop_price else stop_price
            else:
                raw_fill = max(stop_price, bar["open"]) if bar["open"] >= stop_price else stop_price
            pos["trade"].exit_trigger_stop_price = stop_price
            pos["trade"].exit_open_gap_through_stop = gapped
            if gapped:
                gap_pct = abs(bar["open"] - stop_price) / stop_price * 100.0
                result.stop_gap_overshoot_events.append({"symbol": symbol, "ts": ts, "gap_pct": gap_pct})
            _close_position(symbol, bar, raw_fill, "protective_stop")

        # --- 1) 비경쟁 처리(청산류) - pending FLAT 체결, 보호적 손절(기존 포지션), funding, high-water ---
        for symbol in symbols_at_ts:
            idx = index_by_symbol[symbol][ts]
            bar = trade_bars_by_symbol[symbol][idx]

            order = pending_orders.get(symbol)
            if order is not None and order.get("kind") == "reduce" and symbol in positions:
                pending_orders.pop(symbol)
                pos = positions[symbol]
                current_base_qty = pos["contracts"] * pos["contract_size"]
                target_base_qty = order["reduce_to_base_quantity"]
                if target_base_qty is not None and current_base_qty > 0 and target_base_qty < current_base_qty:
                    fill_ratio = 1.0 - (target_base_qty / current_base_qty)
                    raw_price = bar["open"]
                    _close_position(symbol, bar, raw_price, "reduce_intent", fill_ratio=fill_ratio)
            elif order is not None and order["target_side"] == SIDE_FLAT and symbol in positions:
                pending_orders.pop(symbol)
                raw_price = bar["open"]
                _close_position(symbol, bar, raw_price, "signal")

            _check_and_apply_protective_stop(symbol, bar)

            if symbol in positions and symbol in funding_rate_fn_by_symbol:
                rate = funding_rate_fn_by_symbol[symbol](symbol, ts)
                if rate is not None:
                    pos = positions[symbol]
                    notional = pos["contracts"] * pos["contract_size"] * bar["close"]
                    funding_cost = notional * rate if pos["side"] == SIDE_LONG else -notional * rate
                    pos["trade"].funding_cost += funding_cost
                    wallet_balance -= funding_cost

            if symbol in positions:
                pos = positions[symbol]
                if pos["side"] == SIDE_LONG:
                    pos["high_water"] = max(pos["high_water"], bar["high"])
                else:
                    pos["high_water"] = min(pos["high_water"], bar["low"])

        # --- 2) 경쟁 처리(신규진입 체결) - ranking_fn 순서로 순차 처리 ---
        entry_candidates = []
        for symbol in symbols_at_ts:
            order = pending_orders.get(symbol)
            if order is not None and order["target_side"] != SIDE_FLAT and symbol not in positions:
                idx = index_by_symbol[symbol][ts]
                bar = trade_bars_by_symbol[symbol][idx]
                score = ranking_fn(symbol, bar, order["target_side"], order.get("stop_price"), positions) if ranking_fn else 0.0
                entry_candidates.append((score, symbol, order, bar))
        entry_candidates.sort(key=lambda item: (-item[0], item[1]))

        for _score, symbol, order, bar in entry_candidates:
            pending_orders.pop(symbol, None)
            side = order["target_side"]
            stop_price = order.get("stop_price")
            raw_price = bar["open"]
            effective_price, spread_cost_pu, slippage_cost_pu = be._apply_price_costs(
                raw_price, side, is_entry=True, spread_bps=spread_bps, slippage_bps=slippage_bps,
            )
            meta = instrument_meta_by_symbol[symbol]
            current_equity = _current_equity()
            current_reserved = _current_reserved_risk()
            current_gross_notional = _current_gross_notional()
            sizing = compute_position_size(
                current_equity=current_equity, per_trade_risk_pct=per_trade_risk_pct,
                portfolio_risk_cap_pct=portfolio_risk_cap_pct, current_reserved_risk_usdt=current_reserved,
                current_gross_notional_usdt=current_gross_notional, side=side, entry_price=effective_price,
                stop_price=stop_price, fee_rate=fee_rate, max_gross_leverage=max_gross_leverage,
                contract_size=meta["contract_size"], lot_step=meta.get("lot_step"), min_contracts=meta.get("min_contracts"),
            )
            if not sizing["accepted"]:
                result.rejected_entries.append({"symbol": symbol, "ts": ts, "side": side, "reason": sizing["reason"]})
                continue

            entry_fee = effective_price * sizing["contracts"] * meta["contract_size"] * fee_rate
            wallet_balance -= entry_fee
            position_id = f"{symbol}#{bar['open_time_ms']}"
            if symbol in positions:
                # 구조적으로 발생할 수 없어야 함(entry_candidates 수집 단계에서 이미
                # "symbol not in positions"로 걸러짐) - 그래도 실제로 발생하면 조용히
                # 덮어쓰지 않고 명시적으로 세고 기록한다(항목2/9 불변조건 검증용).
                result.positions_discarded_or_overwritten_count += 1
                result.lifecycle_audit.append({
                    "position_id": positions[symbol]["trade"].position_id, "symbol": symbol,
                    "terminal_status": "overwritten", "overwritten_at_ts": ts,
                })
            trade = MtmTrade(
                symbol=symbol, side=side, entry_time_ms=bar["open_time_ms"], entry_price=effective_price,
                contracts=sizing["contracts"], contract_size=meta["contract_size"],
                fee_cost=entry_fee,
                spread_cost=spread_cost_pu * sizing["contracts"] * meta["contract_size"],
                slippage_cost=slippage_cost_pu * sizing["contracts"] * meta["contract_size"],
                planned_risk_usdt=sizing["planned_risk_usdt"],
                raw_entry_price=raw_price, entry_fee_usdt=entry_fee,
                position_id=position_id, entry_fill_id=position_id,
            )
            positions[symbol] = {
                "side": side, "contracts": sizing["contracts"], "contract_size": meta["contract_size"],
                "entry_price": effective_price, "live_stop_price": stop_price, "planned_risk_usdt": sizing["planned_risk_usdt"],
                "initial_stop_price": stop_price,
                "high_water": raw_price, "trade": trade,
            }
            # Phase 3.4 항목6.2 - 포지션은 N+1 open부터 존재했으므로, 그 진입 bar
            # 자신의 유리한 극값(롱=high, 숏=low)을 즉시 high_water에 반영한다
            # (raw 가격 기준, effective 아님 - 항목6.1). Phase 1(비경쟁 처리)의
            # 일반 high_water 갱신은 "다음" ts부터만 이 심볼을 보므로, 이 bar
            # 자신의 갱신은 여기서 직접 해야 한다. high_water를 지금 갱신해도
            # 안전한 이유 - 이 bar에 대한 손절 체크(아래 _check_and_apply_
            # protective_stop)는 live_stop_price(진입 전 결정된 initial stop)만
            # 보고 high_water는 전혀 참조하지 않는다. signal_fn이 이 high_water를
            # 근거로 새 trailing을 요청하더라도, 그 요청은 이번 ts의 4단계(신호
            # 평가)에서만 live_stop_price에 반영되고, 그 갱신된 stop이 실제로
            # bar 가격과 대조되는 시점은 항상 "다음" ts부터다 - 같은 bar에 소급
            # 적용되지 않는다.
            if side == SIDE_LONG:
                positions[symbol]["high_water"] = max(raw_price, bar["high"])
            else:
                positions[symbol]["high_water"] = min(raw_price, bar["low"])
            result.entry_fills_count += 1
            result.positions_opened_count += 1

            # backtest_engine.py의 기존 계약(Phase 3.2 항목7, 보존 대상) - 방금
            # 이 bar에서 막 체결된 포지션이라도, "체결된 그 bar 자신"의 저가/고가가
            # 이미 손절을 건드렸다면 다음 ts까지 미루지 않고 같은 bar 안에서 즉시
            # 청산한다. Phase 1(비경쟁 처리)은 이 시점엔 이미 지나갔으므로 여기서
            # 직접 호출해야 한다.
            if stop_price is not None:
                _check_and_apply_protective_stop(symbol, bar)

        # --- 3) MTM 자본곡선 기록(이 ts의 모든 체결/청산/funding 반영 후) ---
        equity_now = _current_equity()
        gross_notional_now = _current_gross_notional()
        active_reserved_risk_now = _current_reserved_risk()
        portfolio_risk_cap_usdt_now = max(0.0, equity_now * portfolio_risk_cap_pct / 100.0)
        result.equity_curve.append({
            "ts": ts, "equity": equity_now, "wallet_balance": wallet_balance, "gross_notional": gross_notional_now,
            "gross_leverage": (gross_notional_now / equity_now) if equity_now > 0 else None,
            "active_reserved_risk_usdt": active_reserved_risk_now,
            "portfolio_risk_cap_usdt": portfolio_risk_cap_usdt_now,
            "reserved_risk_pct_of_equity": (active_reserved_risk_now / equity_now * 100.0) if equity_now > 0 else None,
        })

        # --- 4) 신호 평가 - 다음 ts 체결용 pending 큐잉(경쟁 없음, 그냥 의도 기록) ---
        for symbol in symbols_at_ts:
            idx = index_by_symbol[symbol][ts]
            bars = trade_bars_by_symbol[symbol]
            window = be.BarWindow(bars, idx)
            position_state = None
            if symbol in positions:
                pos = positions[symbol]
                position_state = {
                    "side": pos["side"], "entry_price": pos["entry_price"],
                    "stop_price": pos["live_stop_price"], "high_water": pos["high_water"],
                    "contracts": pos["contracts"],
                    "base_quantity": pos["contracts"] * pos["contract_size"],
                    "contract_size": pos["contract_size"],
                    "position_id": pos["trade"].position_id,
                    "entry_time_ms": pos["trade"].entry_time_ms,
                    "raw_entry_price": pos["trade"].raw_entry_price,
                    "initial_stop_price": pos["initial_stop_price"],
                    "entry_fee_usdt": pos["trade"].entry_fee_usdt,
                    "fee_rate": fee_rate, "spread_bps": spread_bps, "slippage_bps": slippage_bps,
                }
            raw_signal = signal_fns[symbol](symbol, window, position_state)
            desired, requested_stop = (raw_signal, None) if isinstance(raw_signal, str) else (raw_signal["target_side"], raw_signal.get("stop_price"))
            current_side = positions[symbol]["side"] if symbol in positions else SIDE_FLAT

            if desired == current_side:
                if desired != SIDE_FLAT and requested_stop is not None:
                    pos = positions[symbol]
                    if not stop_contract.stop_would_loosen(pos["side"], pos["live_stop_price"], requested_stop):
                        pos["live_stop_price"] = requested_stop
                        # 사용자 지시 6번 - trailing이 유리해져도 예약(planned_risk_usdt)은
                        # 조기 반환하지 않는다(보수적으로 유지) - live_stop_price만 갱신한다.
                # Phase 3.8 Part 6 - typed-intent 부분 축소 hook. backtest_engine.py와
                # 동일한 선택 필드(raw_signal["reduce_to_base_quantity"])를 재사용한다 -
                # 기존 문자열/target_side+stop_price만 쓰는 호출부는 완전히 그대로다.
                if desired != SIDE_FLAT and isinstance(raw_signal, dict) and raw_signal.get("reduce_to_base_quantity") is not None:
                    pending_orders[symbol] = {
                        "kind": "reduce", "target_side": desired,
                        "reduce_to_base_quantity": raw_signal["reduce_to_base_quantity"],
                    }
                continue

            if current_side == SIDE_FLAT and desired != SIDE_FLAT:
                # 신규진입 신호 - 이 엔진은 별도 주문중개 계층이 없어 신호==주문
                # 생성이 항상 1:1이다(entry_signals와 entry_orders_created가
                # 구조적으로 항상 같은 값이 되는 이유).
                result.entry_signals_count += 1
                result.entry_orders_created_count += 1
            new_order = {"target_side": desired, "stop_price": requested_stop}
            pending_orders[symbol] = new_order

    for symbol, pos in positions.items():
        final_mark = _mark_at(symbol, _last_seen_ts[symbol])
        # raw_unrealized_pnl: raw_entry_price 기준(비용 미반영) - net_unrealized_pnl:
        # effective_entry_price(entry_price) 기준(entry spread/slippage 이미 반영,
        # 이 모듈의 기존 unrealized_pnl 정의 - Phase 3.5 항목1.3 완전표용으로 이름만 명확화).
        raw_pos_for_upl = dict(pos, entry_price=pos["trade"].raw_entry_price)
        result.open_positions_at_end[symbol] = {
            "side": pos["side"], "contracts": pos["contracts"], "planned_risk_usdt": pos["planned_risk_usdt"],
            "contract_size": pos["contract_size"], "entry_price": pos["entry_price"],
            "raw_entry_price": pos["trade"].raw_entry_price, "entry_fee_usdt": pos["trade"].entry_fee_usdt,
            "final_mark_price": final_mark,
            "raw_unrealized_pnl": _unrealized_pnl(raw_pos_for_upl, final_mark),
            "net_unrealized_pnl": _unrealized_pnl(pos, final_mark),
            "unrealized_pnl_at_end": _unrealized_pnl(pos, final_mark),  # 하위호환 별칭(=net_unrealized_pnl)
            # 아직 청산 전이라 trade.funding_pnl_usdt는 미확정(0.0)이다 - 실시간
            # 누적값인 funding_cost(양수=지불)를 부호 변환해서 노출한다(완전대사용).
            "accumulated_funding_pnl_usdt": -pos["trade"].funding_cost,
            "position_id": pos["trade"].position_id,
            "reservation_id": pos["trade"].position_id,  # 이 엔진은 별도 예약장부가 없어 position_id와 동일(Phase 3.3/3.4에서 확정)
            "current_protective_stop": pos["live_stop_price"],
        }
        result.lifecycle_audit.append({
            "position_id": pos["trade"].position_id, "entry_fill_id": pos["trade"].entry_fill_id, "symbol": symbol,
            "side": pos["trade"].side, "entry_timestamp": pos["trade"].entry_time_ms, "entry_price": pos["trade"].entry_price,
            "terminal_status": "active_at_final_end", "close_timestamp": None, "close_reason": None,
        })
        # 이 엔진은 별도의 분리된 예약 장부가 없다 - positions 딕셔너리 자체가 유일한
        # 예약 출처이므로, 열린 포지션과 무관하게 남는 예약(누수)은 구조적으로
        # 발생할 수 없다. 그래도 사용자 지시대로 두 이름을 명시적으로 분리해서
        # 기록한다 - true_stale은 항상 {}여야 하며, 그렇지 않다면 그 자체가 새로운
        # 예약 누수 버그의 증거다.
        result.stale_reservations_at_end[symbol] = pos["planned_risk_usdt"]
        result.active_reservations_at_end[symbol] = pos["planned_risk_usdt"]

    return result
