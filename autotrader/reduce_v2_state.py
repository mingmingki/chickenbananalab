"""REDUCE v2(2026-09-13, 사용자 지시) - 보유 포지션 AI 관리의 반복 REDUCE_50 실행 사고
수정으로 도입한 포지션별 영속 상태.

사고(2026-09-13, PI 실거래): 메인 판단(Gemini)은 계속 bullish+hold였는데, 포지션
재점검(Gemini 재검토 + GPT 게이트)이 같은 단기(1분/3분봉) 약화 신호로 REDUCE_50을
11번 연속 반환했고, 실행계층(trader._execute_position_ai_reduce_50)은 매번 "현재
남은 수량의 절반"을 줄였을 뿐 포지션 단위 누적 감축 상태를 전혀 추적하지 않았다 -
7879계약이 11번 연속 반감돼 4계약까지 줄었다.

이 모듈은 심볼당 "지금 열려있는 포지션 하나"의 REDUCE v2 생애주기 상태를 디스크에
원자적으로 저장한다(core_short_downgrade.py/core_kill_switch.py와 동일한
tempfile+os.replace 패턴, jsonl_cache.get_path_lock으로 read-modify-write 직렬화).
포지션 식별은 side+entry_price(0.1% 허용오차 - okx_client.match_realized_close와
동일 관례)로 하고, 서버 재시작이나 entry_price 표시상의 아주 미세한 오차만으로는
절대 리셋하지 않는다 - side가 바뀌거나 entry_price가 허용오차를 벗어나야("진짜 새
포지션") 리셋한다."""
import datetime
import json
import math
import os
import tempfile

import jsonl_cache
import process_lock

ENTRY_PRICE_TOLERANCE = 1e-3  # okx_client.match_realized_close와 동일한 관례
CONTRACT_QUANTITY_TOLERANCE = 1e-8
MAX_CUMULATIVE_REDUCTION_RATIO = 0.50
STAGE_TARGET_RATIO = 0.25  # 스테이지마다 "최초 계약수" 대비 25%


def _state_path(user_dir: str) -> str:
    return os.path.join(user_dir, "reduce_v2_state.json")


def _load(user_dir: str) -> dict:
    path = _state_path(user_dir)
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError('REDUCE lifecycle state corrupt; entry/reduce blocked')
    return data


def _save_atomic(user_dir: str, data: dict) -> None:
    process_lock.save_json_atomic(_state_path(user_dir), data)


def _is_same_position(sym_state: dict, side: str, entry_price: float) -> bool:
    if sym_state.get("side") != side:
        return False
    stored_entry = sym_state.get("entry_price")
    if not isinstance(stored_entry, (int, float)) or stored_entry <= 0:
        return False
    if entry_price <= 0:
        return False
    return abs(entry_price - stored_entry) / stored_entry <= ENTRY_PRICE_TOLERANCE


def _default_state(side: str, entry_price: float, entry_time, initial_contracts: float) -> dict:
    return {
        "side": side,
        "entry_price": entry_price,
        "entry_time": entry_time,
        "initial_contracts": initial_contracts,
        "actual_reduced_contracts": 0.0,
        "cumulative_reduced_ratio": 0.0,
        "reduce_stage": 0,
        "last_processed_closed_5m_candle_ts": None,
        "last_processed_closed_1h_candle_ts": None,
        "last_reduction_order_time": None,
        "last_reduction_filled_contracts": None,
        "stage1_1h_macd": None,
        "last_negative_guard_review_bar": None,
        "last_block_reason": None,
        "last_block_time": None,
        "structure_profit_live_done": False,
        "structure_profit_live_reason": None,
    }


def load_or_init(
    user_dir: str, symbol: str, side: str, entry_price: float, entry_time, current_contracts: float,
) -> dict:
    """이 심볼의 REDUCE v2 상태를 읽어온다. 저장된 상태가 없거나(첫 리뷰) side/entry_price가
    지금 포지션과 다르면("진짜 새 포지션") initial_contracts=current_contracts로 새로
    초기화한다 - 재시작이나 entry_price 표시상의 미세한 오차만으로는 리셋하지 않는다."""
    path = _state_path(user_dir)
    with jsonl_cache.get_path_lock(path):
        data = _load(user_dir)
        sym_state = data.get(symbol)
        if sym_state and sym_state.get('pending_order'):
            return dict(sym_state)
        if sym_state is None or not _is_same_position(sym_state, side, entry_price):
            sym_state = _default_state(side, entry_price, entry_time, current_contracts)
            data[symbol] = sym_state
            _save_atomic(user_dir, data)
        return dict(sym_state)


