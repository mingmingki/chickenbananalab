"""LONG 신규진입 Shadow veto 실험의 저장소. 두 부분으로 나눈다:

1. entry_veto_shadow_log.jsonl - append-only 감사 이력(candidate_created/gate_outcome/
   price_outcome_resolved). 분석/리포트 만들 때만 전체를 읽는다.
2. entry_veto_shadow_state.json - "아직 안 끝난(OPEN) candidate"만 담는 bounded 상태.
   매 cycle의 outcome updater는 이 파일만 읽고 쓴다 - JSONL 전체를 매 cycle 재파싱하면
   append할 때마다 mtime이 바뀌어 jsonl_cache가 사실상 매번 무효화되기 때문이다(기존
   메모리 문제를 고려해 이 구조는 피한다). state.json은 원자적으로(임시파일 -> replace)
   쓴다.

동시성: BTC/ETH/XRP가 서로 다른 스레드(trader._symbol_loop, 같은 프로세스 안)에서 이
파일들을 동시에 건드린다. user_dir(계정)별로 하나의 RLock을 두고, "읽기->수정->쓰기"
전체를 그 lock 안에서 한 트랜잭션으로 실행한다 - 그래야 lost update(한 스레드가 방금 쓴
내용을 다른 스레드가 못 보고 읽어서 그대로 덮어쓰는 것)를 막는다. JSONL append도 같은
lock으로 직렬화한다(줄 단위 쓰기가 인터리빙되거나 이벤트가 유실되지 않도록).
"""
import datetime
import json
import os
import tempfile
import threading

import jsonl_cache

_registry_lock = threading.Lock()
_locks: dict[str, threading.RLock] = {}


def _lock_for(user_dir: str) -> threading.RLock:
    """user_dir 하나당 RLock 하나. registry 자체에 대한 동시 최초 접근도
    _registry_lock으로 직렬화해서, 여러 스레드가 동시에 "처음 보는 user_dir"에 접근해도
    전부 같은 lock 객체를 받는다(서로 다른 lock을 하나씩 만들어버리면 lock 자체가
    무의미해진다)."""
    with _registry_lock:
        lock = _locks.get(user_dir)
        if lock is None:
            lock = threading.RLock()
            _locks[user_dir] = lock
        return lock


def _log_path(user_dir: str) -> str:
    return os.path.join(user_dir, "entry_veto_shadow_log.jsonl")


def _state_path(user_dir: str) -> str:
    return os.path.join(user_dir, "entry_veto_shadow_state.json")


