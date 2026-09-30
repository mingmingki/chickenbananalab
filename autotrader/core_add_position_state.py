"""ADD_POSITION(2026-09-15, 사용자 직접 지시) - CORE 보유 포지션 AI 관리에 "손실 중이지만
추세는 여전히 유효하다"는 판단이 나오면 평단가를 개선하기 위해 딱 한 번만 추가 진입하는
기능의 포지션별 영속 상태.

reduce_v2_state.py는 반복 REDUCE_50 사고에 특화된 파일이라 여기 얹지 않고, core_kill_switch.py/
core_manual_close.py와 동일한 패턴(원자적 tempfile+os.replace, jsonl_cache.get_path_lock으로
read-modify-write 직렬화)의 독립 파일로 둔다. 포지션 식별은 reduce_v2_state.position_identity()를
그대로 재사용한다 - "같은 포지션"의 기준을 두 파일에서 따로 정의하지 않는다.

`used`는 체결이 실제로 확인된 뒤에만 세팅한다(주문만 내고 프로세스가 죽으면, 재조정 루프가
나중에 fetch_position()으로 실제 체결을 확인한 뒤에야 세팅) - 낙관적으로 먼저 세팅하지 않는다.
`pending_order`는 주문 제출 "전"에 내구성 있게 먼저 기록한다(크래시 시 재조정 루프가 이어받을
수 있도록) - REDUCE v2 및 _restore_core_reduce_protection의 "일단 기록 후 제출" 관례와 동일."""
import os

import jsonl_cache
import process_lock
import reduce_v2_state

position_identity = reduce_v2_state.position_identity  # 포지션 식별 기준은 하나만 둔다


def _state_path(user_dir: str) -> str:
    return os.path.join(user_dir, "core_add_position_state.json")


def _load(user_dir: str) -> dict:
    return process_lock.load_json_or_default(
        _state_path(user_dir), dict, corrupted_error_prefix='ADD_POSITION lifecycle state',
    )


def _save_atomic(user_dir: str, data: dict) -> None:
    process_lock.save_json_atomic(_state_path(user_dir), data)


def _default_state(lifecycle_id: str | None) -> dict:
    return {
        "lifecycle_id": lifecycle_id,
        "used": False,
        "pending_order": None,
        "executed_at": None,
        "execution_id": None,
        "last_block_reason": None,
        "last_block_time": None,
    }


def ensure_position(user_dir: str, symbol: str, position: dict) -> dict:
    """이 심볼의 ADD_POSITION 상태를 읽어온다. 저장된 상태가 없거나 lifecycle_id가
    지금 포지션과 다르면("진짜 새 포지션") used=False로 새로 초기화한다. identity를
    알 수 없는 상태(구버전 기록 등)는 reduce_v2_state.ensure_position()과 동일한
    보수적 원칙 - 모르면 절대 자동으로 다시 허용 상태로 되돌리지 않는다(이미 pending
    이거나 이미 lifecycle_id가 있었는데 지금 identity를 못 구하면 기존 그대로 유지)."""
    with jsonl_cache.get_path_lock(_state_path(user_dir)):
        data = _load(user_dir)
        old = data.get(symbol)
        identity = position_identity(position)
        if old and old.get('pending_order'):
            if identity and old.get('lifecycle_id') != identity:
                raise ValueError('unresolved add-position order belongs to another lifecycle')
            return dict(old)
        if old and identity and old.get('lifecycle_id') == identity:
            return dict(old)
        if old and not identity:
            return dict(old)  # identity 확인 불가 - 기존 상태(허용 여부 포함) 그대로 유지
        state = _default_state(identity)
        data[symbol] = state
        _save_atomic(user_dir, data)
        return dict(state)


def get(user_dir: str, symbol: str) -> dict | None:
    data = _load(user_dir)
    sym_state = data.get(symbol)
    return dict(sym_state) if sym_state else None