def get(user_dir: str, symbol: str) -> dict | None:
    data = _load(user_dir)
    sym_state = data.get(symbol)
    return dict(sym_state) if sym_state else None


def clear(user_dir: str, symbol: str) -> None:
    path = _state_path(user_dir)
    with jsonl_cache.get_path_lock(path):
        data = _load(user_dir)
        if symbol in data:
            if data[symbol].get('pending_order'):
                return False
            del data[symbol]
            _save_atomic(user_dir, data)


def record_block(user_dir: str, symbol: str, reason: str) -> None:
    path = _state_path(user_dir)
    with jsonl_cache.get_path_lock(path):
        data = _load(user_dir)
        sym_state = data.get(symbol)
        if sym_state is None:
            return
        sym_state["last_block_reason"] = reason
        sym_state["last_block_time"] = datetime.datetime.now().isoformat(timespec="seconds")
        data[symbol] = sym_state
        _save_atomic(user_dir, data)


def record_stage_executed(
    user_dir: str, symbol: str, stage: int, filled_contracts: float, stage1_1h_macd: float | None = None,
    execution_id: str | None = None, stage_completed: bool = True,
) -> None:
    path = _state_path(user_dir)
    with jsonl_cache.get_path_lock(path):
        data = _load(user_dir)
        sym_state = data.get(symbol)
        if sym_state is None:
            return
        if execution_id and execution_id in sym_state.get('executed_ids', []):
            return
        initial = sym_state.get("initial_contracts") or 0.0
        sym_state["actual_reduced_contracts"] = sym_state.get("actual_reduced_contracts", 0.0) + filled_contracts
        sym_state["cumulative_reduced_ratio"] = (
            sym_state["actual_reduced_contracts"] / initial if initial > 0 else 0.0
        )
        # 시장가 감축도 드물게 부분체결될 수 있다. 요청량보다 적게 체결된 주문을
        # 한 단계 완료로 표시하면 두 단계 누적 감축이 50%에 못 미친 채 종료된다.
        # 부분체결은 실제 수량만 누적하고 단계는 유지하여, 다음 새 확정봉에서 해당
        # 단계의 남은 목표량만 다시 검토할 수 있게 한다.
        if stage_completed:
            sym_state["reduce_stage"] = max(sym_state.get("reduce_stage", 0), stage)
            if sym_state.get('adaptive_reduce_target_stage') == stage:
                sym_state['adaptive_reduce_target_contracts'] = None
                sym_state['adaptive_reduce_target_stage'] = None
        sym_state["last_reduction_order_time"] = datetime.datetime.now().isoformat(timespec="seconds")
        sym_state["last_reduction_filled_contracts"] = filled_contracts
        sym_state["last_block_reason"] = None
        sym_state["last_block_time"] = None
        if stage_completed and stage1_1h_macd is not None:
            sym_state["stage1_1h_macd"] = stage1_1h_macd
        if execution_id:
            sym_state.setdefault('executed_ids', []).append(execution_id)
        data[symbol] = sym_state
        _save_atomic(user_dir, data)


def mark_candle_processed(user_dir: str, symbol: str, tf: str, candle_ts: str) -> None:
    """중복 주문 방지(스펙: REDUCE_V2_BLOCKED already_processed_bar) - 이 확정봉을
    이미 이번 스테이지 판단에 소비했다고 표시한다. tf는 "5m" 또는 "1h"만 쓴다."""
    field = "last_processed_closed_5m_candle_ts" if tf == "5m" else "last_processed_closed_1h_candle_ts"
    path = _state_path(user_dir)
    with jsonl_cache.get_path_lock(path):
        data = _load(user_dir)
        sym_state = data.get(symbol)
        if sym_state is None:
            return
        sym_state[field] = candle_ts
        data[symbol] = sym_state
        _save_atomic(user_dir, data)


def position_identity(position: dict) -> str | None:
    """Exchange lifecycle, including creation time (OKX may reuse posId)."""
    pid = position.get('position_id')
    created = position.get('entry_timestamp_ms')
    if not pid or created is None:
        return None
    return f"{pid}:{created}:{position['side']}"


def _valid_initial_contracts(initial_contracts, current_contracts):
    values = (initial_contracts, current_contracts)
    if any(isinstance(value, bool) or not isinstance(value, (int, float))
           or not math.isfinite(value) or value <= 0 for value in values):
        return False
    return initial_contracts >= current_contracts


