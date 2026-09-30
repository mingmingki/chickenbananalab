"""CORE SHORT 연속 stop_loss cooldown/downgrade(2026-08-29, 사용자 지시).

같은 symbol의 CORE SHORT에서 실제 stop_loss가 2회 연속 발생하면:
  1) 15분 동안 그 symbol의 신규 SHORT 진입 자체를 완전히 막는다(레벨 무관).
  2) cooldown이 끝난 뒤 "다음 SHORT 1회"는 SHORT_LEVEL을 한 단계 낮춘다(1회만
     소비되고 이후엔 정상 복귀).

BTC/ETH는 완전히 독립적으로 추적한다(symbol을 키로 하는 단일 상태 파일을 공유하되
서로 다른 키만 건드린다). take_profit/signal_close 등 stop_loss가 아닌 사유로
청산되면 연속 카운터가 리셋된다. LONG 청산은 이 추적에 전혀 영향을 주지 않는다.

서버 재시작 시에도 우회되지 않도록(47번 지시류 원칙과 동일) 디스크에 저장한다 -
매 호출마다 새로 읽고 쓰는 순수 함수형 API라 재시작 자체가 상태를 초기화하지 않는다."""
import datetime
import json
import os
import tempfile

import jsonl_cache

COOLDOWN_MINUTES = 15
CONSECUTIVE_SL_THRESHOLD = 2


def _state_path(user_dir: str) -> str:
    return os.path.join(user_dir, "core_short_downgrade_state.json")


def _default_symbol_state() -> dict:
    return {"consecutive_sl": 0, "cooldown_until": None, "downgrade_pending": False}


def _load(user_dir: str) -> dict:
    path = _state_path(user_dir)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _save_atomic(user_dir: str, data: dict) -> None:
    os.makedirs(user_dir, exist_ok=True)
    path = _state_path(user_dir)
    fd, tmp_path = tempfile.mkstemp(dir=user_dir, prefix=".core_short_downgrade.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def record_close(user_dir: str, symbol: str, side: str, close_reason: str | None) -> None:
    """CORE 포지션이 청산될 때마다 호출한다. side!="short"면 아무 것도 하지 않는다
    (LONG 청산은 이 추적과 무관). BTC/ETH 두 심볼 스레드가 동시에 이 파일을 건드릴
    수 있어(각자 다른 키지만 같은 파일) jsonl_cache의 기존 path-lock을 재사용해
    read-modify-write를 직렬화한다(market_structure_log.py와 동일한 재사용 패턴)."""
    if side != "short":
        return
    path = _state_path(user_dir)
    with jsonl_cache.get_path_lock(path):
        state = _load(user_dir)
        sym_state = state.get(symbol, _default_symbol_state())
        # sl_proximity_emergency_close(2026-09-15) - 손절 근접 긴급청산도 개념적으로는
        # "손절당한 것"이지 정상적으로 청산 결정한 게 아니라 실제 stop_loss와 동일하게
        # 취급한다. 문자열 자체는 "stop_loss"를 그대로 재사용하지 않는다 - 대시보드/
        # 거래기록에서 "거래소 SL이 실제로 체결됨"과 "SL 근접으로 봇이 미리 청산함"을
        # 구분할 수 있어야 하기 때문(둘 다 "stop_loss"면 이 기능이 실제로 얼마나
        # 발동했는지 나중에 알 수 없다).
        if close_reason in ("stop_loss", "sl_proximity_emergency_close"):
            sym_state["consecutive_sl"] = sym_state.get("consecutive_sl", 0) + 1
            if sym_state["consecutive_sl"] >= CONSECUTIVE_SL_THRESHOLD:
                cooldown_until = datetime.datetime.now() + datetime.timedelta(minutes=COOLDOWN_MINUTES)
                sym_state["cooldown_until"] = cooldown_until.isoformat()
                sym_state["downgrade_pending"] = True
                sym_state["consecutive_sl"] = 0
        else:
            sym_state["consecutive_sl"] = 0
        state[symbol] = sym_state
        _save_atomic(user_dir, state)


def check(user_dir: str, symbol: str, now: datetime.datetime | None = None) -> dict:
    """반환: {"blocked": bool, "downgrade": bool}. blocked=True면 cooldown이 아직
    안 끝나 이번 사이클 신규 SHORT 진입 자체를 하면 안 된다(레벨 무관). blocked=False
    인데 downgrade=True면 cooldown이 방금 끝나 이번 1회만 SHORT_LEVEL을 한 단계
    낮춰야 한다는 뜻 - 이 호출이 그 1회분을 소비한다(다음 호출은 정상)."""
    now = now or datetime.datetime.now()
    path = _state_path(user_dir)
    with jsonl_cache.get_path_lock(path):
        state = _load(user_dir)
        sym_state = state.get(symbol)
        if sym_state is None:
            return {"blocked": False, "downgrade": False}

        cooldown_until_str = sym_state.get("cooldown_until")
        if not cooldown_until_str:
            return {"blocked": False, "downgrade": False}

        try:
            cooldown_until = datetime.datetime.fromisoformat(cooldown_until_str)
        except ValueError:
            cooldown_until = None

        if cooldown_until is not None and now < cooldown_until:
            return {"blocked": True, "downgrade": False}

        # cooldown이 끝났음(또는 값이 손상돼 더는 신뢰 불가) - downgrade_pending을
        # 여기서 소비하고 상태를 정리한다.
        downgrade = bool(sym_state.get("downgrade_pending"))
        sym_state["cooldown_until"] = None
        sym_state["downgrade_pending"] = False
        state[symbol] = sym_state
        _save_atomic(user_dir, state)
        return {"blocked": False, "downgrade": downgrade}
