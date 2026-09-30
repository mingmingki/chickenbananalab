"""EXECUTION_MODE=SHADOW 전용 가상 포지션 추적(2026-08-31 Phase 1.5A).

실제 포지션(client.fetch_position())과 완전히 분리된 별도의 상태다 - SHADOW 모드는
거래소에 아무것도 쓰지 않으므로 실제 포지션은 절대 생기지 않는다. 이 모듈이 그 대신
"만약 진짜 주문을 냈다면 어떻게 됐을지"를 시뮬레이션해서 기록한다.

영속화 방식은 core_kill_switch.py와 동일한 원칙을 따른다: 계정별 파일에 원자적으로
쓰고, 재시작에도 살아남는다. 심볼당 열린 가상 포지션은 최대 1개다(실제 CORE 정책과
동일 - 같은 방향 포지션 보유 중이면 재진입 안 함)."""
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


def _state_path(user_dir: str) -> str:
    return os.path.join(user_dir, "shadow_open_state.json")


def _log_path(user_dir: str) -> str:
    return os.path.join(user_dir, "shadow_trades.jsonl")


def _default_state() -> dict:
    return {"open_positions": {}, "seen_event_keys": {}}


def _load_state(user_dir: str) -> dict:
    path = _state_path(user_dir)
    if not os.path.exists(path):
        return _default_state()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        # 상태 파일이 깨지면 가상 포지션 상태만 잃는다(실제 자금 영향 없음) - fail-open으로
        # 빈 상태에서 다시 시작해도 안전하다(실거래 kill switch와는 성격이 다름).
        return _default_state()
    default = _default_state()
    for key, val in default.items():
        data.setdefault(key, val)
    return data


def _save_state_atomic(user_dir: str, state: dict) -> None:
    os.makedirs(user_dir, exist_ok=True)
    path = _state_path(user_dir)
    fd, tmp_path = tempfile.mkstemp(dir=user_dir, prefix=".shadow_open_state.", suffix=".tmp")
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