def clear(user_dir: str, symbol: str) -> None:
    """포지션이 완전히 청산됐을 때 호출(reduce_v2_state.clear()와 항상 같이 호출됨) -
    다음에 진짜 새 포지션이 열리면 ensure_position()이 어차피 새로 초기화하지만,
    상태 파일이 무한정 쌓이지 않도록 명시적으로 지운다. pending_order가 남아있으면
    (미해결 주문이 있으면) 지우지 않는다 - reduce_v2_state.clear()와 동일한 안전장치."""
    with jsonl_cache.get_path_lock(_state_path(user_dir)):
        data = _load(user_dir)
        if symbol in data:
            if data[symbol].get('pending_order'):
                return
            del data[symbol]
            _save_atomic(user_dir, data)


def record_pending(user_dir: str, symbol: str, pending: dict) -> None:
    """주문 제출 '전'에 먼저 기록한다(내구성 우선) - 이미 pending이 있으면 덮어쓰지
    않는다(같은 시도의 재개만 허용, 새 시도로 착각해 덮어쓰지 않음)."""
    with jsonl_cache.get_path_lock(_state_path(user_dir)):
        data = _load(user_dir)
        sym_state = data.get(symbol)
        if sym_state is None:
            raise ValueError('missing add-position lifecycle')
        if sym_state.get('pending_order') and sym_state['pending_order'].get('client_order_id') != pending.get('client_order_id'):
            raise ValueError('a different add-position order is already pending')
        sym_state['pending_order'] = pending
        data[symbol] = sym_state
        _save_atomic(user_dir, data)


def update_pending_fields(user_dir: str, symbol: str, **fields) -> dict:
    with jsonl_cache.get_path_lock(_state_path(user_dir)):
        data = _load(user_dir)
        sym_state = data.get(symbol)
        if sym_state is None or not sym_state.get('pending_order'):
            raise ValueError('no pending add-position order to update')
        sym_state['pending_order'].update(fields)
        data[symbol] = sym_state
        _save_atomic(user_dir, data)
        return dict(sym_state)


def record_executed(user_dir: str, symbol: str, execution_id: str, executed_at: str) -> None:
    """체결이 실제로 확인된 뒤에만 호출 - used=True로 확정하고 pending_order를 지운다.
    같은 execution_id로 다시 호출돼도(재조정 루프의 재시도 등) 안전하게 무시한다."""
    with jsonl_cache.get_path_lock(_state_path(user_dir)):
        data = _load(user_dir)
        sym_state = data.get(symbol)
        if sym_state is None:
            return
        if sym_state.get('used') and sym_state.get('execution_id') == execution_id:
            return
        sym_state['used'] = True
        sym_state['execution_id'] = execution_id
        sym_state['executed_at'] = executed_at
        sym_state['last_block_reason'] = None
        sym_state['last_block_time'] = None
        data[symbol] = sym_state
        _save_atomic(user_dir, data)


def clear_pending(user_dir: str, symbol: str) -> None:
    """pending_order만 지운다(used는 절대 안 건드림) - 두 가지 경우에 쓴다: (1) 주문이
    최종적으로 취소/실패로 확정됐을 때(체결 자체가 안 됨 - used는 여전히 False로 남아
    다음 기회에 다시 시도 가능), (2) 체결+보호주문 재부착+검증까지 전부 성공적으로
    끝났을 때(used는 이미 True - 이제 더 이상 재조정이 필요 없다는 뜻으로 지운다).
    record_executed()는 일부러 pending_order를 안 건드린다 - 체결은 확인됐어도
    보호주문 재부착이 아직 안 끝났을 수 있어서(그 사이 크래시하면 재조정 루프가
    pending_order를 보고 이어받아야 한다), fill 확인과 pending 해제는 서로 다른
    시점이다."""
    with jsonl_cache.get_path_lock(_state_path(user_dir)):
        data = _load(user_dir)
        sym_state = data.get(symbol)
        if sym_state is None:
            return
        sym_state['pending_order'] = None
        data[symbol] = sym_state
        _save_atomic(user_dir, data)


def record_block(user_dir: str, symbol: str, reason: str) -> None:
    import datetime
    with jsonl_cache.get_path_lock(_state_path(user_dir)):
        data = _load(user_dir)
        sym_state = data.get(symbol)
        if sym_state is None:
            return
        sym_state['last_block_reason'] = reason
        sym_state['last_block_time'] = datetime.datetime.now().isoformat(timespec="seconds")
        data[symbol] = sym_state
        _save_atomic(user_dir, data)
