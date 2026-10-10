"""Candidate C 전향(forward) 가상 체결 기록/평가 - 실주문 경로와 완전히 분리(2026-09-14).

배경(ChatGPT 검토 지적, 2026-09-14 세 번째 재검토 - R1/R2/R3): 이전 버전은
GPT가 승인한 EntryIntent만 보고 가상 포지션을 열었지만, 그 뒤로는 실제
decide()의 current_position에 이 가상 포지션이 전혀 반영되지 않았다 - 실제
Shadow 계정은 진입 자체를 실행하지 않으므로 real epoch_store가 생기지 않고
(candidate_c_hybrid_live_adapter.py의 CANDIDATE_C_LIVE_EXECUTE 하드 게이트가
GPT 승인 "직후", 실제 epoch 생성 이전에 조기 반환함), run_steady_state_cycle()의
current_position은 항상 None으로 남는다. 그 결과 decide()는 이 가상 포지션에
대해 절대로 ReduceIntent/StopUpdateIntent/ReversalIntent를 낼 수 없었다(모두
current_position is not None을 전제로 함) - 이전 버전의 "관리 Intent 처리"
분기는 result(실제 사이클 반환값)에서 그 값이 나오는 걸 기다렸지만, 실제로는
절대 나오지 않는 값을 기다리고 있었던 셈이다(unit test는 이 값을 직접 주입해
통과했을 뿐 실제 재현이 아니었음).

이번 수정: 가상 포지션이 열려 있는 동안은 이 모듈 자신이 공유 결정 엔진
(candidate_c_decision_engine.decide())을 별도의(실주문 계좌/ledger/epoch와
완전히 분리된) 가상 포지션 상태로 직접 호출해, 그 결과(Reduce/StopUpdate/
Reversal/Exit)를 이 모듈 자신이 적용한다. run_steady_state_cycle() 자체를
다시 부르지 않는다(GPT 진입 게이트 재호출 방지 - 관리 Intent는 애초에 GPT를
거치지 않으므로 안전하게 분리 가능: candidate_c_hybrid_live_adapter.py의
GPT 게이트(gga.verify_candidate_signal)는 _execute_entry() 안에서만, 즉
EntryIntent에서만 호출된다).

단순화(명시적으로 공개):
- 체결가는 그 순간 가장 최근 확정 5분봉의 종가를 쓴다(다음 확정봉 시가를 쓰는
  backtest_engine.py와 다른 규칙 - "그 순간 실제로 봤을 시세"에 더 가깝게
  근사하려는 의도). look-ahead는 없다(항상 이미 확정된 봉만 쓴다).
- 수수료/스프레드/슬리피지 가정치는 scripts/run_candidate_c_backtest.py와 동일한
  값을 그대로 재사용한다(TAKER_FEE_RATE=0.0005, 스프레드/슬리피지 각 3bp).
- 진입 이후 새로 들어온 확정봉을 전부(마지막 봉 하나가 아니라) 시간순으로
  순회하며 손절/익절 터치와 decide() 재평가를 진행한다 - 재시작/누락봉으로
  중간 확정봉을 건너뛰지 않는다(2026-09-14 R2 수정).
- CANDIDATE_C_MAX_CONCURRENT_POSITIONS를 가상 계좌에도 반영한다 - 이미 그
  개수만큼 다른 심볼의 가상 포지션이 열려 있으면 새 가상 진입을 열지 않는다
  (2026-09-14 R3 수정, 심볼별 파일 존재 여부를 세는 방식이라 별도 잠금 없이
  일관되게 셀 수 있다).
- decide()가 관리 판단에 필요로 하는 high_water/weakening_prev/derisk_done/
  profit_lock_active/profit_lock_stop_price를 이 모듈이 "가상 epoch"로 직접
  들고 다니며 candidate_c_hybrid_cycle.run_steady_state_cycle()과 동일한
  공식으로 갱신한다(그 함수를 그대로 재사용하지 않고 공식만 그대로 옮긴 이유 -
  그 함수는 real epoch_store/intent_ledger/GPT 게이트에 강하게 결합돼 있어
  가상 계좌만 분리해 재사용하기 어려움).
"""
from __future__ import annotations

import datetime
import glob
import json
import math
import os
import threading

import candidate_c_decision_engine as dec
import candidate_c_exit_management as cem
import candidate_c_reversal_state_machine as rsm
import candidate_c_setup_tracker as st
import cost_accounting
import stop_contract
import strategy_indicators as si

TAKER_FEE_RATE = 0.0005
ASSUMED_SPREAD_BPS = 3.0
ASSUMED_SLIPPAGE_BPS = 3.0
MODEL_VERSION = "v3_decide_driven"

_lock = threading.Lock()


def _open_path(user_dir: str, symbol: str) -> str:
    safe = symbol.replace("/", "_").replace(":", "_")
    return os.path.join(user_dir, f"candidate_c_forward_paper_open_{safe}.json")


def _trades_path(user_dir: str) -> str:
    return os.path.join(user_dir, "candidate_c_forward_paper_trades.jsonl")