def initial_contracts_from_open_record(symbol, position, open_record, contract_size):
    """Return a restart baseline only when the live open record proves this position.

    trade_log ``amount`` is coin quantity while OKX ``contracts`` is contract count.
    Exact quantity agreement deliberately excludes positions changed by an ADD,
    reduction, or an external order; those stay fail-closed instead of inventing a
    new reduction ceiling from the remaining quantity.
    """
    if not isinstance(open_record, dict) or not isinstance(position, dict):
        return None
    if (open_record.get('type') != 'open' or open_record.get('symbol') != symbol
            or open_record.get('dry_run') is not False):
        return None
    side = position.get('side')
    if side not in ('long', 'short') or open_record.get('side') != side:
        return None
    values = (
        open_record.get('price'), open_record.get('amount'),
        position.get('entry_price'), position.get('contracts'), contract_size,
    )
    if any(isinstance(value, bool) or not isinstance(value, (int, float))
           or not math.isfinite(value) or value <= 0 for value in values):
        return None
    record_entry, amount, current_entry, current_contracts, size = values
    if abs(record_entry - current_entry) / current_entry > ENTRY_PRICE_TOLERANCE:
        return None
    recovered = amount / size
    if not math.isclose(
            recovered, current_contracts,
            rel_tol=CONTRACT_QUANTITY_TOLERANCE,
            abs_tol=CONTRACT_QUANTITY_TOLERANCE):
        return None
    return float(recovered)


def ensure_position(
    user_dir, symbol, position, initial_contracts=None, entry_time=None,
    open_record=None, contract_size=None,
):
    with jsonl_cache.get_path_lock(_state_path(user_dir)):
        data = _load(user_dir)
        old = data.get(symbol)
        identity = position_identity(position)
        if initial_contracts is None and open_record is not None:
            initial_contracts = initial_contracts_from_open_record(
                symbol, position, open_record, contract_size,
            )
            if initial_contracts is not None and entry_time is None:
                record_time = open_record.get('time')
                if isinstance(record_time, str) and record_time:
                    entry_time = record_time
        if old and old.get('pending_order'):
            if old.get('lifecycle_id') != identity:
                raise ValueError('unresolved reduction belongs to another lifecycle')
            return dict(old)
        if old and identity and old.get('lifecycle_id') == identity:
            if (not old.get('baseline_known')
                    and _valid_initial_contracts(initial_contracts, position.get('contracts'))):
                old['initial_contracts'] = float(initial_contracts)
                old['baseline_known'] = True
                if entry_time is not None:
                    old['entry_time'] = entry_time
                data[symbol] = old
                _save_atomic(user_dir, data)
            return dict(old)
        # Unknown/missing identity must never reset remaining size into a new baseline.
        if old and not identity and old.get('lifecycle_id') is None:
            return dict(old)
        known = bool(identity and _valid_initial_contracts(
            initial_contracts, position.get('contracts'),
        ))
        state = _default_state(position['side'], position['entry_price'], entry_time,
                               float(initial_contracts) if known else None)
        state.update(lifecycle_id=identity, baseline_known=known, pending_order=None,
                     executed_ids=[], last_fast_review_bar=None,
                     last_negative_guard_review_bar=None)
        if old and not old.get('lifecycle_id') and _is_same_position(old, position['side'], position['entry_price']):
            # Carry the old ceiling/stages forward, but require an independently known lifecycle.
            state.update({key: value for key, value in old.items() if key not in ('lifecycle_id', 'baseline_known')})
            legacy_unreduced = (
                old.get('reduce_stage', 0) == 0
                and old.get('actual_reduced_contracts', 0.0) == 0.0
                and old.get('cumulative_reduced_ratio', 0.0) == 0.0
            )
            state['baseline_known'] = bool(known and legacy_unreduced)
            if state['baseline_known']:
                state['initial_contracts'] = float(initial_contracts)
                if entry_time is not None:
                    state['entry_time'] = entry_time
        data[symbol] = state
        _save_atomic(user_dir, data)
        return dict(state)


def update_fields(user_dir, symbol, **fields):
    with jsonl_cache.get_path_lock(_state_path(user_dir)):
        data = _load(user_dir)
        if symbol not in data:
            raise ValueError('missing reduce lifecycle')
        data[symbol].update(fields)
        _save_atomic(user_dir, data)
        return dict(data[symbol])
