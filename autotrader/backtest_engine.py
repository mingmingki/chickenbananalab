"""Phase 2 - 이벤트 기반 백테스터 "실행 계약"(execution contract) 검증용 엔진.

중요 - 이 모듈은 전략이 아니다. Candidate A/B 같은 실제 매매 전략이나 파라미터
최적화는 Phase 3 대상이고, 여기서는 절대 다루지 않는다. 이 파일이 제공하는
`dummy_alternating_signal()`은 가격/지표를 전혀 보지 않고 인덱스만으로 기계적으로
long/flat을 반복하는 더미 신호일 뿐이며, 오직 아래 실행 계약이 올바르게 동작하는지
검증하기 위한 용도로만 쓴다:

- confirm=1(확정) 캔들만 신호 입력으로 쓸 수 있다 - 미확정 캔들이 섞여 있으면 즉시
  실패한다.
- bar t에서 만든 신호는 bar t+1 이전에는 절대 체결되지 않는다(같은 봉 종가 체결 금지).
- 롱/숏 모두 지원, 계약 단위(execution_units) 기반, 수수료/스프레드/슬리피지/펀딩을
  각각 분리해서 계산한다.
- 부분체결/주문거부를 모델링할 수 있다(주입 가능한 fill_model_fn).
- 여러 심볼이 같은 타임스탬프를 공유하면 항상 같은 순서(심볼명 오름차순)로 처리한다
  (결정적 이벤트 순서).
- 포트폴리오 리스크 예약(심볼 전체 총 리스크 상한)을 지원한다 - 실거래용
  portfolio_risk.py(디스크 기반, 프로세스 간 원자성 목적)를 그대로 재사용하지 않고
  이 엔진 전용의 메모리 내 리스크 장부를 쓴다. 백테스트는 단일 스레드로 시간 순서대로
  순차 처리되므로 프로세스 간 원자성이 애초에 필요 없고, 매 bar마다 디스크 I/O를 거치면
  대량의 과거 데이터를 도는 백테스터 성능이 크게 떨어지기 때문이다(실거래 도구를
  시뮬레이션 도구에 억지로 재사용하지 않는다).
- 미래 데이터 접근은 코드 수준에서 원천 차단한다(BarWindow가 현재 시점 이후 인덱스
  접근 시 즉시 예외).
- 동일 입력+설정이면 항상 byte-identical 결과(순수 함수, wall-clock/random 의존 없음)."""
from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from typing import Callable

import cost_accounting
import execution_units
import stop_contract

SIDE_LONG = "long"
SIDE_SHORT = "short"
SIDE_FLAT = "flat"


class UnconfirmedCandleInputError(Exception):
    """confirm=0(미확정) 캔들이 신호 입력으로 섞여 들어온 경우 - 백테스트 시작 전에
    즉시 막는다."""


class FutureDataAccessError(Exception):
    """신호 함수(혹은 그 무엇이든)가 아직 도래하지 않은 미래 bar에 접근하려 한 경우."""


class RiskLimitExceededInBacktest(Exception):
    """이 예외 자체는 엔진 내부에서 잡아 "주문 차단" 이벤트로 기록하는 용도로만 쓰고,
    호출부까지 전파되지는 않는다(테스트에서 필요하면 직접 이 이름으로 import해서 씀)."""


class BarWindow:
    """symbol 하나에 대해 "현재 시점까지만" 보이는 읽기전용 뷰. 미래 인덱스에
    접근하면 즉시 FutureDataAccessError를 던져 look-ahead를 원천 차단한다."""

    __slots__ = ("_bars", "_visible_up_to")

    def __init__(self, bars: list[dict], visible_up_to_index: int):
        self._bars = bars
        self._visible_up_to = visible_up_to_index

    def __len__(self) -> int:
        return self._visible_up_to + 1

    def _resolve(self, idx: int) -> int:
        resolved = idx if idx >= 0 else len(self) + idx
        if resolved < 0 or resolved > self._visible_up_to:
            raise FutureDataAccessError(
                f"현재 시점(index={self._visible_up_to}) 이후 데이터(index={idx})에 접근 시도"
            )
        return resolved

    def __getitem__(self, idx):
        if isinstance(idx, slice):
            start, stop, step = idx.indices(len(self))
            return [self._bars[self._resolve(i)] for i in range(start, stop, step)]
        return self._bars[self._resolve(idx)]

    def latest(self) -> dict:
        return self._bars[self._visible_up_to]


