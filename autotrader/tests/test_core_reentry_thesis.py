import contextlib
import datetime as dt
import time

import pandas as pd
import pytest

import core_reentry_thesis as crt
import trader
from state import TraderState

SYMBOL = "XRP/USDT:USDT"


def _df(rows):
    return pd.DataFrame(rows)


def _one_h_long():
    return _df([{"timestamp": dt.datetime(2026, 9, 25, 14, 0), "close": 102.0, "ema_20": 101.0, "ema_50": 100.0}])


def _one_h_short():
    return _df([{"timestamp": dt.datetime(2026, 9, 25, 14, 0), "close": 98.0, "ema_20": 99.0, "ema_50": 100.0}])


def _five_m(side="long", count=2):
    rows = []
    for i in range(count):
        if side == "long":
            rows.append({"timestamp": dt.datetime(2026, 9, 25, 14, 5 + i * 5), "close": 101.0 + i, "ema_20": 100.0})
        else:
            rows.append({"timestamp": dt.datetime(2026, 9, 25, 14, 5 + i * 5), "close": 99.0 - i, "ema_20": 100.0})
    return _df(rows)


def test_record_ai_close_persists_and_reload(tmp_path):
    close_time = dt.datetime(2026, 9, 25, 13, 30)
    rec = crt.record_ai_close(str(tmp_path), SYMBOL, "long", close_time, "invalidated")
    loaded = crt.get(str(tmp_path), SYMBOL)
    assert loaded == rec
    assert loaded["closed_side"] == "long"
    assert loaded["assessment"] == "invalidated"
    assert loaded["status"] == "blocked"
    assert loaded["minimum_until"] == "2026-09-25T14:00:00"


def test_same_side_minimum_30_minutes_blocks_even_with_recovery(tmp_path):
    rec = crt.record_ai_close(str(tmp_path), SYMBOL, "long", dt.datetime(2026, 9, 25, 13, 30), "invalidated")
    result = crt.evaluate_same_side(rec, "long", dt.datetime(2026, 9, 25, 13, 50), _one_h_long(), _five_m("long", 2))
    assert result["blocked"] is True
    assert result["reason"] == "minimum_lock"


def test_two_confirmed_5m_bars_required(tmp_path):
    rec = crt.record_ai_close(str(tmp_path), SYMBOL, "long", dt.datetime(2026, 9, 25, 13, 30), "invalidated")
    result = crt.evaluate_same_side(rec, "long", dt.datetime(2026, 9, 25, 14, 1), _one_h_long(), _five_m("long", 1))
    assert result["blocked"] is True
    assert result["one_h_pass"] is True
    assert result["confirmed_5m_recovery_count"] == 1


def test_long_recovery_after_minimum_requires_1h_and_two_5m(tmp_path):
    rec = crt.record_ai_close(str(tmp_path), SYMBOL, "long", dt.datetime(2026, 9, 25, 13, 30), "invalidated")
    result = crt.evaluate_same_side(rec, "long", dt.datetime(2026, 9, 25, 14, 1), _one_h_long(), _five_m("long", 2))
    assert result["blocked"] is False
    assert result["recovered"] is True
    assert result["one_h_pass"] is True
    assert result["confirmed_5m_recovery_count"] == 2


def test_short_recovery_is_exact_mirror(tmp_path):
    rec = crt.record_ai_close(str(tmp_path), SYMBOL, "short", dt.datetime(2026, 9, 25, 13, 30), "weakening")
    result = crt.evaluate_same_side(rec, "short", dt.datetime(2026, 9, 25, 14, 1), _one_h_short(), _five_m("short", 2))
    assert result["blocked"] is False
    assert result["recovered"] is True


def test_missing_recovery_evidence_fails_closed(tmp_path):
    rec = crt.record_ai_close(str(tmp_path), SYMBOL, "long", dt.datetime(2026, 9, 25, 13, 30), "invalidated")
    result = crt.evaluate_same_side(rec, "long", dt.datetime(2026, 9, 25, 14, 1), None, _five_m("long", 2))
    assert result["blocked"] is True
    assert result["reason"] == "recovery_evidence_missing"


def test_clear_recovered_persists_status(tmp_path):
    crt.record_ai_close(str(tmp_path), SYMBOL, "long", dt.datetime(2026, 9, 25, 13, 30), "invalidated")
    rec = crt.clear_recovered(str(tmp_path), SYMBOL, {"one_h_pass": True, "confirmed_5m_recovery_count": 2}, dt.datetime(2026, 9, 25, 14, 2))
    assert rec["status"] == "recovered"
    assert rec["cleared_at"] == "2026-09-25T14:02:00"
    assert crt.get(str(tmp_path), SYMBOL)["status"] == "recovered"