def _append_log(user_dir: str, record: dict) -> None:
    os.makedirs(user_dir, exist_ok=True)
    with open(_log_path(user_dir), "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def get_open_position(user_dir: str, symbol: str) -> dict | None:
    state = _load_state(user_dir)
    return state["open_positions"].get(symbol)


def simulate_entry(
    cfg, symbol: str, side: str, event_key: str,
    candidate_time: str, raw_signal_price: float, simulated_fill_price: float,
    amount_coin: float, contracts: float, contract_size: float, notional: float,
    margin: float, leverage: float, sl_price: float, tp_price: float,
    short_level_ctx: dict | None, gemini_decision: dict, gpt_result: dict | None,
    fee_assumption: float, slippage_assumption: float, version_fields: dict,
) -> dict:
    """가상 진입을 기록한다. event_key가 이미 처리된 적 있으면(동일 사이클의 중복
    호출 등) 새로 만들지 않고 기존 기록을 그대로 반환한다 - idempotent."""
    with _lock_for(cfg.user_dir):
        state = _load_state(cfg.user_dir)
        existing_trade_id = state["seen_event_keys"].get(event_key)
        if existing_trade_id is not None:
            return {"shadow_trade_id": existing_trade_id, "duplicate": True, **state["open_positions"].get(symbol, {})}

        if symbol in state["open_positions"]:
            # 이미 같은 심볼에 가상 포지션이 있음 - 실제 CORE도 같은 방향 포지션
            # 보유 중이면 재진입하지 않으므로, 여기서도 새 진입을 만들지 않는다.
            return {"shadow_trade_id": None, "duplicate": False, "skipped_reason": "already_open"}

        shadow_trade_id = f"shadow-{symbol.replace('/', '_').replace(':', '_')}-{datetime.datetime.now().strftime('%Y%m%d%H%M%S%f')}"
        record = {
            "type": "shadow_open",
            "shadow_trade_id": shadow_trade_id,
            "symbol": symbol,
            "side": side,
            "event_key": event_key,
            "candidate_time": candidate_time,
            "entry_time": datetime.datetime.now().isoformat(timespec="seconds"),
            "raw_signal_price": raw_signal_price,
            "simulated_fill_price": simulated_fill_price,
            "amount_coin": amount_coin,
            "contracts": contracts,
            "contract_size": contract_size,
            "notional": notional,
            "margin": margin,
            "leverage": leverage,
            "sl_price": sl_price,
            "tp_price": tp_price,
            "short_level": (short_level_ctx or {}).get("level"),
            "short_level_reasons": (short_level_ctx or {}).get("reasons"),
            "gemini_action": gemini_decision.get("action"),
            "gemini_confidence": gemini_decision.get("confidence"),
            "gemini_regime": gemini_decision.get("market_regime"),
            "gpt_decision": (gpt_result or {}).get("decision"),
            "gpt_confidence": (gpt_result or {}).get("confidence"),
            "fee_assumption": fee_assumption,
            "slippage_assumption": slippage_assumption,
            # SL/TP watcher용 - 아직 처리한 1분봉이 없음. AI_LIVE_CLOSE=false 기본값에서는
            # 실제 거래도 exchange-side SL/TP로만 닫히므로(run_cycle의 "외부청산 감지"),
            # 가상 포지션도 동일한 역할을 하는 이 워처가 없으면 절대 닫히지 않는다.
            "sl_tp_last_processed_ms": None,
            "sl_tp_data_gap_seen": False,
            **version_fields,
        }
        _append_log(cfg.user_dir, record)
        state["open_positions"][symbol] = record
        state["seen_event_keys"][event_key] = shadow_trade_id
        _save_state_atomic(cfg.user_dir, state)
        return {**record, "duplicate": False}


def to_position_dict(record: dict, mark_price: float) -> dict:
    """client.fetch_position()과 동일한 필드 shape으로 변환한다 - run_cycle/gemini_analyzer가
    실제 포지션과 가상 포지션을 구분 없이 다룰 수 있게 하기 위함(파이프라인 동일성)."""
    entry_price = record["simulated_fill_price"]
    side = record["side"]
    contracts = record["contracts"]
    amount_coin = record["amount_coin"]
    if side == "long":
        unrealized_pnl = (mark_price - entry_price) * amount_coin
    else:
        unrealized_pnl = (entry_price - mark_price) * amount_coin
    notional = record.get("notional") or (entry_price * amount_coin)
    pnl_pct = (unrealized_pnl / notional * 100) if notional else None
    return {
        "side": side,
        "contracts": contracts,
        "entry_price": entry_price,
        "mark_price": mark_price,
        "unrealized_pnl": unrealized_pnl,
        "pnl_pct": pnl_pct,
        "leverage": record.get("leverage"),
    }


def _candle_hits(side: str, sl_price: float, tp_price: float, low: float, high: float) -> tuple[bool, bool]:
    if side == "long":
        return (low <= sl_price), (high >= tp_price)
    return (high >= sl_price), (low <= tp_price)


def advance_sl_tp_watcher(cfg, symbol: str, candles_1m: list) -> str | None:
    """열린 가상 포지션의 SL/TP를 아직 안 본 1분봉으로 이어서 확인한다(entry_veto_outcome.py의
    "동일 캔들 충돌은 보수적으로/낙관적 판정 금지, 연속성 끊기면 추측하지 않고 gap만 표시"
    원칙을 그대로 따른다 - 다만 이건 24시간 horizon이 있는 후보 관찰이 아니라 무기한 보유
    중인 포지션이라 entry_veto_outcome.advance_candidate를 그대로 재사용하지 않고, 같은
    원칙만 최소하게 다시 구현한다).

    candles_1m: [[ts_ms, o, h, l, c, v], ...] - 오름차순, 이미 처리된 구간이 섞여 있어도
    안전하다.
    반환값: "stop_loss" | "take_profit" | None(아직 안 걸렸거나 gap이라 판단을 보류함).
    None을 반환한 경우에도 gap을 만났으면 sl_tp_data_gap_seen이 기록되어(경고 로그/최종
    보고에 노출) 조용히 넘어가지 않는다."""
    if not candles_1m:
        return None
    with _lock_for(cfg.user_dir):
        state = _load_state(cfg.user_dir)
        record = state["open_positions"].get(symbol)
        if record is None:
            return None

        candles = sorted(candles_1m, key=lambda c: c[0])
        last_processed = record.get("sl_tp_last_processed_ms")
        usable = [c for c in candles if last_processed is None or c[0] > last_processed]
        if not usable:
            return None

        if last_processed is not None:
            expected_next = last_processed + 60_000
            if usable[0][0] > expected_next:
                record["sl_tp_data_gap_seen"] = True
                state["open_positions"][symbol] = record
                _save_state_atomic(cfg.user_dir, state)
                return None

        sl_price, tp_price, side = record["sl_price"], record["tp_price"], record["side"]
        outcome = None
        for ts_ms, o, h, l, c, v in usable:
            hit_sl, hit_tp = _candle_hits(side, sl_price, tp_price, l, h)
            record["sl_tp_last_processed_ms"] = ts_ms
            if hit_sl:
                # 동시에 hit_tp여도 보수적으로 SL을 먼저 찍은 것으로 처리(낙관적 판정 금지).
                outcome = "stop_loss"
                break
            if hit_tp:
                outcome = "take_profit"
                break

        state["open_positions"][symbol] = record
        _save_state_atomic(cfg.user_dir, state)
        return outcome


def simulate_close(
    cfg, symbol: str, event_key: str, exit_reason: str,
    simulated_exit_price: float, fee_assumption: float, version_fields: dict,
) -> dict | None:
    """가상 청산을 기록한다. 열린 가상 포지션이 없으면 None(할 게 없음)."""
    with _lock_for(cfg.user_dir):
        state = _load_state(cfg.user_dir)
        existing_trade_id = state["seen_event_keys"].get(event_key)
        if existing_trade_id is not None:
            return {"shadow_trade_id": existing_trade_id, "duplicate": True}

        open_record = state["open_positions"].get(symbol)
        if open_record is None:
            return None

        entry_price = open_record["simulated_fill_price"]
        side = open_record["side"]
        contracts = open_record["contracts"]
        amount_coin = open_record["amount_coin"]
        if side == "long":
            gross_pnl = (simulated_exit_price - entry_price) * amount_coin
        else:
            gross_pnl = (entry_price - simulated_exit_price) * amount_coin
        net_pnl = gross_pnl - fee_assumption

        record = {
            "type": "shadow_close",
            "shadow_trade_id": open_record["shadow_trade_id"],
            "symbol": symbol,
            "side": side,
            "event_key": event_key,
            "exit_time": datetime.datetime.now().isoformat(timespec="seconds"),
            "exit_reason": exit_reason,
            "entry_price": entry_price,
            "simulated_exit_price": simulated_exit_price,
            "contracts": contracts,
            "amount_coin": amount_coin,
            "gross_pnl": gross_pnl,
            "fee_assumption": fee_assumption,
            "net_pnl": net_pnl,
            **version_fields,
        }
        _append_log(cfg.user_dir, record)
        del state["open_positions"][symbol]
        state["seen_event_keys"][event_key] = open_record["shadow_trade_id"]
        _save_state_atomic(cfg.user_dir, state)
        return record


def recent(user_dir: str, limit: int = 200) -> list:
    path = _log_path(user_dir)
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return list(reversed(rows))[:limit]
