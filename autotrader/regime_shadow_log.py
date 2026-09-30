"""Market Regime Shadow 기록소 - entry_veto_shadow_log.py와 완전히 별도(파일도, lock도,
schema도). Regime history는 "매 cycle 계속 이어지는 관측 로그"라 성격이 다르고
(candidate처럼 open/resolved로 끝나지 않는다), 사용자 지시대로 Phase 1에서는
entry_veto_shadow_log.py/entry_veto_outcome.py를 건드리지 않는다.

두 파일:
- regime_shadow_log.jsonl: append-only, 매 cycle·매 심볼 관측 1줄(감사/Phase 4 분석용).
- regime_tracker_state.json: 심볼별 persistence 상태(confirmed_regime/episode_id/
  enter·exit streak)만 담는 작은 bounded state. 원자적 쓰기.

동시성: entry_veto_shadow_log.py와 동일한 이유(BTC/ETH/XRP가 별도 스레드에서 동시에
이 파일들을 건드림)로 user_dir별 RLock을 자체적으로 둔다(그 모듈의 락을 공유하지
않는다 - 완전히 독립적인 파일이므로 독립적인 lock이 맞다)."""
import datetime
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


def _log_path(user_dir: str) -> str:
    return os.path.join(user_dir, "regime_shadow_log.jsonl")


def _state_path(user_dir: str) -> str:
    return os.path.join(user_dir, "regime_tracker_state.json")


def _load_state(user_dir: str) -> dict:
    path = _state_path(user_dir)
    if not os.path.exists(path):
        return {"symbols": {}}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if "symbols" not in data:
                return {"symbols": {}}
            return data
    except (json.JSONDecodeError, OSError):
        return {"symbols": {}}


def _save_state_atomic(user_dir: str, state: dict) -> None:
    os.makedirs(user_dir, exist_ok=True)
    path = _state_path(user_dir)
    fd, tmp_path = tempfile.mkstemp(dir=user_dir, prefix=".regime_tracker_state.", suffix=".tmp")
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


_DEFAULT_SYMBOL_STATE = {
    "confirmed_regime": "TRANSITION",
    "episode_id": 0,
    "enter_streak": {"TREND": 0, "RANGE": 0},
    "exit_streak": {"TREND": 0, "RANGE": 0},
}

ENTER_STREAK_REQUIRED = 3   # RANGE/TREND 진입 확정에 필요한 연속 CLOSED cycle 수
EXIT_STREAK_REQUIRED = 2    # RANGE/TREND 종료(이탈) 확정에 필요한 연속 CLOSED cycle 수


def get_regime_tracker(user_dir: str, symbol: str) -> dict:
    state = _load_state(user_dir)
    return state.get("symbols", {}).get(symbol, dict(_DEFAULT_SYMBOL_STATE))