def _append_event(user_dir: str, event_type: str, candidate_id: str, payload: dict) -> None:
    """호출부가 이미 _lock_for(user_dir)를 들고 있는 상태(create_candidate/
    update_candidate_outcome/record_gate_outcome)에서 호출된다 - RLock이라 같은
    스레드의 재진입은 안전하다. 독립적으로 호출돼도(테스트 등) 그 자체로 직렬화된다."""
    with _lock_for(user_dir):
        os.makedirs(user_dir, exist_ok=True)
        record = {
            "event": event_type,
            "candidate_id": candidate_id,
            "time": datetime.datetime.now().isoformat(timespec="seconds"),
            **payload,
        }
        with open(_log_path(user_dir), "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _load_state(user_dir: str) -> dict:
    """os.replace()의 원자성 덕분에 쓰기 도중에도 항상 완전한 이전 파일이나 완전한 새
    파일 중 하나만 보인다(깨진 중간 상태를 읽을 일이 없다) - 그래서 순수 읽기 전용
    호출부(get_open_candidates/get_short_run_tracker)는 lock 없이도 안전하다."""
    path = _state_path(user_dir)
    if not os.path.exists(path):
        return {"candidates": {}}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if "candidates" not in data:
                return {"candidates": {}}
            return data
    except (json.JSONDecodeError, OSError):
        return {"candidates": {}}


def _save_state_atomic(user_dir: str, state: dict) -> None:
    """임시파일 이름을 tempfile.mkstemp로 매번 유일하게 만든다 - 고정 이름(.tmp)을 쓰면
    동시에 여러 스레드가 같은 임시파일을 놓고 경쟁하다 os.replace가
    FileNotFoundError로 죽을 수 있다(실제로 재현됨). 이 함수 자체는 항상
    _lock_for(user_dir) 안에서만 호출되므로 사실 그 경합은 lock만으로도 이미 없어지지만,
    임시파일 유일성은 별도의 방어선으로 유지한다."""
    os.makedirs(user_dir, exist_ok=True)
    path = _state_path(user_dir)
    fd, tmp_path = tempfile.mkstemp(dir=user_dir, prefix=".entry_veto_shadow_state.", suffix=".tmp")
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


def create_candidate(user_dir: str, candidate_id: str, symbol: str, entry_snapshot: dict,
                      vetoes: dict, observation_tag: str, outcome_state: dict) -> None:
    """candidate 생성 - 감사 이력(jsonl)에 entry snapshot 전체를 남기고, 가격 추적용
    bounded state에도 추가한다. JSONL append + state 읽기/수정/쓰기 전체를 하나의
    lock 트랜잭션으로 묶는다."""
    with _lock_for(user_dir):
        _append_event(user_dir, "candidate_created", candidate_id, {
            "symbol": symbol,
            "entry_snapshot": entry_snapshot,
            "vetoes": vetoes,
            "observation_tag": observation_tag,
            "outcome_state": outcome_state,
        })
        state = _load_state(user_dir)
        # outcome_state 자체에도 "symbol" 필드가 있을 수 있어(entry_veto_outcome.new_candidate_state가
        # 넣음) **로 뒤에 풀면 인자로 받은 symbol을 조용히 덮어쓸 위험이 있다 - 명시적으로 제거하고
        # 인자로 받은 symbol을 항상 단일 진실 소스로 쓴다.
        outcome_state = {k: v for k, v in outcome_state.items() if k != "symbol"}
        state["candidates"][candidate_id] = {
            "symbol": symbol,
            "vetoes": vetoes,
            "observation_tag": observation_tag,
            **outcome_state,
        }
        _save_state_atomic(user_dir, state)


def record_gate_outcome(user_dir: str, candidate_id: str, pipeline_status: str, **fields) -> None:
    """GPT gate/로컬 게이트/주문 결과 - 감사 이력에만 남긴다(가격 추적 state와는 무관).
    pipeline_status: LOCAL_BLOCKED / GPT_APPROVED / GPT_WAIT / GPT_REJECT / GPT_ERROR /
    ORDER_EXECUTED / ORDER_FAILED 중 하나."""
    with _lock_for(user_dir):
        _append_event(user_dir, "gate_outcome", candidate_id, {
            "pipeline_status": pipeline_status,
            **fields,
        })


def get_open_candidates(user_dir: str, symbol: str | None = None) -> dict:
    state = _load_state(user_dir)
    candidates = state["candidates"]
    if symbol is None:
        return candidates
    return {cid: c for cid, c in candidates.items() if c.get("symbol") == symbol}


def update_candidate_outcome(user_dir: str, candidate_id: str, new_outcome_state: dict) -> None:
    """outcome updater가 advance_candidate()로 갱신한 state를 반영한다. OPEN이 아니게
    되면(TP_FIRST/SL_FIRST/AMBIGUOUS/NEITHER_24H) bounded state에서 제거하고 최종 결과를
    감사 이력에 append한다 - state.json이 무한정 커지지 않는다. "이 candidate가 아직
    state에 있는지 확인 -> 수정 -> 저장(+필요시 append)" 전체를 하나의 lock 트랜잭션으로
    묶는다(중간에 다른 스레드가 같은 state.json을 건드리지 못하게)."""
    with _lock_for(user_dir):
        state = _load_state(user_dir)
        if candidate_id not in state["candidates"]:
            return
        symbol = state["candidates"][candidate_id].get("symbol")
        vetoes = state["candidates"][candidate_id].get("vetoes")
        observation_tag = state["candidates"][candidate_id].get("observation_tag")

        if new_outcome_state.get("outcome_status") == "OPEN":
            clean_state = {k: v for k, v in new_outcome_state.items() if k != "symbol"}
            state["candidates"][candidate_id] = {
                "symbol": symbol, "vetoes": vetoes, "observation_tag": observation_tag,
                **clean_state,
            }
            _save_state_atomic(user_dir, state)
            return

        del state["candidates"][candidate_id]
        _save_state_atomic(user_dir, state)
        _append_event(user_dir, "price_outcome_resolved", candidate_id, {
            "symbol": symbol, "vetoes": vetoes, "observation_tag": observation_tag,
            "outcome_state": new_outcome_state,
        })


def get_short_run_tracker(user_dir: str, symbol: str) -> dict:
    state = _load_state(user_dir)
    tracker = state.get("short_run_tracker", {})
    return tracker.get(symbol, {"active": False, "run_id": 0})


def advance_short_run_tracker(user_dir: str, symbol: str, eligible: bool) -> tuple[bool, int]:
    """eligible_short_shadow(= short_shadow_condition AND position is None)의 false->true
    edge에서만 새 run_id를 발급한다. 호출부가 position 보유 중이면 eligible=False로 넘겨야
    한다 - 그래야 "롱을 청산한 뒤에도 하락 조건이 계속 true였던" 경우, flat이 되는 바로 그
    시점에 다시 false->true로 인식되어 새 SHORT_RUN이 생긴다(포지션 보유 중 내내
    true->true였다는 이유로 영원히 못 만드는 문제 방지). 읽기/수정/쓰기 전체를 하나의
    lock 트랜잭션으로 묶는다."""
    with _lock_for(user_dir):
        state = _load_state(user_dir)
        tracker = state.setdefault("short_run_tracker", {})
        current = tracker.get(symbol, {"active": False, "run_id": 0})
        is_new_run = bool(eligible) and not current["active"]
        new_run_id = current["run_id"] + 1 if is_new_run else current["run_id"]
        tracker[symbol] = {"active": bool(eligible), "run_id": new_run_id}
        _save_state_atomic(user_dir, state)
        return is_new_run, new_run_id


# ---- 분석/리포트 전용(매 cycle이 아니라 필요할 때만 호출) ----
def _load_all_events(user_dir: str) -> list:
    return jsonl_cache.load_jsonl_cached(_log_path(user_dir))


def recent_events(user_dir: str, limit: int = 200) -> list:
    return list(reversed(_load_all_events(user_dir)))[:limit]


def reconstruct_candidates(user_dir: str) -> dict:
    """jsonl 전체를 접어서(candidate_id별로) 현재까지의 최신 상태를 재구성한다.
    분석 스크립트에서만 쓴다 - run_cycle에서는 절대 호출하지 않는다."""
    events = _load_all_events(user_dir)
    result = {}
    for ev in events:
        cid = ev["candidate_id"]
        if ev["event"] == "candidate_created":
            result[cid] = {
                "symbol": ev["symbol"], "entry_snapshot": ev["entry_snapshot"],
                "vetoes": ev["vetoes"], "observation_tag": ev["observation_tag"],
                "outcome_state": ev["outcome_state"], "gate_events": [],
            }
        elif ev["event"] == "gate_outcome" and cid in result:
            result[cid]["gate_events"].append(ev)
        elif ev["event"] == "price_outcome_resolved" and cid in result:
            result[cid]["outcome_state"] = ev["outcome_state"]
    return result
