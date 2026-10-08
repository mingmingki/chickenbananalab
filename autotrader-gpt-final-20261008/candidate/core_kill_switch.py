"""CORE 전용 global kill switch(2026-08-30, FAST 제거 통합 작업).

FAST는 fast_live_log.py에 kill switch를 갖고 있었지만 CORE는 지금까지 이런
메커니즘이 전혀 없었다 - order_safety.py의 UNKNOWN_ORDER_STATE/보호주문 검증
실패 처리가 "신규 진입을 즉시 전부 동결"하려면 반드시 필요하다(사용자 지시 -
"global kill switch"는 삭제하면 안 되는 공통 안전기능).

FAST와 동일한 설계 원칙을 그대로 따른다: 파일로 영속화해서 재시작에도 살아남고
(안전장치가 재시작 한 번으로 조용히 풀리면 안 됨), operator가 대시보드에서
명시적으로 해제하기 전까지는 자동으로 풀리지 않는다. CORE는 심볼이 여러 개라도
kill switch는 계정 전체 단위로 하나만 둔다(FAST도 XRP/PI 공용으로 하나였던 것과
동일 - 특정 심볼 하나가 위험 신호를 보이면 그 계정 전체의 신규 진입을 멈추는 게
더 안전한 기본값이라는 판단은 FAST 때와 동일하게 유지한다)."""
import json
import os
import tempfile
import threading

_registry_lock = threading.Lock()
_locks: dict[str, threading.RLock] = {}


def _lock_for(user_dir: str) -> threading.RLock:
    with _registry_lock:
        lock = _locks.get(user_dir)
        if lock is None:
            lock = threading.RLock()
            _locks[user_dir] = lock
        return lock


def _state_path(user_dir: str) -> str:
    return os.path.join(user_dir, "core_safety_state.json")


def _default_state() -> dict:
    return {"kill_switch_active": False, "kill_switch_reason": None}


def _load_state(user_dir: str) -> dict:
    path = _state_path(user_dir)
    if not os.path.exists(path):
        return _default_state()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        # kill switch 상태 파일 손상은 fail-closed(켜져 있다고 간주)해야 안전하다 -
        # fail-open으로 "꺼짐"을 반환하면 정작 위험 신호가 있었던 상황에서 신규
        # 진입이 그대로 계속될 수 있다.
        return {"kill_switch_active": True, "kill_switch_reason": "core_safety_state.json 손상 - fail-closed"}
    default = _default_state()
    for key, val in default.items():
        data.setdefault(key, val)
    return data


def _save_state_atomic(user_dir: str, state: dict) -> None:
    os.makedirs(user_dir, exist_ok=True)
    path = _state_path(user_dir)
    fd, tmp_path = tempfile.mkstemp(dir=user_dir, prefix=".core_safety_state.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


def is_active(user_dir: str) -> bool:
    return bool(_load_state(user_dir)["kill_switch_active"])


def get_reason(user_dir: str) -> str | None:
    return _load_state(user_dir)["kill_switch_reason"]


def activate(user_dir: str, reason: str) -> None:
    with _lock_for(user_dir):
        state = _load_state(user_dir)
        state["kill_switch_active"] = True
        state["kill_switch_reason"] = reason
        _save_state_atomic(user_dir, state)


def reset(user_dir: str) -> None:
    with _lock_for(user_dir):
        state = _load_state(user_dir)
        state["kill_switch_active"] = False
        state["kill_switch_reason"] = None
        _save_state_atomic(user_dir, state)