def dummy_alternating_signal(symbol: str, window: BarWindow, position_state: dict | None = None, *, flip_every: int = 10) -> str:
    """실행 계약 검증 전용 더미 신호 - 가격/지표를 전혀 참조하지 않고 인덱스만으로
    long/flat을 기계적으로 반복한다. 절대 실전 전략이 아니다(Phase 3에서 실제
    전략으로 교체됨)."""
    idx = len(window) - 1
    return SIDE_LONG if (idx // flip_every) % 2 == 0 else SIDE_FLAT


@dataclass
class EngineConfig:
    fee_rate: float = 0.0005
    spread_bps: float = 0.0
    slippage_bps: float = 0.0
    equity_usdt: float = 10_000.0
    risk_per_trade_usdt: float = 100.0  # 주문 크기 산정용 고정 금액(구조 검증용 - 최적화 대상 아님)
    stop_distance_pct: float = 1.0  # 포트폴리오 리스크 계산용 가상 손절폭(구조 검증용)
    max_total_risk_usdt: float | None = None
    max_per_trade_risk_usdt: float | None = None
    funding_rate_fn: Callable[[str, int], float | None] | None = None
    # ranking_fn(symbol, bar, target_side, requested_stop_price, open_positions) -> float
    # 같은 타임스탬프에 여러 심볼이 동시에 신규 진입을 원할 때, 리스크 예산을
    # 누가 먼저 가져갈지 정하는 우선순위 점수(높을수록 우선). None이면(기본값)
    # 기존과 동일하게 심볼명 오름차순으로만 결정된다(Phase 2 결정적 순서 계약과
    # 하위호환) - Phase 3 포트폴리오 배분에서만 실제 랭킹 함수를 주입한다.
    ranking_fn: Callable[[str, dict, str, float | None, dict], float] | None = None
    sizing_config: dict | None = None
    max_positions: int | None = None
    progress_fn: Callable[[int, int], None] | None = None


@dataclass
class Trade:
    """gross_pnl은 effective(spread+slippage 반영) 가격 기준 execution_adjusted_pnl의
    별칭이다(Phase 3.3 - 기존 필드 의미를 바꾸지 않음). spread_cost/slippage_cost는
    진입+청산 누적 attribution(설명용)일 뿐 net_pnl에서 다시 차감되지 않는다.
    fee_cost/funding_cost도 기존 의미(누적, funding_cost는 양수=지불) 그대로 유지한다.

    raw_market_pnl/raw_entry_price/raw_exit_price/entry_fee_usdt/exit_fee_usdt/
    funding_pnl_usdt(양수=수취)는 Phase 3.3에서 추가된, 명확히 분리된 신규 필드다."""
    symbol: str
    side: str
    entry_time_ms: int
    entry_price: float
    exit_time_ms: int | None
    exit_price: float | None
    contracts: float
    base_quantity: float
    gross_pnl: float | None
    fee_cost: float
    spread_cost: float
    slippage_cost: float
    funding_cost: float
    net_pnl: float | None
    exit_reason: str | None = None  # "signal" | "protective_stop" | None(아직 열려있음)
    raw_entry_price: float | None = None
    raw_exit_price: float | None = None
    raw_market_pnl: float | None = None
    entry_fee_usdt: float = 0.0
    exit_fee_usdt: float = 0.0
    funding_pnl_usdt: float = 0.0
    position_id: str | None = None


@dataclass
class BacktestResult:
    trades: list[Trade] = field(default_factory=list)
    blocked_orders: list[dict] = field(default_factory=list)
    rejected_orders: list[dict] = field(default_factory=list)
    funding_missing_events: list[dict] = field(default_factory=list)
    reduce_events: list["ReduceEvent"] = field(default_factory=list)
    open_positions_at_end: dict = field(default_factory=dict)
    equity_curve: list[dict] = field(default_factory=list)

    def closed_round_trips(self) -> list[Trade]:
        """Each closed epoch includes its allocated partial legs exactly once."""
        grouped = {}
        for event in self.reduce_events:
            grouped.setdefault(event.position_id, []).append(event)
        output = []
        for final in self.trades:
            trade = replace(final)
            for leg in grouped.get(trade.position_id, []):
                for name in ("gross_pnl", "fee_cost", "spread_cost", "slippage_cost",
                             "funding_cost", "raw_market_pnl", "entry_fee_usdt", "exit_fee_usdt"):
                    setattr(trade, name, (getattr(trade, name) or 0.0) + getattr(leg, name))
                trade.net_pnl += leg.realized_pnl
                trade.base_quantity += leg.reduced_base_quantity
                trade.contracts += leg.reduced_contracts
            trade.funding_pnl_usdt = -trade.funding_cost
            output.append(trade)
        return output

    def fingerprint(self) -> str:
        import hashlib
        payload = {
            "trades": [_trade_to_dict(t) for t in self.trades],
            "blocked_orders": self.blocked_orders,
            "rejected_orders": self.rejected_orders,
            "funding_missing_events": self.funding_missing_events,
            "reduce_events": [vars(r) for r in self.reduce_events],
        }
        canonical = json.dumps(payload, sort_keys=True, default=str)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _trade_to_dict(t: Trade) -> dict:
    return {
        "symbol": t.symbol, "side": t.side, "entry_time_ms": t.entry_time_ms, "entry_price": t.entry_price,
        "exit_time_ms": t.exit_time_ms, "exit_price": t.exit_price, "contracts": t.contracts,
        "base_quantity": t.base_quantity, "gross_pnl": t.gross_pnl, "fee_cost": t.fee_cost,
        "spread_cost": t.spread_cost, "slippage_cost": t.slippage_cost, "funding_cost": t.funding_cost,
        "net_pnl": t.net_pnl, "exit_reason": t.exit_reason,
    }


# Phase 3.8 - never-loosen 판정은 stop_contract.py(공용, backtest_engine/
# portfolio_mtm_engine/candidate_c_live_execution_adapter 세 엔진이 전부
# 같은 함수 객체를 호출한다)로 이동했다. 이 모듈 자체의 private 복사본은
# 삭제했다 - 세 엔진이 서로 다른 사본을 갖는 것을 구조적으로 방지하기
# 위함(과거에는 다른 모듈이 backtest_engine._stop_would_loosen을 직접 import
# 해서 재사용했었다). CORE의 protective_exit.py는 독립된, 더 오래되고
# 실거래에 이미 쓰이는 별도 구현이라 손대지 않는다(범위 밖).


@dataclass
class ReduceEvent:
    """Phase 3.7 - typed-intent hook으로 추가된 부분 축소 이벤트. Trade와 별개로
    기록한다(포지션이 여러 번 부분 축소될 수 있어 1:1이 아님)."""
    symbol: str
    ts: int
    reduced_base_quantity: float
    residual_base_quantity: float
    fill_price: float
    realized_pnl: float
    position_id: str | None = None
    reduced_contracts: float = 0.0
    gross_pnl: float = 0.0
    raw_market_pnl: float = 0.0
    fee_cost: float = 0.0
    spread_cost: float = 0.0
    slippage_cost: float = 0.0
    funding_cost: float = 0.0
    entry_fee_usdt: float = 0.0
    exit_fee_usdt: float = 0.0


class _InMemoryRiskLedger:
    """백테스터 전용 - 심볼 전체 동시 리스크 총합을 메모리 내에서 추적한다. 단일
    스레드 순차 이벤트 루프 안에서만 쓰이므로 별도 잠금이 필요 없다(실거래용
    portfolio_risk.py와 달리 프로세스 간 원자성 문제가 애초에 존재하지 않음)."""

    def __init__(self):
        self._reserved: dict[str, float] = {}

    def total_reserved(self) -> float:
        return sum(self._reserved.values())

    def try_reserve(self, symbol: str, risk_usdt: float, *, max_per_trade: float | None, max_total: float | None) -> tuple[bool, str | None]:
        if max_per_trade is not None and risk_usdt > max_per_trade:
            return False, "per_trade_cap_exceeded"
        if max_total is not None and self.total_reserved() + risk_usdt > max_total:
            return False, "total_cap_exceeded"
        self._reserved[symbol] = risk_usdt
        return True, None

    def release(self, symbol: str) -> None:
        self._reserved.pop(symbol, None)

    def scale_down(self, symbol: str, factor: float) -> None:
        """부분 축소 - 상한 재검사 없이 예약을 비례 축소한다(축소는 절대
        기존 한도를 새로 위반할 수 없으므로 try_reserve의 cap 검사가 필요
        없음)."""
        if symbol in self._reserved:
            self._reserved[symbol] *= factor


def _default_fill_model(order: dict, bar: dict) -> dict:
    return {"fill_ratio": 1.0, "rejected": False}


def _apply_price_costs(raw_price: float, side: str, is_entry: bool, spread_bps: float, slippage_bps: float) -> tuple[float, float, float]:
    """매수 방향 주문은 불리한 쪽(더 비싸게), 매도 방향은 불리한 쪽(더 싸게) 체결된다고
    가정한다. 반환: (effective_price, spread_cost_per_unit, slippage_cost_per_unit)."""
    buying = (side == SIDE_LONG and is_entry) or (side == SIDE_SHORT and not is_entry)
    direction = 1 if buying else -1
    spread_cost_per_unit = raw_price * (spread_bps / 10000.0)
    slippage_cost_per_unit = raw_price * (slippage_bps / 10000.0)
    effective_price = raw_price + direction * (spread_cost_per_unit + slippage_cost_per_unit)
    return effective_price, spread_cost_per_unit, slippage_cost_per_unit


def run_backtest(
    bars_by_symbol: dict[str, list[dict]],
    signal_fn: Callable[[str, BarWindow, dict | None], str | dict],
    instrument_meta_by_symbol: dict[str, dict],
    cfg: EngineConfig,
    fill_model_fn: Callable[[dict, dict], dict] | None = None,
) -> BacktestResult:
    """bars_by_symbol의 각 리스트는 이미 open_time_ms 오름차순으로 정렬된, confirm=1만
    포함된 캔들이어야 한다(아니면 UnconfirmedCandleInputError).

    signal_fn(symbol, window, position_state) -> "long"|"short"|"flat"(기존 계약,
    하위호환) 또는 {"target_side": ..., "stop_price": float|None}(Phase 2.1/3 확장 -
    진입 시 초기 손절가, 보유 중 트레일링 갱신을 함께 표현할 수 있다).

    position_state: 현재 심볼에 열린 포지션이 없으면 None, 있으면
    {"side":, "entry_price":, "stop_price":, "high_water":}(읽기 전용 스냅샷).

    보호적 손절(stop_price)은 신호와 별개로, 매 bar마다 그 bar의 저가/고가가
    stop_price를 건드렸는지 "먼저" 확인해서(신호 평가보다 먼저) 즉시 그 bar
    범위 내에서 체결한다 - 이는 "신호는 다음 bar 시가로만 체결"이라는 계약
    위반이 아니다. 손절은 신호가 아니라 이미 걸려 있던 상시 주문(실거래의
    거래소측 attached SL과 동일한 성격)이 가격 조건을 만족해 스스로 발동한
    것이기 때문이다. 손절은 절대 더 불리한 방향으로 넓어지지 않는다(Phase 1
    protective_exit.py와 동일한 불변식)."""
    fill_model_fn = fill_model_fn or _default_fill_model

    for symbol, bars in bars_by_symbol.items():
        for bar in bars:
            if bar.get("confirm") != 1:
                raise UnconfirmedCandleInputError(
                    f"[{symbol}] confirm!=1인 캔들이 신호 입력에 섞여 있음(open_time_ms={bar.get('open_time_ms')})"
                )

    # 전체 타임라인(모든 심볼의 open_time_ms 합집합, 오름차순).
    all_timestamps = sorted({bar["open_time_ms"] for bars in bars_by_symbol.values() for bar in bars})
    index_by_symbol: dict[str, dict[int, int]] = {
        symbol: {bar["open_time_ms"]: i for i, bar in enumerate(bars)}
        for symbol, bars in bars_by_symbol.items()
    }

    result = BacktestResult()
    risk_ledger = _InMemoryRiskLedger()
    positions: dict[str, dict] = {}  # symbol -> {"side":, "trade": Trade, "risk_reserved": bool}
    pending_orders: dict[str, dict] = {}  # symbol -> order queued to execute at the NEXT bar for that symbol

    last_prices = {}
    for timeline_index, ts in enumerate(all_timestamps):
        # 결정적 순서 - 같은 타임스탬프를 공유하는 심볼은 항상 이름 오름차순으로 처리
        # (신규 진입 리스크 배분 자체는 아래에서 ranking_fn으로 별도 재정렬한다 -
        # 이 정렬은 그 외 모든 처리(체결/손절확인/펀딩/신호평가)의 순서일 뿐이다).
        symbols_at_ts = sorted(s for s, idx_map in index_by_symbol.items() if ts in idx_map)
        entry_candidates_this_ts: list[tuple[float, str, dict]] = []
        for symbol in symbols_at_ts:
            bars = bars_by_symbol[symbol]
            idx = index_by_symbol[symbol][ts]
            bar = bars[idx]
            last_prices[symbol] = bar["close"]
            meta = instrument_meta_by_symbol[symbol]

            # 1) 이 심볼에 지난 bar에서 큐잉된 주문이 있으면, "이번 bar의 시가"로 지금
            #    체결한다(신호가 나온 bar의 종가로 체결하는 것은 계약상 금지).
            order = pending_orders.pop(symbol, None)
            if order is not None:
                _execute_order(symbol, order, bar, meta, cfg, fill_model_fn, positions, risk_ledger, result)

            # 1.5) 보호적 손절 확인 - 신호가 아니라 이미 걸려 있던 상시 주문이 이번
            #      bar의 가격 범위 안에서 스스로 발동했는지 본다(같은 bar 내 체결 -
            #      "신호의 같은 봉 종가 체결 금지"와는 무관한 별개 메커니즘).
            if symbol in positions and positions[symbol].get("stop_price") is not None:
                _check_protective_stop(symbol, bar, cfg, positions, result, risk_ledger)

            # 2) 펀딩 정산 시각을 지났으면 반영한다(미제공은 0이 아니라 missing으로 기록).
            if cfg.funding_rate_fn is not None and symbol in positions:
                rate = cfg.funding_rate_fn(symbol, ts)
                pos = positions[symbol]
                if rate is None:
                    result.funding_missing_events.append({"symbol": symbol, "ts": ts})
                else:
                    notional = pos["trade"].base_quantity * bar["close"]
                    funding_cost = notional * rate if pos["side"] == SIDE_LONG else -notional * rate
                    pos["trade"].funding_cost += funding_cost

            # 2.5) high_water는 신호와 무관하게 매 bar 엔진이 직접 갱신한다(전략은
            #      그 값을 참고만 하고, 조작할 수 없다 - Phase 1 protective_exit.py와
            #      동일하게 "관측"과 "결정"을 분리한다).
            if symbol in positions:
                pos = positions[symbol]
                if pos["side"] == SIDE_LONG:
                    pos["high_water"] = max(pos["high_water"], bar["high"])
                else:
                    pos["high_water"] = min(pos["high_water"], bar["low"])

            # 3) 이 시점까지만 보이는 window로 신호를 만든다(미래 접근 시 즉시 예외).
            window = BarWindow(bars, idx)
            position_state = None
            if symbol in positions:
                pos = positions[symbol]
                position_state = {
                    "side": pos["side"], "entry_price": pos["trade"].entry_price,
                    "stop_price": pos.get("stop_price"), "high_water": pos["high_water"],
                    "contracts": pos["trade"].contracts,
                    "base_quantity": pos["trade"].base_quantity,
                    "contract_size": meta.get("contract_size", 1.0),
                    "position_id": pos["trade"].position_id,
                    "entry_time_ms": pos["trade"].entry_time_ms,
                    "raw_entry_price": pos["trade"].raw_entry_price,
                    "initial_stop_price": pos.get("initial_stop_price"),
                    "entry_fee_usdt": pos["trade"].entry_fee_usdt,
                    "fee_rate": cfg.fee_rate, "spread_bps": cfg.spread_bps,
                    "slippage_bps": cfg.slippage_bps,
                }
            raw_signal = signal_fn(symbol, window, position_state)
            if isinstance(raw_signal, str):
                desired, requested_stop, reduce_to_base_quantity = raw_signal, None, None
            else:
                desired, requested_stop = raw_signal["target_side"], raw_signal.get("stop_price")
                reduce_to_base_quantity = raw_signal.get("reduce_to_base_quantity")
            current_side = positions[symbol]["side"] if symbol in positions else SIDE_FLAT

            if desired == current_side:
                # 방향 변경 없음 - 보유 중이면 트레일링 갱신 후보만 반영한다(절대 완화 방향 금지).
                if desired != SIDE_FLAT and requested_stop is not None:
                    pos = positions[symbol]
                    if not stop_contract.stop_would_loosen(pos["side"], pos.get("stop_price"), requested_stop):
                        pos["stop_price"] = requested_stop
                if desired != SIDE_FLAT and reduce_to_base_quantity is not None:
                    pending_orders[symbol] = {
                        "kind": "reduce", "reduce_to_base_quantity": reduce_to_base_quantity,
                        "signal_time_ms": ts, "signal_index": idx,
                    }
                continue

            new_order = {"target_side": desired, "signal_time_ms": ts, "signal_index": idx, "stop_price": requested_stop}
            if current_side != SIDE_FLAT and desired == SIDE_FLAT:
                pending_orders[symbol] = new_order  # 청산만 - 리스크 예약 불필요
            elif current_side == SIDE_FLAT and desired != SIDE_FLAT:
                # 리스크 예약은 즉시 시도하지 않는다 - 같은 타임스탬프의 다른 심볼도
                # 전부 평가한 뒤, ranking_fn 점수 순으로 한 번에 처리한다(알파벳순
                # 선점 방지).
                score = cfg.ranking_fn(symbol, bar, desired, requested_stop, positions) if cfg.ranking_fn else 0.0
                entry_candidates_this_ts.append((score, symbol, new_order))
            else:
                # 반전(long<->short) - 먼저 청산 주문만 큐잉하고, flat 확인 후 재진입은
                # 다음 신호 사이클에 맡긴다(실거래 exit_coordinator와 동일한 원칙 -
                # 청산 확정 전 반대 주문을 먼저 내지 않는다).
                pending_orders[symbol] = {"target_side": SIDE_FLAT, "signal_time_ms": ts, "signal_index": idx, "stop_price": None}

        # 신규 진입 후보들 - 점수 내림차순(동점이면 심볼명 오름차순, 안정적
        # tie-breaker)으로 정렬해서 순서대로 리스크 예산을 배분한다. ranking_fn이
        # 없으면 전부 0점이라 사실상 기존과 동일한 심볼명 오름차순이 된다.
        entry_candidates_this_ts.sort(key=lambda item: (-item[0], item[1]))
        for _score, symbol, new_order in entry_candidates_this_ts:
            occupied = set(positions) | {
                s for s, order in pending_orders.items()
                if order.get("target_side") in (SIDE_LONG, SIDE_SHORT)
            }
            if cfg.max_positions is not None and len(occupied) >= cfg.max_positions:
                result.blocked_orders.append({"symbol": symbol, "ts": ts, "reason": "max_positions"})
                continue
            ok, reason = risk_ledger.try_reserve(
                symbol, cfg.risk_per_trade_usdt, max_per_trade=cfg.max_per_trade_risk_usdt, max_total=cfg.max_total_risk_usdt,
            )
            if not ok:
                result.blocked_orders.append({"symbol": symbol, "ts": ts, "reason": reason, "target_side": new_order["target_side"]})
            else:
                pending_orders[symbol] = new_order

        realized = sum(t.net_pnl or 0.0 for t in result.trades) + sum(e.realized_pnl for e in result.reduce_events)
        unrealized = sum(
            (last_prices[s] - p["trade"].entry_price) * p["trade"].base_quantity
            * (1 if p["side"] == SIDE_LONG else -1)
            - p["trade"].fee_cost - p["trade"].funding_cost
            for s, p in positions.items()
        )
        result.equity_curve.append({"ts": ts, "equity": cfg.equity_usdt + realized + unrealized,
                                    "open_positions": len(positions)})
        if cfg.progress_fn and (timeline_index % 500 == 0 or timeline_index == len(all_timestamps) - 1):
            cfg.progress_fn(timeline_index + 1, len(all_timestamps))

    result.open_positions_at_end = {s: replace(p["trade"]) for s, p in positions.items()}
    return result


def _check_protective_stop(
    symbol: str, bar: dict, cfg: EngineConfig, positions: dict, result: BacktestResult,
    risk_ledger: "_InMemoryRiskLedger",
) -> None:
    pos = positions[symbol]
    stop_price = pos["stop_price"]
    side = pos["side"]
    triggered = (side == SIDE_LONG and bar["low"] <= stop_price) or (side == SIDE_SHORT and bar["high"] >= stop_price)
    if not triggered:
        return
    # 갭으로 시가부터 이미 stop_price보다 불리하게 열렸으면 시가로, 아니면 stop_price로 체결.
    if side == SIDE_LONG:
        raw_fill_price = min(stop_price, bar["open"]) if bar["open"] <= stop_price else stop_price
    else:
        raw_fill_price = max(stop_price, bar["open"]) if bar["open"] >= stop_price else stop_price

    trade = pos["trade"]
    effective_price, spread_cost_pu, slippage_cost_pu = _apply_price_costs(
        raw_fill_price, side, is_entry=False, spread_bps=cfg.spread_bps, slippage_bps=cfg.slippage_bps,
    )
    fee_cost = effective_price * trade.base_quantity * cfg.fee_rate
    entry_spread_cost_pu = trade.spread_cost / trade.base_quantity  # 이 시점까지는 진입 다리 값만 누적돼 있음
    entry_slippage_cost_pu = trade.slippage_cost / trade.base_quantity
    breakdown = cost_accounting.compute_cost_breakdown(
        side=side, quantity=trade.base_quantity,
        raw_entry_price=trade.raw_entry_price, raw_exit_price=raw_fill_price,
        effective_entry_price=trade.entry_price, effective_exit_price=effective_price,
        entry_spread_cost_per_unit=entry_spread_cost_pu, entry_slippage_cost_per_unit=entry_slippage_cost_pu,
        exit_spread_cost_per_unit=spread_cost_pu, exit_slippage_cost_per_unit=slippage_cost_pu,
        entry_fee_usdt=trade.entry_fee_usdt, exit_fee_usdt=fee_cost,
        funding_pnl_usdt=-trade.funding_cost,
    )
    trade.exit_time_ms = bar["open_time_ms"]
    trade.exit_price = effective_price
    trade.raw_exit_price = raw_fill_price
    trade.raw_market_pnl = breakdown.raw_market_pnl
    trade.gross_pnl = breakdown.execution_adjusted_pnl
    trade.fee_cost += fee_cost
    trade.exit_fee_usdt = fee_cost
    trade.spread_cost += spread_cost_pu * trade.base_quantity
    trade.slippage_cost += slippage_cost_pu * trade.base_quantity
    trade.funding_pnl_usdt = -trade.funding_cost
    trade.net_pnl = breakdown.net_pnl
    trade.exit_reason = "protective_stop"
    result.trades.append(trade)
    risk_ledger.release(symbol)
    del positions[symbol]


def _execute_reduce_order(
    symbol: str, order: dict, bar: dict, meta: dict, cfg: EngineConfig,
    positions: dict, risk_ledger: _InMemoryRiskLedger, result: BacktestResult,
) -> None:
    """Phase 3.7 - typed-intent hook. 기존 (sym, window, position_state) ->
    "long"|"short"|"flat" 계약은 부분 축소를 표현할 방법이 없어서(포지션
    수량이 진입 시 고정) 억지로 flat/재진입에 끼워맞추지 않고, raw_signal
    dict에 새 선택 필드 "reduce_to_base_quantity"를 추가해 별도 typed 경로로
    처리한다. 기존 신호(문자열 또는 target_side/stop_price dict)만 쓰는
    모든 기존 호출부(Candidate A/B 포함)는 이 필드를 절대 채우지 않으므로
    동작이 완전히 그대로다."""
    current = positions.get(symbol)
    if current is None:
        return
    trade = current["trade"]
    target_qty = order["reduce_to_base_quantity"]
    if target_qty is None or target_qty >= trade.base_quantity:
        return  # 축소할 게 없음(이미 목표 이하) - no-op

    # 2026-09-13 버그 수정(실제 DOGE/SOL 과거 데이터로 백테스트 중 발견) -
    # target_qty<=0("dust-safe 전량 축소", candidate_c_backtest_signal_adapter.py의
    # INTENT_REDUCE 분기가 잔여 없이 반환하는 경우)을 "잔여 0인 부분 축소"로 처리하면
    # trade.base_quantity=0인 채로 포지션이 positions dict에 계속 남는다 - 그 뒤 진짜
    # FLAT 신호가 왔을 때 _execute_order의 종가 처리가 0으로 나누기(ZeroDivisionError)를
    # 일으킨다. target_qty<=0은 부분 축소가 아니라 "전량 청산"과 동등하게 처리해야
    # 한다(_execute_order의 SIDE_FLAT 분기와 동일한 완결 처리 - 아래는 그 로직의 재사용).
    if target_qty <= 0:
        raw_price = bar["open"]
        effective_price, spread_cost_pu, slippage_cost_pu = _apply_price_costs(
            raw_price, trade.side, is_entry=False, spread_bps=cfg.spread_bps, slippage_bps=cfg.slippage_bps,
        )
        closing_quantity = trade.base_quantity
        fee_cost = effective_price * closing_quantity * cfg.fee_rate
        entry_spread_cost_pu = trade.spread_cost / trade.base_quantity
        entry_slippage_cost_pu = trade.slippage_cost / trade.base_quantity
        breakdown = cost_accounting.compute_cost_breakdown(
            side=trade.side, quantity=closing_quantity,
            raw_entry_price=trade.raw_entry_price, raw_exit_price=raw_price,
            effective_entry_price=trade.entry_price, effective_exit_price=effective_price,
            entry_spread_cost_per_unit=entry_spread_cost_pu, entry_slippage_cost_per_unit=entry_slippage_cost_pu,
            exit_spread_cost_per_unit=spread_cost_pu, exit_slippage_cost_per_unit=slippage_cost_pu,
            entry_fee_usdt=trade.entry_fee_usdt, exit_fee_usdt=fee_cost,
            funding_pnl_usdt=-trade.funding_cost,
        )
        trade.exit_time_ms = bar["open_time_ms"]
        trade.exit_price = effective_price
        trade.raw_exit_price = raw_price
        trade.raw_market_pnl = breakdown.raw_market_pnl
        trade.gross_pnl = breakdown.execution_adjusted_pnl
        trade.fee_cost += fee_cost
        trade.exit_fee_usdt = fee_cost
        trade.spread_cost += spread_cost_pu * closing_quantity
        trade.slippage_cost += slippage_cost_pu * closing_quantity
        trade.funding_pnl_usdt = -trade.funding_cost
        trade.net_pnl = breakdown.net_pnl
        trade.exit_reason = "signal"
        result.trades.append(trade)
        if risk_ledger is not None:
            risk_ledger.release(symbol)
        del positions[symbol]
        return

    reduced_qty = trade.base_quantity - target_qty
    if reduced_qty <= 0:
        return

    raw_price = bar["open"]
    effective_price, spread_cost_pu, slippage_cost_pu = _apply_price_costs(
        raw_price, trade.side, is_entry=False, spread_bps=cfg.spread_bps, slippage_bps=cfg.slippage_bps,
    )
    fee_cost = effective_price * reduced_qty * cfg.fee_rate
    entry_spread_cost_pu = trade.spread_cost / trade.base_quantity
    entry_slippage_cost_pu = trade.slippage_cost / trade.base_quantity
    breakdown = cost_accounting.compute_cost_breakdown(
        side=trade.side, quantity=reduced_qty,
        raw_entry_price=trade.raw_entry_price, raw_exit_price=raw_price,
        effective_entry_price=trade.entry_price, effective_exit_price=effective_price,
        entry_spread_cost_per_unit=entry_spread_cost_pu, entry_slippage_cost_per_unit=entry_slippage_cost_pu,
        exit_spread_cost_per_unit=spread_cost_pu, exit_slippage_cost_per_unit=slippage_cost_pu,
        entry_fee_usdt=trade.entry_fee_usdt * (reduced_qty / trade.base_quantity),
        exit_fee_usdt=fee_cost, funding_pnl_usdt=-trade.funding_cost * (reduced_qty / trade.base_quantity),
    )
    result.reduce_events.append(ReduceEvent(
        symbol=symbol, ts=bar["open_time_ms"], reduced_base_quantity=reduced_qty,
        residual_base_quantity=target_qty, fill_price=effective_price, realized_pnl=breakdown.net_pnl,
        position_id=trade.position_id,
        reduced_contracts=execution_units.coin_to_contracts(reduced_qty, meta.get("contract_size", 1.0)),
        gross_pnl=breakdown.execution_adjusted_pnl, raw_market_pnl=breakdown.raw_market_pnl,
        fee_cost=trade.entry_fee_usdt * (reduced_qty / trade.base_quantity) + fee_cost,
        entry_fee_usdt=trade.entry_fee_usdt * (reduced_qty / trade.base_quantity), exit_fee_usdt=fee_cost,
        spread_cost=(entry_spread_cost_pu + spread_cost_pu) * reduced_qty,
        slippage_cost=(entry_slippage_cost_pu + slippage_cost_pu) * reduced_qty,
        funding_cost=trade.funding_cost * (reduced_qty / trade.base_quantity),
    ))

    remaining_fraction = target_qty / trade.base_quantity
    # 남은 포지션의 누적 비용(spread/slippage/fee/funding)을 잔여 비율만큼
    # 비례 축소한다 - 이미 청산된 부분(reduce_events에 기록됨)의 몫이
    # 남은 trade에 이중으로 남지 않게 한다.
    trade.spread_cost *= remaining_fraction
    trade.slippage_cost *= remaining_fraction
    trade.fee_cost *= remaining_fraction
    trade.entry_fee_usdt *= remaining_fraction
    trade.funding_cost *= remaining_fraction
    trade.base_quantity = target_qty
    trade.contracts = execution_units.coin_to_contracts(target_qty, meta.get("contract_size", 1.0))

    if risk_ledger is not None:
        risk_ledger.scale_down(symbol, remaining_fraction)


def _execute_order(
    symbol: str, order: dict, bar: dict, meta: dict, cfg: EngineConfig,
    fill_model_fn, positions: dict, risk_ledger: _InMemoryRiskLedger, result: BacktestResult,
) -> None:
    if order.get("kind") == "reduce":
        _execute_reduce_order(symbol, order, bar, meta, cfg, positions, risk_ledger, result)
        return
    fill = fill_model_fn(order, bar)
    if fill.get("rejected"):
        result.rejected_orders.append({"symbol": symbol, "ts": bar["open_time_ms"], "order": order})
        if order["target_side"] == SIDE_FLAT:
            return  # 청산 거부 - 포지션 그대로 유지
        risk_ledger.release(symbol)
        return

    target_side = order["target_side"]
    current = positions.get(symbol)

    if target_side == SIDE_FLAT:
        if current is None:
            return
        trade = current["trade"]
        raw_price = bar["open"]
        effective_price, spread_cost_pu, slippage_cost_pu = _apply_price_costs(
            raw_price, trade.side, is_entry=False, spread_bps=cfg.spread_bps, slippage_bps=cfg.slippage_bps,
        )
        closing_quantity = trade.base_quantity * fill["fill_ratio"]
        fee_cost = effective_price * closing_quantity * cfg.fee_rate
        entry_spread_cost_pu = trade.spread_cost / trade.base_quantity  # 이 시점까지는 진입 다리 값만 누적돼 있음
        entry_slippage_cost_pu = trade.slippage_cost / trade.base_quantity
        breakdown = cost_accounting.compute_cost_breakdown(
            side=trade.side, quantity=closing_quantity,
            raw_entry_price=trade.raw_entry_price, raw_exit_price=raw_price,
            effective_entry_price=trade.entry_price, effective_exit_price=effective_price,
            entry_spread_cost_per_unit=entry_spread_cost_pu, entry_slippage_cost_per_unit=entry_slippage_cost_pu,
            exit_spread_cost_per_unit=spread_cost_pu, exit_slippage_cost_per_unit=slippage_cost_pu,
            entry_fee_usdt=trade.entry_fee_usdt, exit_fee_usdt=fee_cost,
            funding_pnl_usdt=-trade.funding_cost,
        )
        trade.exit_time_ms = bar["open_time_ms"]
        trade.exit_price = effective_price
        trade.raw_exit_price = raw_price
        trade.raw_market_pnl = breakdown.raw_market_pnl
        trade.gross_pnl = breakdown.execution_adjusted_pnl
        trade.fee_cost += fee_cost
        trade.exit_fee_usdt = fee_cost
        trade.spread_cost += spread_cost_pu * trade.base_quantity * fill["fill_ratio"]
        trade.slippage_cost += slippage_cost_pu * trade.base_quantity * fill["fill_ratio"]
        trade.funding_pnl_usdt = -trade.funding_cost
        trade.net_pnl = breakdown.net_pnl
        trade.exit_reason = "signal"
        result.trades.append(trade)
        risk_ledger.release(symbol)
        del positions[symbol]
        return

    # 신규 진입(long/short)
    raw_price = bar["open"]
    effective_price, spread_cost_pu, slippage_cost_pu = _apply_price_costs(
        raw_price, target_side, is_entry=True, spread_bps=cfg.spread_bps, slippage_bps=cfg.slippage_bps,
    )
    if cfg.sizing_config is not None:
        size = execution_units.calculate_candidate_entry_size(
            config_payload=cfg.sizing_config, entry_price=effective_price, equity=cfg.equity_usdt,
            stop_risk_per_coin=abs(effective_price - (order.get("stop_price") or effective_price)),
            contract_size=meta["contract_size"], lot_step=meta["lot_step"],
            min_contracts=meta.get("min_size", meta.get("min_contracts")),
        )
        base_quantity_full = size["amount_coin"]
    else:
        notional_usdt = cfg.risk_per_trade_usdt / (cfg.stop_distance_pct / 100.0)
        base_quantity_full = notional_usdt / effective_price
    base_quantity = base_quantity_full * fill["fill_ratio"]
    contract_size = meta.get("contract_size", 1.0)
    base_quantity = execution_units.round_amount_to_lot_step(base_quantity, contract_size, meta.get("lot_step"))
    contracts = execution_units.coin_to_contracts(base_quantity, contract_size)
    if base_quantity <= 0 or not execution_units.meets_minimum_amount(
        base_quantity, contract_size, meta.get("min_size", meta.get("min_contracts")),
    ):
        result.rejected_orders.append({"symbol": symbol, "ts": bar["open_time_ms"], "reason": "below_minimum"})
        risk_ledger.release(symbol)
        return
    fee_cost = effective_price * base_quantity * cfg.fee_rate

    initial_stop = order.get("stop_price")
    positions[symbol] = {
        "side": target_side,
        "stop_price": initial_stop,
        "initial_stop_price": initial_stop,
        # Phase 3.4 항목6.1 - high_water는 effective(비용 반영) 가격이 아니라
        # 보호주문 trigger/캔들 high-low와 동일한 raw 가격 기준을 쓴다. 이 시점
        # 이후 같은 ts의 2.5단계(high_water 갱신)가 이 진입 bar 자신의 high/low를
        # 이미 이 값과 max/min 비교하므로, "raw_entry_price와 N+1 high/low 중 더
        # 유리한 쪽"이 자동으로 반영된다(항목6.2).
        "high_water": raw_price,
        "trade": Trade(
            symbol=symbol, side=target_side, entry_time_ms=bar["open_time_ms"], entry_price=effective_price,
            exit_time_ms=None, exit_price=None, contracts=contracts, base_quantity=base_quantity,
            gross_pnl=None, fee_cost=fee_cost,
            spread_cost=spread_cost_pu * base_quantity, slippage_cost=slippage_cost_pu * base_quantity,
            funding_cost=0.0, net_pnl=None,
            raw_entry_price=raw_price, entry_fee_usdt=fee_cost,
            position_id=f"{symbol}#{bar['open_time_ms']}",
        ),
    }