def advance_regime_tracker(user_dir: str, symbol: str, raw_regime_closed: str) -> dict:
    """raw_regime_closed(반드시 CLOSED 기준으로 계산된 값)를 받아 confirmed_regime/
    episode_id/streak을 갱신한다. 읽기-수정-쓰기 전체를 하나의 lock 트랜잭션으로 묶는다.

    규칙(사용자 지시):
    - BREAKOUT: persistence 기다리지 않고 즉시 반영(1-cycle). 이미 BREAKOUT이면 같은
      episode 유지, 아니면 새 episode.
    - RANGE/TREND 진입: raw_regime이 ENTER_STREAK_REQUIRED(3)번 연속이어야 confirmed로 전환.
    - RANGE/TREND 유지 중 raw가 잠깐(1 cycle) 벗어나는 건 허용, EXIT_STREAK_REQUIRED(2)번
      연속 벗어나야 종료 -> TRANSITION으로 전환(새 episode).
    - TRANSITION 상태에서는 raw_regime별로 enter_streak를 누적하다 기준 도달 시 확정.
    """
    with _lock_for(user_dir):
        state = _load_state(user_dir)
        symbols = state.setdefault("symbols", {})
        current = dict(_DEFAULT_SYMBOL_STATE, **symbols.get(symbol, {}))
        current["enter_streak"] = dict(_DEFAULT_SYMBOL_STATE["enter_streak"], **current.get("enter_streak", {}))
        current["exit_streak"] = dict(_DEFAULT_SYMBOL_STATE["exit_streak"], **current.get("exit_streak", {}))

        confirmed = current["confirmed_regime"]
        episode_id = current["episode_id"]
        is_new_episode = False

        if raw_regime_closed == "BREAKOUT":
            if confirmed != "BREAKOUT":
                episode_id += 1
                is_new_episode = True
            confirmed = "BREAKOUT"
            current["enter_streak"] = {"TREND": 0, "RANGE": 0}
            current["exit_streak"] = {"TREND": 0, "RANGE": 0}

        elif confirmed in ("TREND", "RANGE"):
            if raw_regime_closed == confirmed:
                current["exit_streak"][confirmed] = 0
            else:
                current["exit_streak"][confirmed] += 1
                if current["exit_streak"][confirmed] >= EXIT_STREAK_REQUIRED:
                    confirmed = "TRANSITION"
                    episode_id += 1
                    is_new_episode = True
                    current["enter_streak"] = {"TREND": 0, "RANGE": 0}
                    current["exit_streak"] = {"TREND": 0, "RANGE": 0}
                    if raw_regime_closed in ("TREND", "RANGE"):
                        current["enter_streak"][raw_regime_closed] = 1

        else:  # confirmed == "TRANSITION"(또는 초기 상태)
            if raw_regime_closed in ("TREND", "RANGE"):
                other = "RANGE" if raw_regime_closed == "TREND" else "TREND"
                current["enter_streak"][raw_regime_closed] += 1
                current["enter_streak"][other] = 0
                if current["enter_streak"][raw_regime_closed] >= ENTER_STREAK_REQUIRED:
                    confirmed = raw_regime_closed
                    episode_id += 1
                    is_new_episode = True
                    current["enter_streak"] = {"TREND": 0, "RANGE": 0}
                    current["exit_streak"] = {"TREND": 0, "RANGE": 0}
            else:
                current["enter_streak"] = {"TREND": 0, "RANGE": 0}

        current["confirmed_regime"] = confirmed
        current["episode_id"] = episode_id
        symbols[symbol] = current
        _save_state_atomic(user_dir, state)

        return {
            "confirmed_regime": confirmed, "episode_id": episode_id, "is_new_episode": is_new_episode,
            "enter_streak": dict(current["enter_streak"]), "exit_streak": dict(current["exit_streak"]),
        }


def record_observation(user_dir: str, symbol: str, classify_result: dict, tracker_result: dict,
                        gemini_regime, gemini_action, position_side) -> None:
    """매 cycle·매 심볼 관측 1줄 append. entry_veto_shadow_log.jsonl과 별도 파일이라
    그쪽의 mtime-cache 우려와 무관하다(이 로그는 애초에 hot-path에서 다시 읽지 않는다)."""
    with _lock_for(user_dir):
        os.makedirs(user_dir, exist_ok=True)
        record = {
            "time": datetime.datetime.now().isoformat(timespec="seconds"),
            "symbol": symbol,
            "threshold_version": classify_result.get("threshold_version"),
            "regime_live": classify_result.get("regime_live"),
            "regime_closed": classify_result.get("regime_closed"),
            "live_closed_regime_divergence": classify_result.get("live_closed_regime_divergence"),
            "scores_live": classify_result.get("scores_live"),
            "scores_closed": classify_result.get("scores_closed"),
            "features_closed": classify_result.get("features_closed"),
            "confirmed_regime": tracker_result.get("confirmed_regime"),
            "episode_id": tracker_result.get("episode_id"),
            "is_new_episode": tracker_result.get("is_new_episode"),
            "enter_streak": tracker_result.get("enter_streak"),
            "exit_streak": tracker_result.get("exit_streak"),
            "gemini_regime": gemini_regime,
            "gemini_action": gemini_action,
            "position_side": position_side,
        }
        with open(_log_path(user_dir), "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


# ---- 분석 전용(Phase 1 24시간 리포트 등에서 필요할 때만 호출, run_cycle에서는 안 씀) ----

def load_all(user_dir: str) -> list:
    path = _log_path(user_dir)
    if not os.path.exists(path):
        return []
    records = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records