class _Logger:
    def info(self, *args, **kwargs): pass
    def warning(self, *args, **kwargs): pass
    def error(self, *args, **kwargs): pass
    def exception(self, *args, **kwargs): pass


class _Cfg:
    EXECUTION_MODE = "LIVE"
    REENTRY_COOLDOWN_MINUTES = 15
    logger = _Logger()

    def __init__(self, user_dir):
        self.user_dir = str(user_dir)


class _Client:
    def __init__(self, position):
        self.position = dict(position)
        self.closed = False

    def fetch_position(self):
        return None if self.closed else dict(self.position)

    def close_position(self, position, **kwargs):
        self.closed = True
        return {"id": "close-1"}


def _position():
    return {
        "position_id": "p1",
        "entry_timestamp_ms": 123456789,
        "side": "long",
        "contracts": 2.0,
        "entry_price": 100.0,
        "mark_price": 99.0,
        "_approval_started_at": time.time(),
        "_gemini_assessment": "invalidated",
    }


def _patch_close_dependencies(monkeypatch, events):
    monkeypatch.setattr(trader.cc_ownership, "account_order_lock", lambda *_: contextlib.nullcontext())
    monkeypatch.setattr(trader.core_unified_service, "routed_close", lambda *a, **k: None)
    monkeypatch.setattr(trader, "_resolve_external_close_pnl", lambda *a, **k: {
        "gross_pnl": -2.0, "fee": 0.2, "source": "test", "exit_price": 99.0,
        "net_pnl": -2.2, "funding_fee": 0.0,
    })
    monkeypatch.setattr(trader.trade_log, "record_close", lambda *a, **k: events.append("trade_log"))
    monkeypatch.setattr(trader.core_short_downgrade, "record_close", lambda *a, **k: None)
    monkeypatch.setattr(trader.reduce_v2_state, "clear", lambda *a, **k: None)
    monkeypatch.setattr(trader.core_add_position_state, "clear", lambda *a, **k: None)
    monkeypatch.setattr(trader, "_notify_telegram", lambda *a, **k: None)


def test_execute_close_records_ai_thesis_only_after_trade_log(monkeypatch, tmp_path):
    events = []
    _patch_close_dependencies(monkeypatch, events)
    original = crt.record_ai_close

    def record(*args, **kwargs):
        assert events == ["trade_log"]
        events.append("thesis")
        return original(*args, **kwargs)

    monkeypatch.setattr(crt, "record_ai_close", record)
    state = TraderState()
    position = _position()
    ok = trader._execute_close(_Cfg(tmp_path), state, _Client(position), SYMBOL, position, "position_ai_close_all")
    assert ok is True
    assert events == ["trade_log", "thesis"]
    assert crt.get(str(tmp_path), SYMBOL)["assessment"] == "invalidated"


def test_execute_close_non_ai_reason_does_not_create_thesis(monkeypatch, tmp_path):
    events = []
    _patch_close_dependencies(monkeypatch, events)
    state = TraderState()
    position = _position()
    ok = trader._execute_close(_Cfg(tmp_path), state, _Client(position), SYMBOL, position, "signal_close")
    assert ok is True
    assert events == ["trade_log"]
    assert crt.get(str(tmp_path), SYMBOL) is None


def test_thesis_write_failure_does_not_cancel_confirmed_close(monkeypatch, tmp_path):
    events = []
    _patch_close_dependencies(monkeypatch, events)
    monkeypatch.setattr(crt, "record_ai_close", lambda *a, **k: (_ for _ in ()).throw(OSError("disk")))
    state = TraderState()
    position = _position()
    ok = trader._execute_close(_Cfg(tmp_path), state, _Client(position), SYMBOL, position, "position_ai_close_all")
    assert ok is True
    assert events == ["trade_log"]
    symbol_state = state.snapshot()["symbols"][SYMBOL]
    assert symbol_state["reentry_block_until"] is not None