def _load_open(user_dir: str, symbol: str) -> dict | None:
    path = _open_path(user_dir, symbol)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _save_open_atomic(user_dir: str, symbol: str, state: dict | None) -> None:
    import tempfile
    path = _open_path(user_dir, symbol)
    if state is None:
        try:
            os.remove(path)
        except OSError:
            pass
        return
    os.makedirs(user_dir, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=user_dir, prefix=".cc_forward_paper_open.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


def _count_open_positions(user_dir: str, *, exclude_symbol: str | None = None) -> int:
    """다른 심볼들의 가상 포지션 파일 수를 센다(2026-09-14 R3 수정) - 파일 하나 =
    열린 가상 포지션 하나(이 모듈 자신의 불변식)이므로 잠금 없이 안전하게 셀 수 있다."""
    exclude_path = _open_path(user_dir, exclude_symbol) if exclude_symbol else None
    pattern = os.path.join(user_dir, "candidate_c_forward_paper_open_*.json")
    return len([p for p in glob.glob(pattern) if p != exclude_path])


def _append_trade(user_dir: str, record: dict) -> None:
    os.makedirs(user_dir, exist_ok=True)
    with open(_trades_path(user_dir), "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _apply_price_costs(raw_price: float, side: str, is_entry: bool,
                        spread_bps: float, slippage_bps: float) -> tuple[float, float, float]:
    """backtest_engine._apply_price_costs()와 동일한 공식(왕복 전체를 한 번에
    청산 시점에서 되짚어 계산하지 않고, 진입/청산 각 다리를 개별적으로 불리한
    방향으로 조정) - 매수 방향 주문은 더 비싸게, 매도 방향은 더 싸게 체결된다고
    가정한다. 반환: (effective_price, spread_cost_per_unit, slippage_cost_per_unit).
    backtest_engine.py의 함수를 직접 import하지 않는 이유 - 그쪽은 모듈 비공개
    함수이고 이 공식 자체는 cost_accounting.py의 계약(불변식 docstring)에 따라
    고정돼 있어, 여기서 같은 공식을 독립적으로 재구현해도 어긋날 위험이 없다."""
    buying = (side == "long" and is_entry) or (side == "short" and not is_entry)
    direction = 1 if buying else -1
    spread_cost_per_unit = raw_price * (spread_bps / 10_000.0)
    slippage_cost_per_unit = raw_price * (slippage_bps / 10_000.0)
    effective_price = raw_price + direction * (spread_cost_per_unit + slippage_cost_per_unit)
    return effective_price, spread_cost_per_unit, slippage_cost_per_unit


def _cost_breakdown_for_exit(open_state: dict, exit_price: float, closed_base_qty: float,
                              entry_fee_usdt_for_this_portion: float) -> cost_accounting.CostBreakdown:
    """2026-09-14 수정(ChatGPT v5 재검토 P1 지적, 직접 재현 확인) - 이전에는
    청산 시점에 (entry_price+exit_price)/2 기준 왕복 비용을 한 번에 되짚어
    계산했는데, candidate_c_exit_management.compute_profit_lock_stop_price()는
    cost_accounting.py의 단일 방향(진입 따로/청산 따로) 계약을 전제로 손익분기
    가격을 역산한다 - 서로 다른 두 비용 모형이 섞여, "손익분기로 계산된" 가격에서
    실제로 청산해도 net_pnl이 음수가 나오는 자기모순이 있었다(재현: 진입1.0/
    SL0.9/종가1.12/ATR 통제, profit-lock가 1.0011009106714652를 내놨지만 그
    가격에서 150계약 청산 시 실제 Net -0.1650450). 이제 진입 시점에 저장해 둔
    effective_entry_price/entry_fee_usdt(_apply_price_costs 기준, cost_accounting.py와
    같은 단일 방향 공식)를 그대로 재사용해 cost_accounting.compute_cost_breakdown()으로
    계산한다 - compute_profit_lock_stop_price()가 역산할 때 쓰는 것과 대수적으로
    동일한 식이므로, 그 가격에서 실제로 청산하면 net_pnl이 정확히 0(그 이상)이
    된다(직접 검증됨)."""
    side = open_state["side"]
    raw_entry_price = open_state["entry_price"]
    effective_entry_price = open_state.get("effective_entry_price", raw_entry_price)
    entry_spread_pu = open_state.get("entry_spread_cost_per_unit", 0.0)
    entry_slippage_pu = open_state.get("entry_slippage_cost_per_unit", 0.0)
    effective_exit_price, exit_spread_pu, exit_slippage_pu = _apply_price_costs(
        exit_price, side, False, ASSUMED_SPREAD_BPS, ASSUMED_SLIPPAGE_BPS,
    )
    exit_fee_usdt = effective_exit_price * closed_base_qty * TAKER_FEE_RATE
    return cost_accounting.compute_cost_breakdown(
        side=side, quantity=closed_base_qty,
        raw_entry_price=raw_entry_price, raw_exit_price=exit_price,
        effective_entry_price=effective_entry_price, effective_exit_price=effective_exit_price,
        entry_spread_cost_per_unit=entry_spread_pu, entry_slippage_cost_per_unit=entry_slippage_pu,
        exit_spread_cost_per_unit=exit_spread_pu, exit_slippage_cost_per_unit=exit_slippage_pu,
        entry_fee_usdt=entry_fee_usdt_for_this_portion, exit_fee_usdt=exit_fee_usdt,
        funding_pnl_usdt=0.0,
    )


def _close_virtual(user_dir: str, symbol: str, open_state: dict, exit_price: float,
                    exit_time_ms: int, exit_reason: str) -> None:
    side = open_state["side"]
    contracts = open_state["contracts"]
    contract_size = open_state["contract_size"]
    base_qty = contracts * contract_size
    # 이전에 부분감축(_reduce_virtual)이 있었다면 entry_fee_usdt는 이미 그만큼
    # 비례 축소돼 남은 잔량 몫만 여기 남아있다(아래 참고).
    entry_fee_remaining = open_state.get("entry_fee_usdt", 0.0)
    breakdown = _cost_breakdown_for_exit(open_state, exit_price, base_qty, entry_fee_remaining)
    _append_trade(user_dir, {
        "symbol": symbol, "side": side, "entry_price": open_state["entry_price"], "exit_price": exit_price,
        "contracts": contracts, "contract_size": contract_size,
        "entry_time_ms": open_state["entry_time_ms"], "exit_time_ms": exit_time_ms,
        "entry_config_hash": open_state.get("config_hash"), "exit_reason": exit_reason,
        "gross_pnl": breakdown.raw_market_pnl,
        "fee_cost": breakdown.entry_fee_usdt + breakdown.exit_fee_usdt,
        "spread_cost": breakdown.spread_cost_usdt, "slippage_cost": breakdown.slippage_cost_usdt,
        "net_pnl": breakdown.net_pnl, "recorded_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "model_version": MODEL_VERSION,
        # 이관된 레거시 포지션이면 그 사실을 거래 기록에도 남긴다(조용히 새
        # 모델의 온전한 실적으로 섞이지 않도록 - P1-3 수정).
        "legacy_migration": open_state.get("legacy_migration"),
    })
    _save_open_atomic(user_dir, symbol, None)


def _reduce_virtual(user_dir: str, symbol: str, open_state: dict, reduced_contracts: float,
                     remaining_contracts: float, exit_price: float, exit_time_ms: int,
                     reason_code: str | None = None) -> dict:
    """실제 실행 어댑터(candidate_c_hybrid_live_adapter._finalize_managed)와 같은
    계약 - ReduceIntent는 잔량을 남기고 감축된 수량분만 실현손익/비용으로
    기록한다. 남은 계약은 동일한 entry_price/stop/target/entry_time으로 계속
    추적한다(별도 포지션이 아니라 같은 epoch의 잔량). 갱신된 open_state를
    반환한다(호출부가 이어서 같은 딕셔너리를 계속 쓸 수 있게).

    entry_fee_usdt는 backtest_engine.py의 Trade.entry_fee_usdt와 동일하게, 감축
    비율만큼 이번 청산분으로 떼어내고 나머지는 잔량에 남겨 다음 청산(추가 감축
    또는 최종 전량청산)에서 다시 비례 배분되게 한다 - 그래야 최종적으로 원래
    진입 수수료 총액을 정확히 한 번씩만 나눠 쓴다(이중차감/누락 없음)."""
    side = open_state["side"]
    contract_size = open_state["contract_size"]
    current_contracts = open_state["contracts"]
    base_qty = reduced_contracts * contract_size
    total_entry_fee = open_state.get("entry_fee_usdt", 0.0)
    fraction = (reduced_contracts / current_contracts) if current_contracts else 0.0
    entry_fee_for_this_reduce = total_entry_fee * fraction
    breakdown = _cost_breakdown_for_exit(open_state, exit_price, base_qty, entry_fee_for_this_reduce)
    _append_trade(user_dir, {
        "symbol": symbol, "side": side, "entry_price": open_state["entry_price"], "exit_price": exit_price,
        "contracts": reduced_contracts, "contract_size": contract_size,
        "entry_time_ms": open_state["entry_time_ms"], "exit_time_ms": exit_time_ms,
        "entry_config_hash": open_state.get("config_hash"), "exit_reason": "partial_reduce",
        "gross_pnl": breakdown.raw_market_pnl,
        "fee_cost": breakdown.entry_fee_usdt + breakdown.exit_fee_usdt,
        "spread_cost": breakdown.spread_cost_usdt, "slippage_cost": breakdown.slippage_cost_usdt,
        "net_pnl": breakdown.net_pnl, "recorded_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "model_version": MODEL_VERSION, "remaining_contracts_after": remaining_contracts,
        "legacy_migration": open_state.get("legacy_migration"),
    })
    open_state["contracts"] = remaining_contracts
    open_state["entry_fee_usdt"] = total_entry_fee - entry_fee_for_this_reduce
    if reason_code in ("partial_take_profit_2r", "PARTIAL_TP_2R_DUST_SAFE_FULL_EXIT"):
        open_state["partial_take_profit_done"] = True
    else:
        open_state["derisk_done"] = True
    _save_open_atomic(user_dir, symbol, open_state)
    return open_state


def _build_current_position(open_state: dict) -> dict:
    """candidate_c_decision_engine.decide()가 기대하는 current_position 스키마
    그대로 - candidate_c_position_reconciliation.build_candidate_c_position_state()
    와 필드가 동일하다(그 함수를 그대로 재사용하려면 실제 PositionEpochState
    객체가 필요해, 여기서는 같은 필드를 가상 open_state에서 직접 만든다).

    effective_entry_price/entry_fee_usdt(2026-09-14 수정, ChatGPT v5 재검토 P1
    지적) - 이전에는 raw 진입가와 수수료 0을 그대로 넣어 decide()의 profit-lock
    가격 역산이 "수수료 없음"을 가정했지만, 실제 청산(_close_virtual)은 실제
    수수료를 뗐다 - 자기모순. 이제 진입 시 _apply_price_costs로 실제 계산해
    저장해 둔 값을 그대로 쓴다(레거시 open_state에 이 필드가 없으면 - 2026-09-10
    이전 형식 - raw 값/0으로 안전하게 대체하되, 이 fallback은 P1-3 레거시 이관
    처리와 함께 다뤄야 한다는 것을 알아두는 게 좋다)."""
    return {
        "side": open_state["side"], "position_id": f"paper:{open_state['entry_time_ms']}",
        "contracts": open_state["contracts"], "raw_entry_price": open_state["entry_price"],
        "initial_stop_price": open_state.get("initial_stop_price") or open_state["entry_price"],
        "high_water": open_state.get("high_water", open_state["entry_price"]),
        "effective_entry_price": open_state.get("effective_entry_price", open_state["entry_price"]),
        "entry_fee_usdt": open_state.get("entry_fee_usdt", 0.0),
        "contract_size": open_state["contract_size"], "fee_rate": TAKER_FEE_RATE,
        "spread_bps": ASSUMED_SPREAD_BPS, "slippage_bps": ASSUMED_SLIPPAGE_BPS,
    }


def _paper_decide(open_state: dict, bars_5m_window: list, bars_4h: list, bars_1h: list,
                   strategy_policy: dict | None, *, indicator_fn=None,
                   lot_step: float = 0.0001, min_size: float = 0.0001, risk_adaptive_partials=False):
    """실제 라이브(run_steady_state_cycle)와 동일한 공식으로 high_water를 먼저
    갱신한 뒤 decide()를 호출한다. setup_tracker/reversal_machine은 이 호출에서
    실제로 쓰이지 않는다(current_position이 있는 관리 분기는 둘 다 참조하지
    않음 - decide() 자체 코드로 확인됨) - 그래도 매번 새 인메모리 인스턴스를
    만들어 넘긴다(영속할 이유가 없고, SAFE_HALT/PENDING류 상태가 아니라는
    것만 보장하면 충분함).

    indicator_fn/lot_step/min_size(2026-09-14 수정, ChatGPT v5 재검토 P1 지적,
    직접 재현 확인) - 이전에는 항상 si.augment_with_indicators로 그때그때
    넘어온 창만으로 지표를 새로 계산했는데, 실제 사이클(candidate_c_trader_
    adapter.py)은 연속 checkpoint(IndicatorBook)를 쓴다 - 같은 마지막 봉이어도
    이전에 얼마나 긴 이력을 누적해 왔는지에 따라 EMA 같은 지표가 달라질 수
    있다(400봉 대 마지막 200봉만 넣은 통제 실험에서 같은 마지막 봉의 EMA50이
    1.4996648950939402 대 1.5로 실제로 달랐고 방향 판정까지 LONG/NONE으로
    갈렸다). lot_step/min_size도 0.0001 고정값 대신 실제 상품 규칙을 받아야
    dust_full_exit/부분감축 갈림이 실제 계약과 같아진다. indicator_fn이 없으면
    (독립 테스트 등) 기존처럼 si.augment_with_indicators로 대체한다."""
    indicator_fn = indicator_fn or si.augment_with_indicators
    bar = bars_5m_window[-1]
    side = open_state["side"]
    old_water = open_state.get("high_water", open_state["entry_price"])
    new_water = max(old_water, bar["high"]) if side == "long" else min(old_water, bar["low"])
    open_state["high_water"] = new_water

    current_position = _build_current_position(open_state)
    # decide()/build_as_of_snapshot 계약(2026-09-14 실행으로 확인) - as_of_ms는
    # close_time_ms가 아니라 "확정 5분봉의 open_time_ms"여야 하고(candidate_c_
    # timeframe_contract.build_as_of_snapshot이 bars_5m_for_10m 안에서 이 값과
    # open_time_ms가 일치하는 확정봉을 찾아 그 close_time_ms로 다시 치환한다),
    # bars_5m_for_10m_up_to_asof도 그 시점의 5분봉 한 개가 아니라 10분봉 파생에
    # 필요한 만큼의 앞선 확정 5분봉 구간을 함께 받아야 한다 - 실제 라이브
    # (run_steady_state_cycle)도 매 사이클 전체 5분봉 윈도우를 그대로 넘긴다.
    as_of_ms = bar["open_time_ms"]
    close_time_ms = bar["close_time_ms"]
    bars_4h_confirmed = [b for b in bars_4h if b.get("confirm") == 1 and b["close_time_ms"] <= close_time_ms]
    bars_1h_confirmed = [b for b in bars_1h if b.get("confirm") == 1 and b["close_time_ms"] <= close_time_ms]

    ctx = dec.DecisionContext(
        account_id="paper", symbol=open_state.get("symbol", ""), strategy_id="candidate_c_paper",
        config_version_id=open_state.get("config_hash", "v1"), config_hash=open_state.get("config_hash", "v1"),
        risk_per_trade_pct=1.0, strategy_policy=strategy_policy,
        risk_adaptive_partials=risk_adaptive_partials,
    )
    intent = dec.decide(
        ctx, as_of_ms=as_of_ms,
        bars_4h_confirmed_up_to_asof=bars_4h_confirmed, bars_1h_confirmed_up_to_asof=bars_1h_confirmed,
        bars_1d_confirmed_up_to_asof=[], bars_5m_for_10m_up_to_asof=bars_5m_window,
        setup_tracker=st.SetupTracker.in_memory(), epoch_store=_PaperEpochView(open_state),
        reversal_machine=rsm.SymbolReversalMachine(open_state.get("symbol", "paper")),
        current_position=current_position, weakening_prev=open_state.get("weakening_prev", False),
        lot_step=lot_step, min_size=min_size, indicator_fn=indicator_fn,
    )

    if intent.reduction_policy_hash:
        open_state['reduction_policy_hash'] = intent.reduction_policy_hash
    # 실제 라이브(run_steady_state_cycle)와 동일하게, weakening_prev는 이 결정
    # "이후"의 상태로 갱신해 저장한다(이번 판단 자체는 이전 값을 썼음) - 같은
    # indicator_fn을 써서 decide() 내부와 동일한 EMA를 본다.
    if bars_1h_confirmed:
        ema_20_1h = indicator_fn(bars_1h_confirmed, donchian_n=20)[-1]["ema_20"]
        open_state["weakening_prev"] = dec.weakening_from_1h(
            side=side, close_1h=bars_1h_confirmed[-1]["close"], ema_20_1h=ema_20_1h,
        )
    if open_state.get("pilot_entry") and bars_4h_confirmed:
        row4 = indicator_fn(bars_4h_confirmed, donchian_n=20)[-1]
        direction4 = dec.direction_from_4h_indicators(
            ema_20=row4.get("ema_20"), ema_50=row4.get("ema_50"), close=row4.get("close"))
        if direction4 == ("LONG" if side == "long" else "SHORT"):
            open_state["pilot_4h_confirmed"] = True
    return intent


class _PaperEpochView:
    """decide()의 관리 분기가 epoch_store.get()으로 derisk_done/profit_lock_active
    두 필드를 실제로 읽는다(2026-09-14 - 처음엔 "전혀 참조 안 함"으로 가정했으나,
    직접 실행해 AssertionError로 드러남 - _reduce_virtual/_apply_paper_intent가
    이미 이 두 필드를 open_state에 갱신해 두므로, 그 값을 real PositionEpochState
    모양으로 감싸 돌려주기만 하면 된다). 실제 파일 기반 PositionEpochStore는
    새로 만들거나 재사용하지 않는다(실주문 epoch와 완전히 분리 유지) - 이
    open_state 자체가 이 가상 포지션의 유일한 영속 저장소다.
    decide()는 이 반환값에 대해 save()를 호출하지 않는다(순수 함수라는 계약 -
    decide() 소스로 확인됨, epoch_store.save는 호출부만 부름) - 혹시라도 실제로
    호출되면(가정이 또 틀렸다는 뜻) 조용히 넘어가지 않고 바로 드러나도록
    예외를 던진다."""
    def __init__(self, open_state: dict):
        self._open_state = open_state

    def get(self, key):
        return cem.PositionEpochState(
            derisk_done=self._open_state.get("derisk_done", False),
            pilot_entry=self._open_state.get("pilot_entry", False),
            pilot_4h_confirmed=self._open_state.get("pilot_4h_confirmed", False),
            partial_take_profit_done=self._open_state.get("partial_take_profit_done", False),
            profit_lock_active=self._open_state.get("profit_lock_active", False),
            original_contracts=self._open_state.get("original_contracts",
                                                     self._open_state.get("contracts")),
        )

    def save(self, key, value):
        raise AssertionError("decide()가 관리 분기에서 epoch_store.save()를 실제로 호출함 - 가정 재검토 필요")


def _apply_paper_intent(user_dir: str, symbol: str, open_state: dict, intent, bar) -> bool:
    """paper_decide()가 낸 Intent를 적용한다. 포지션이 이 호출로 완전히
    닫혔으면 True를 반환한다(호출부가 같은 배치의 나머지 봉 처리를 멈추게)."""
    as_of_ms = bar["close_time_ms"]
    close = bar["close"]
    if intent.kind == dec.INTENT_STOP_UPDATE:
        # 2026-09-14 수정(ChatGPT v5 재검토 P1 지적, 직접 재현 확인) - decide()의
        # trailing 분기는 이미 profit-lock으로 더 타이트해진 stop을 모르고 순수
        # ATR 공식만으로 새 후보를 낸다(진입가1.0/SL0.9/종가1.12 재현: profit-lock
        # SL 1.0011... 다음 사이클에서 trailing 후보 0.95로 실제로 후퇴함).
        # backtest_engine.py/portfolio_mtm_engine.py와 동일한 공통 계약
        # (stop_contract.stop_would_loosen)을 여기서도 적용해, 후퇴하는 후보는
        # 아예 반영하지 않는다(가격도, profit_lock_active 플래그도 그대로 둔다 -
        # 플래그만 True로 바꾸고 가격은 안 바꾸면 "보호가 개선됐다"는 거짓 기록이
        # 됨).
        if (intent.raw_stop_price is not None and not stop_contract.stop_would_loosen(
                open_state["side"], open_state.get("stop_price"), intent.raw_stop_price)):
            open_state["stop_price"] = intent.raw_stop_price
            if intent.reason_code == "profit_lock_activated":
                open_state["profit_lock_active"] = True
                open_state["profit_lock_stop_price"] = intent.raw_stop_price
        _save_open_atomic(user_dir, symbol, open_state)
        return False
    if intent.kind in (dec.INTENT_EXIT, dec.INTENT_REVERSAL):
        _close_virtual(user_dir, symbol, open_state, close, as_of_ms, "signal_exit")
        return True
    if intent.kind == dec.INTENT_REDUCE:
        current_contracts = open_state["contracts"]
        target_residual = intent.target_residual
        reduce_quantity = intent.reduce_quantity
        if target_residual is None or target_residual <= 1e-9:
            _close_virtual(user_dir, symbol, open_state, close, as_of_ms, "reduce_dust_full_exit")
            return True
        if (isinstance(reduce_quantity, (int, float)) and reduce_quantity > 1e-9
                and target_residual < current_contracts - 1e-9):
            reduced = current_contracts - target_residual
            _reduce_virtual(user_dir, symbol, open_state, reduced, target_residual,
                            close, as_of_ms, reason_code=intent.reason_code)
        return False
    # NoAction 등 - high_water/weakening_prev만 갱신된 채로 저장한다.
    _save_open_atomic(user_dir, symbol, open_state)
    return False


def update(cfg, symbol: str, result: dict, bars_5m_confirmed: list, contract_size: float,
           *, bars_4h: list | None = None, bars_1h: list | None = None,
           strategy_policy: dict | None = None, indicator_fn=None, indicator_ready: bool = True,
           lot_step: float = 0.0001, min_size: float = 0.0001) -> None:
    """매 사이클, run_steady_state_cycle()의 실제 결과를 받은 직후 호출한다.
    이 함수 자체가 예외를 던지면 호출부가 삼켜야 한다(실제 사이클에 영향 금지).
    실제 거래소 변경 호출은 이 모듈 어디에도 없다(client를 아예 받지 않음).

    indicator_fn/indicator_ready/lot_step/min_size(2026-09-14 수정, ChatGPT v5
    재검토 P1 지적) - 실제 사이클(candidate_c_trader_adapter.py)이 이번 사이클에
    쓴 것과 동일한 연속 지표 checkpoint·상품 규칙을 그대로 넘겨받는다. indicator_
    ready=False(WARMING_UP 등)면 이번 배치의 어떤 확정봉도 decide() 판단으로
    누적하지 않는다(신뢰할 수 없는 지표로 낸 가상 관리 판단을 정상 검증 자료처럼
    쌓지 않기 위함) - 다만 이미 걸려있는 보호(stop/target 터치)는 지표 상태와
    무관하게 계속 확인한다."""
    if not bars_5m_confirmed:
        return
    bars_4h = bars_4h or []
    bars_1h = bars_1h or []

    with _lock:
        open_state = _load_open(cfg.user_dir, symbol)

        if open_state is not None:
            open_state.setdefault("symbol", symbol)
            # 2026-09-14 R2 수정 - 마지막 봉 하나만 보지 않는다. 아직 처리하지
            # 않은(entry 이후, 직전 처리 이후) 확정봉을 시간순으로 전부 순회한다.
            last_processed = open_state.get("last_processed_close_ms")
            if last_processed is None:
                # 2026-09-14 수정(ChatGPT v5 재검토 P1 지적, 직접 재현 확인) - v4
                # 이전 형식은 last_processed_close_ms 필드 자체가 없었다. 그 시절
                # 코드는 entry_time_ms(진입봉의 open 시각)를 대신 커서로 썼는데,
                # 그 값은 진입봉 자신의 close_time_ms보다 항상 작아서 진입봉이
                # 매번 "새 확정봉"으로 재처리되고 그 봉의 저가/고가로 소급
                # 손절/익절될 수 있었다(재현: 진입가1.0/SL0.9/저가0.8인 진입봉을
                # 재입력하면 실제로 stop_touch로 닫히고
                # model_version="v3_decide_driven"으로 기록됨 - 새 모델이 실제로
                # 낸 거래처럼 보임). 진입봉의 정확한 close_time_ms를 몰라도
                # 안전한 open_time_ms 기준 하한(진입봉 자신과 그 이전을 명시적으로
                # 제외)으로 대체한다 - 원본 trade 기록은 건드리지 않고, 이 위치를
                # 처음 통과할 때만 이관 사실 자체를 남긴다(조용히 새 모델의 온전한
                # 실적으로 재분류하지 않기 위함).
                open_state.setdefault("legacy_migration", {
                    "migrated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    "reason": "pre_last_processed_close_ms_format_missing_cursor",
                })
                # 이번 배치에 처리할 새 확정봉이 하나도 없어(아래 for가 빈 채로
                # 끝나) 루프 안의 _save_open_atomic()에 한 번도 안 닿더라도, 이관
                # 표시 자체는 즉시 영속화한다(다음 호출까지 미뤄지면 안 됨).
                _save_open_atomic(cfg.user_dir, symbol, open_state)
                new_bars = sorted(
                    (b for b in bars_5m_confirmed if b["open_time_ms"] > open_state["entry_time_ms"]),
                    key=lambda b: b["close_time_ms"],
                )
            else:
                new_bars = sorted(
                    (b for b in bars_5m_confirmed if b["close_time_ms"] > last_processed),
                    key=lambda b: b["close_time_ms"],
                )
            for bar in new_bars:
                side = open_state["side"]
                stop = open_state.get("stop_price")
                target = open_state.get("target_price")
                high, low = bar["high"], bar["low"]
                as_of_ms = bar["close_time_ms"]
                if side == "long" and stop is not None and low <= stop:
                    _close_virtual(cfg.user_dir, symbol, open_state, stop, as_of_ms, "stop_touch")
                    return
                if side == "long" and target is not None and high >= target:
                    _close_virtual(cfg.user_dir, symbol, open_state, target, as_of_ms, "target_touch")
                    return
                if side == "short" and stop is not None and high >= stop:
                    _close_virtual(cfg.user_dir, symbol, open_state, stop, as_of_ms, "stop_touch")
                    return
                if side == "short" and target is not None and low <= target:
                    _close_virtual(cfg.user_dir, symbol, open_state, target, as_of_ms, "target_touch")
                    return

                open_state["last_processed_close_ms"] = as_of_ms
                if not indicator_ready:
                    # 이미 걸려있는 stop/target 터치는 위에서 확인했다 - 신뢰할 수
                    # 없는 지표로 새 관리 판단(decide())만 이번 봉에서 건너뛴다.
                    _save_open_atomic(cfg.user_dir, symbol, open_state)
                    continue

                # decide()가 10분봉을 파생할 수 있게, 이 봉을 포함해 그 시점까지
                # 보이는 확정 5분봉 구간 전체를 넘긴다(호출부가 넘겨준 전체
                # bars_5m_confirmed 창 안에서, 이 봉 이후 시점은 제외).
                window = sorted(
                    (b for b in bars_5m_confirmed if b["open_time_ms"] <= bar["open_time_ms"]),
                    key=lambda b: b["open_time_ms"],
                )
                intent = _paper_decide(open_state, window, bars_4h, bars_1h, strategy_policy,
                                        indicator_fn=indicator_fn, lot_step=lot_step, min_size=min_size,
                                        risk_adaptive_partials=(getattr(cfg,'RISK_ADAPTIVE_PARTIAL_ENABLED',False)
                                            and getattr(cfg,'CANDIDATE_C_CHART_ONLY',False)))
                closed = _apply_paper_intent(cfg.user_dir, symbol, open_state, intent, bar)
                if closed:
                    return
            return

        # 열린 가상 포지션이 없다 - GPT가 실제로 승인한 진입 의도만 새로 연다.
        if not (result.get("intent_kind") == "EntryIntent"
                and result.get("gate_result") == "shadow_mode_live_execute_disabled"
                and result.get("side") in ("long", "short")):
            return
        max_concurrent = getattr(cfg, "CANDIDATE_C_MAX_CONCURRENT_POSITIONS", 1)
        if _count_open_positions(cfg.user_dir, exclude_symbol=symbol) >= max_concurrent:
            # 2026-09-14 R3 수정 - 다른 심볼에서 이미 한도만큼 가상 포지션이
            # 열려 있으면 이 가상 진입은 열지 않는다(실제 계좌 제약 반영).
            return
        latest = bars_5m_confirmed[-1]
        raw_close = latest["close"]
        as_of_ms = latest["close_time_ms"]
        # 2026-09-14 수정(ChatGPT v5 재검토 P1 지적) - 사이징·수수료 계산 모두
        # raw 체결가가 아니라 backtest_engine.py와 동일하게 spread/slippage가
        # 반영된 effective 가격 기준이어야 한다(_cost_breakdown_for_exit이 청산 때
        # 가정하는 것과 동일한 기준).
        effective_entry_price, entry_spread_pu, entry_slippage_pu = _apply_price_costs(
            raw_close, result["side"], True, ASSUMED_SPREAD_BPS, ASSUMED_SLIPPAGE_BPS,
        )
        margin = getattr(cfg, "CANDIDATE_C_FIXED_MARGIN_USDT", 50.0)
        leverage = getattr(cfg, "CANDIDATE_C_LEVERAGE", 3)
        entry_fraction = float(result.get("entry_size_fraction", 1.0))
        if not math.isfinite(entry_fraction) or not 0 < entry_fraction <= 1:
            return
        notional = margin * leverage * entry_fraction
        base_qty = notional / effective_entry_price if effective_entry_price else 0.0
        contracts = base_qty / contract_size if contract_size else 0.0
        if contracts <= 0:
            return
        entry_fee_usdt = effective_entry_price * base_qty * TAKER_FEE_RATE
        _save_open_atomic(cfg.user_dir, symbol, {
            "symbol": symbol, "side": result["side"], "entry_price": raw_close,
            "effective_entry_price": effective_entry_price, "entry_fee_usdt": entry_fee_usdt,
            "entry_spread_cost_per_unit": entry_spread_pu, "entry_slippage_cost_per_unit": entry_slippage_pu,
            "entry_time_ms": latest["open_time_ms"], "contracts": contracts, "contract_size": contract_size,
            "stop_price": result.get("raw_stop_price"), "target_price": result.get("raw_target_price"),
            "initial_stop_price": result.get("raw_stop_price"), "high_water": raw_close,
            "weakening_prev": bool(result.get("entry_weakening_baseline", False)), "derisk_done": False,
            "pilot_entry": bool(result.get("pilot_entry", False)), "pilot_4h_confirmed": False,
            "partial_take_profit_done": False, "original_contracts": contracts,
            "profit_lock_active": False, "profit_lock_stop_price": None,
            # entry가 일어난 이 봉 자체는 "이미 처리됨"으로 표시한다 - 재시작 등으로
            # 이 봉이 다시 전달돼도 진입 이전 가격(이 봉의 low/high)으로 즉시
            # 손절/익절 판정을 하지 않는다(2026-09-14 R2 수정, 첫 번째 재현).
            "last_processed_close_ms": as_of_ms,
            "config_hash": getattr(cfg, "CANDIDATE_C_CONFIG_VERSION_ID", "v1"),
        })


def _summarize_trades(trades: list) -> dict:
    wins = [t for t in trades if t["net_pnl"] > 0]
    losses = [t for t in trades if t["net_pnl"] <= 0]
    gross_profit = sum(t["net_pnl"] for t in wins)
    gross_loss = abs(sum(t["net_pnl"] for t in losses))
    return {
        "count": len(trades),
        "gross_pnl": sum(t["gross_pnl"] for t in trades),
        "fee": sum(t["fee_cost"] for t in trades),
        "spread": sum(t["spread_cost"] for t in trades),
        "slippage": sum(t["slippage_cost"] for t in trades),
        "net_pnl": sum(t["net_pnl"] for t in trades),
        "win_rate": (len(wins) / len(trades) * 100) if trades else None,
        "profit_factor": (gross_profit / gross_loss) if gross_loss > 0 else (None if gross_profit == 0 else float("inf")),
        "trades": trades,
    }


def _model_version_bucket(trade: dict) -> str:
    """이 거래가 어느 model_version 통계에 들어가야 하는지 결정한다.

    2026-09-14 수정(ChatGPT v7 재검토 P1-3 지적, 직접 재현 확인) - _close_virtual/
    _reduce_virtual은 청산 시점의 현재 MODEL_VERSION을 그대로 찍는다(관리 로직
    자체는 실제로 v3_decide_driven이 냈으므로 그 자체는 맞다) - 하지만 그 포지션이
    legacy_migration을 거친 것이면(진입 당시 커서·effective_entry_price/
    entry_fee_usdt 같은 필드가 없던 구형 open_state에서 이어받음), 이전 상태에서
    이어받았고 비용 정보도 완전하지 않은 거래가 최신 모델에서 처음부터 끝까지
    온전히 발생한 거래와 같은 버킷에 섞여, 검증용 성과 집계를 오염시켰다(재현:
    옛 형식 진입가1/SL0.9/150계약 포지션이 다음 봉 SL로 청산되면
    by_model_version['v3_decide_driven'].count가 1 늘어남 - 원래 상태 이력·
    비용 완전성 정보가 사라짐).

    거래 기록 자체의 model_version 필드는 그대로 둔다(관리 로직 자체가 무엇이었는지
    보여주는 값이라 바꾸지 않는다) - 집계 버킷 키만 legacy_migration 표시가 있으면
    구분한다."""
    base = trade.get("model_version") or "legacy_pre_partial_reduce"
    if trade.get("legacy_migration"):
        return base + "_legacy_migrated"
    return base


def summarize(user_dir: str) -> dict:
    """scripts/run_candidate_c_backtest.py의 _summarize()와 같은 형태로 집계한다
    (직접 비교 가능하게). model_version별로도 나눠 보여준다 - 이전 버전들의
    기록(가상 포지션이 decide()에 반영되지 않던 시절 - "v2_partial_reduce" 및
    그 이전, 필드 자체가 없는 legacy_pre_partial_reduce)과 이번 v3_decide_driven
    기록을 같은 "성과"로 섞어 보고하지 않기 위함이다. 구버전 기록은 지우거나
    고치지 않고 그대로 보존한다.

    legacy_migration을 거친 거래는(2026-09-14 v7 수정) 관리 로직상 같은
    MODEL_VERSION이어도 별도 "..._legacy_migrated" 버킷으로 다시 나눈다 -
    _model_version_bucket() 참고. 그래서 "by_model_version['v3_decide_driven']"은
    이제 진입부터 온전히 이 모델로 이뤄진 거래만 포함한다 - 검증용 성과에
    이관 거래를 섞지 않기 위함이다."""
    path = _trades_path(user_dir)
    if not os.path.exists(path):
        return {"count": 0, "trades": []}
    trades = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                trades.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    overall = _summarize_trades(trades)
    by_version: dict[str, list] = {}
    for t in trades:
        by_version.setdefault(_model_version_bucket(t), []).append(t)
    overall["by_model_version"] = {version: _summarize_trades(rows) for version, rows in by_version.items()}
    return overall
