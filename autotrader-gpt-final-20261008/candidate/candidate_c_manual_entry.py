"""Durable operator-initiated Candidate C entry; engine-owned execution only.

The HTTP request never places orders. One request is consumed by the live symbol
loop, through the same ledger/epoch/protection machinery as strategy entries.
An uncertain outcome is not retried or assumed to be flat.
"""
from __future__ import annotations
import json
import os
import time
import uuid
import math

import jsonl_cache
import process_lock


def _path(user_dir):
    return os.path.join(user_dir, "candidate_c_manual_entry.json")


def _load(user_dir):
    try:
        with open(_path(user_dir), encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    if not isinstance(data, dict):
        raise ValueError("manual entry state invalid")
    return data


def get(user_dir, symbol):
    with jsonl_cache.get_path_lock(_path(user_dir)):
        row = _load(user_dir).get(symbol)
        return dict(row) if isinstance(row, dict) else None


def reserve(user_dir, symbol, side, *, now=None):
    if side not in ("long", "short"):
        raise ValueError("invalid side")
    when = time.time() if now is None else float(now)
    path = _path(user_dir)
    with jsonl_cache.get_path_lock(path):
        data = _load(user_dir)
        existing = data.get(symbol)
        if existing and existing.get("status") in ("reserved", "executing", "pending"):
            # Absolutely no second order after a lost HTTP response.
            return dict(existing)
        record = dict(
            request_id="ccme" + uuid.uuid4().hex,
            symbol=symbol, side=side, status="reserved",
            started_at=when, processed_at=None, result_reason=None,
            intent_id=None,
        )
        data[symbol] = record
        process_lock.save_json_atomic(path, data)
        return record


def patch(user_dir, symbol, request_id, **changes):
    path = _path(user_dir)
    with jsonl_cache.get_path_lock(path):
        data = _load(user_dir)
        record = data.get(symbol)
        if not isinstance(record, dict) or record.get("request_id") != request_id:
            raise ValueError("manual entry request identity changed")
        updated = dict(record, **changes)
        data[symbol] = updated
        process_lock.save_json_atomic(path, data)
        return updated


def busy(record):
    return bool(record and record.get("status") in ("reserved", "executing", "pending"))


def process_request(cfg, client, symbol, *, state, account_id, config_version_id,
                    config_hash, strategy_policy, stop_event, clients_for_admission_check):
    """Called by the owning Candidate C symbol loop, never from HTTP."""
    import candidate_c_decision_engine as dec
    import candidate_c_hybrid_live_adapter as live
    import candidate_c_hybrid_ownership as ownership
    import candidate_c_manual_close as manual_close
    import candidate_c_hybrid_bars as bars
    import candidate_c_reversal_state_machine as rsm
    import symbol_entry_control

    request = get(cfg.user_dir, symbol)
    if not request or request["status"] != "reserved":
        return None
    rid = request["request_id"]
    now = time.time()
    if now - float(request["started_at"]) > 180:
        patch(cfg.user_dir, symbol, rid, status="failed", result_reason="request_expired")
        return {"executed": False, "reason": "manual_request_expired"}
    if request["side"] not in ("long", "short"):
        patch(cfg.user_dir, symbol, rid, status="failed", result_reason="invalid_side")
        return {"executed": False, "reason": "invalid_side"}

    with ownership.account_order_lock(cfg.user_dir):
        machine = state.reversal_store.get(symbol)
        state.intent_ledger.refresh()
        try:
            stop_requested = stop_event.is_set()
            position = client.fetch_position()
            algos = client.fetch_pending_protection_algo_ids()
            orders = client.exchange.fetch_open_orders(symbol)
            occupied = ownership.count_candidate_c_open_or_pending_positions(
                clients_for_admission_check, state.reversal_store,
                current_symbol=symbol, user_dir=cfg.user_dir)
            can_enter = (
                getattr(cfg, "CANDIDATE_C_LIVE_EXECUTE", False)
                and not stop_requested
                and machine.state == rsm.State.FLAT
                and not symbol_entry_control.is_paused(cfg.user_dir, symbol)
                and manual_close.block_reason(manual_close.get(cfg.user_dir, symbol)) is None
                and not state.intent_ledger.pending_intents()
                and position is None and algos == [] and orders == []
                and occupied < cfg.CANDIDATE_C_MAX_CONCURRENT_POSITIONS
            )
        except Exception:
            can_enter = False
        if not can_enter:
            patch(cfg.user_dir, symbol, rid, status="failed",
                  result_reason="entry_safety_or_capacity_blocked")
            return {"executed": False, "reason": "entry_safety_or_capacity_blocked"}

        # Reserve before any AI call. After a crash, a request marked "executing"
        # requires reconciliation; it must never be replayed automatically.
        machine.request_entry(request["side"])
        state.reversal_store.persist(symbol, int(now * 1000))
        patch(cfg.user_dir, symbol, rid, status="executing", processed_at=now)

    result = None
    try:
        price = float(client.fetch_last_price())
        equity = float(client.fetch_usdt_equity())
        if not math.isfinite(price) or price <= 0 or not math.isfinite(equity) or equity <= 0:
            raise ValueError("invalid_market_or_equity")
        if time.time() - float(request["started_at"]) > 180:
            raise ValueError("request_expired")
        side = request["side"]
        # Candidate C fallback settings; AI SL/TP price review is performed
        # by the existing _execute_entry when enabled.
        stop_pct = float(getattr(cfg, "CANDIDATE_C_STOP_LOSS_PCT", 2.0)) / 100
        target_pct = float(getattr(cfg, "CANDIDATE_C_TAKE_PROFIT_PCT", 4.0)) / 100
        stop = price * (1-stop_pct if side == "long" else 1+stop_pct)
        target = price * (1+target_pct if side == "long" else 1-target_pct)
        if stop <= 0 or target <= 0:
            raise ValueError("invalid_exit_geometry")
        now_ms = int(time.time() * 1000)
        setup = "manual-" + rid
        intent = dec.Intent(
            kind=dec.INTENT_ENTRY, account_id=account_id, symbol=symbol,
            strategy_id="candidate_c", setup_id=setup, position_epoch=None,
            config_version_id=config_version_id, config_hash=config_hash,
            decision_timestamp=now_ms, source_candle_close_timestamp=now_ms,
            side=side, idempotency_key=dec.make_idempotency_key(
                account_id=account_id, symbol=symbol, strategy_id="candidate_c",
                key_id=setup, decision_timestamp=now_ms, config_hash=config_hash),
            reason_code="manual_operator_entry", input_snapshot_hash=dec.sha256_of({
                "request_id":rid, "symbol":symbol, "side":side, "price":price}),
            raw_stop_price=stop, raw_target_price=target,
            requested_risk_pct=cfg.CANDIDATE_C_RISK_PER_TRADE_PCT,
            strategy_policy=strategy_policy, entry_size_fraction=1.0, entry_attempt=True)
        starting_price=price
        def current_and_valid():
            try:
                last=float(client.fetch_last_price())
                return (math.isfinite(last) and last > 0
                        and time.time()-float(request["started_at"]) <= 180
                        and abs(last-starting_price)/starting_price <= .01
                        and (stop < last < target if side=="long" else target < last < stop))
            except Exception:
                return False
        # The same durable ledger/epoch, order identity, protection validation,
        # leverage, daily-loss and max-concurrency guards as strategy entries.
        result = live.execute_intent(
            cfg, client, intent, snapshot={
                "symbol":symbol,"current_price":price,"atr_4h":None,
                "tf_list":["5m","1h","4h"],"position":None,
            }, equity=equity, is_still_valid_fn=current_and_valid,
            open_position_count_fn=lambda: ownership.count_candidate_c_open_or_pending_positions(
                clients_for_admission_check,state.reversal_store,
                current_symbol=symbol,user_dir=cfg.user_dir),
            max_concurrent_positions=cfg.CANDIDATE_C_MAX_CONCURRENT_POSITIONS,
            ledger=state.intent_ledger,epoch_store=state.epoch_store,
            strategy_policy=strategy_policy,stop_event=stop_event,
            manual_operator_entry=True)
    except Exception as exc:
        # Unknown execution state after an exception must stay fail-closed.
        result = {"executed":False,"pending":True,
                  "reason":"manual_entry_execution_exception",
                  "error_type":type(exc).__name__, "error_detail":str(exc)[:180]}
    with ownership.account_order_lock(cfg.user_dir):
        machine=state.reversal_store.get(symbol)
        if result.get("executed"):
            machine.confirm_entry_filled()
            final_status="confirmed"
        elif result.get("pending") or result.get("critical"):
            machine.observe_entry_outcome(accepted=False,unknown=True)
            final_status="pending"
        else:
            machine.observe_entry_outcome(accepted=False,unknown=False)
            final_status="failed"
        state.reversal_store.persist(symbol,int(time.time()*1000))
        patch(cfg.user_dir,symbol,rid,status=final_status,
              result_reason=result.get("reason") or result.get("gate_result"),
              intent_id=result.get("intent_id"))
    return dict(result, manual_entry_request_id=rid)