def test_entry_thesis_gate_blocks_same_side_at_20_minutes(tmp_path):
    crt.record_ai_close(str(tmp_path), SYMBOL, "long", dt.datetime(2026, 9, 25, 13, 30), "invalidated")
    state = TraderState()
    result = trader._evaluate_ai_close_thesis_entry_gate(
        _Cfg(tmp_path), state, SYMBOL, "long",
        {"1h": _one_h_long(), "5m": _five_m("long", 2)},
        now=dt.datetime(2026, 9, 25, 13, 50),
    )
    assert result["blocked"] is True
    assert result["reason"] == "minimum_lock"


def test_entry_thesis_gate_opposite_side_not_blocked_after_legacy_window(tmp_path):
    crt.record_ai_close(str(tmp_path), SYMBOL, "long", dt.datetime(2026, 9, 25, 13, 30), "invalidated")
    state = TraderState()
    result = trader._evaluate_ai_close_thesis_entry_gate(
        _Cfg(tmp_path), state, SYMBOL, "short", {},
        now=dt.datetime(2026, 9, 25, 13, 50),
    )
    assert result["blocked"] is False
    assert result["reason"] == "opposite_side"


def test_entry_thesis_gate_after_30m_still_requires_exact_recovery(tmp_path):
    crt.record_ai_close(str(tmp_path), SYMBOL, "long", dt.datetime(2026, 9, 25, 13, 30), "invalidated")
    state = TraderState()
    result = trader._evaluate_ai_close_thesis_entry_gate(
        _Cfg(tmp_path), state, SYMBOL, "long",
        {"1h": _one_h_long(), "5m": _five_m("long", 1)},
        now=dt.datetime(2026, 9, 25, 14, 5),
    )
    assert result["blocked"] is True
    assert result["reason"] == "thesis_not_recovered"


def test_entry_thesis_gate_clears_after_exact_recovery(tmp_path):
    crt.record_ai_close(str(tmp_path), SYMBOL, "long", dt.datetime(2026, 9, 25, 13, 30), "invalidated")
    state = TraderState()
    result = trader._evaluate_ai_close_thesis_entry_gate(
        _Cfg(tmp_path), state, SYMBOL, "long",
        {"1h": _one_h_long(), "5m": _five_m("long", 2)},
        now=dt.datetime(2026, 9, 25, 14, 5),
    )
    assert result["blocked"] is False
    assert crt.get(str(tmp_path), SYMBOL)["status"] == "recovered"
    diag = state.snapshot()["symbols"][SYMBOL]
    assert diag["reentry_thesis_1h_pass"] is True
    assert diag["reentry_thesis_5m_count"] >= 2


def test_handle_new_entry_blocks_thesis_before_gpt(monkeypatch, tmp_path):
    import symbol_entry_control

    class Cfg(_Cfg):
        GPT_ENTRY_GATE_ENABLED = True
        OPENAI_API_KEY = "test"
        MIN_CONFIDENCE = 0.6

    monkeypatch.setattr(symbol_entry_control, "is_paused", lambda *a, **k: False)
    monkeypatch.setattr(trader, "_reentry_blocked", lambda *a, **k: (False, 0.0))
    monkeypatch.setattr(trader, "_evaluate_ai_close_thesis_entry_gate", lambda *a, **k: {
        "blocked": True, "reason": "thesis_not_recovered"
    })
    gpt_calls = []
    monkeypatch.setattr(trader, "_gpt_entry_gate", lambda *a, **k: gpt_calls.append(1) or (True, "approved", {}))
    state = TraderState()
    trader._handle_new_entry(
        Cfg(tmp_path), state, object(), SYMBOL, "long",
        {"confidence": 0.9}, "d1", "entry", ["5m", "1h"], "summary", None,
        100.0, 1.0, 98.0, 104.0, closed_dfs={"1h": _one_h_long(), "5m": _five_m("long", 2)},
    )
    assert gpt_calls == []
    attempt = state.snapshot()["symbols"][SYMBOL]["last_entry_attempt"]
    assert attempt["status"] == "LOCAL_BLOCKED"
    assert attempt["reason"] == "ai_close_thesis_not_recovered"

def test_entry_thesis_gate_diagnostics_are_optional_for_minimal_state(tmp_path):
    class MinimalState:
        pass
    class Cfg:
        user_dir = str(tmp_path)
        logger = type("L", (), {"error": lambda *a, **k: None})()
    result = trader._evaluate_ai_close_thesis_entry_gate(
        Cfg(), MinimalState(), SYMBOL, "long", {},
        now=dt.datetime(2026, 9, 25, 14, 0),
    )
    assert result["blocked"] is False
    assert result["reason"] == "no_record"
