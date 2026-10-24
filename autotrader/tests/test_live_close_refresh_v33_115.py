"""Regressions for exchange-flat display and same-worker close synchronization."""
from pathlib import Path
import json
import logging
import re
import shutil
import subprocess
from types import SimpleNamespace

import ccxt
import pytest

import core_entry_events
import trader
from state import TraderState

ROOT = Path(__file__).resolve().parents[1]
SYMBOL = "BTC/USDT:USDT"


def select_in_shipped_dashboard(info):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required to execute the shipped dashboard selector")
    source = (ROOT / "templates/dashboard.html").read_text()
    expression = re.search(r"const pos = ([^;]+);", source[source.index("async function refreshState"):])
    assert expression
    helper = re.search(r"function corePositionForDisplay\([^)]*\)\s*\{.*?\n\}", source, re.S)
    script = (helper.group(0) if helper else "") + "\n"
    script += "const pick = (live, fallback) => live != null ? live : fallback;\n"
    script += "const info = " + json.dumps(info) + ";\n"
    script += "process.stdout.write(JSON.stringify(" + expression.group(1) + ") ?? 'null');"
    result = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=8)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_confirmed_exchange_flat_does_not_resurrect_cached_position():
    stale = {"side": "short", "contracts": 2.27}
    assert select_in_shipped_dashboard({
        "position": stale, "live_position": None,
        "live_protection": {"status": "NOT_REQUIRED_FLAT"},
    }) is None


def test_unknown_live_observation_preserves_cached_position_fallback():
    position = {"side": "short", "contracts": 2.27}
    assert select_in_shipped_dashboard({
        "position": position, "live_position": None,
        "live_protection": {"status": "UNKNOWN"},
    }) == position


def test_new_live_position_wins_over_older_flat_protection_observation():
    fresh = {"side": "long", "contracts": 1.0}
    assert select_in_shipped_dashboard({
        "position": {"side": "short", "contracts": 2.27},
        "live_position": fresh, "live_protection": {"status": "NOT_REQUIRED_FLAT"},
    }) == fresh


def test_initial_empty_state_has_no_position():
    assert select_in_shipped_dashboard({"position": None, "live_position": None}) is None


@pytest.fixture
def close_context(tmp_path, monkeypatch):
    cfg = SimpleNamespace(
        EXECUTION_MODE="LIVE", user_dir=str(tmp_path),
        logger=logging.getLogger("close-refresh-test"), REENTRY_COOLDOWN_MINUTES=15,
        CORE_NEGATIVE_GUARD_ENABLED=False, CORE_FAST_REDUCE_ENABLED=False,
    )
    position = {
        "side": "short", "contracts": 2.27, "entry_price": 79479.9,
        "position_id": "p1", "entry_timestamp_ms": 1792856266680,
    }
    state = TraderState()
    state.update_symbol(SYMBOL, position=position, live_position=position)
    calls = []

    class Client:
        position = None
        error = None

        def fetch_position(self):
            calls.append("position_read")
            if self.error:
                raise self.error
            return self.position

        def fetch_multi_ohlcv(self, timeframes):
            calls.append("candles")
            return {}

    client = Client()
    for name in ["_reconcile_pending_core_reduce", "_reconcile_pending_core_add"]:
        monkeypatch.setattr(trader, name, lambda *args: None)
    monkeypatch.setattr(trader, "_core_recovery_priority_pending", lambda *args, **kwargs: False)
    monkeypatch.setattr(trader, "_maybe_emergency_close_near_stop", lambda *args: False)
    monkeypatch.setattr(trader, "_core_fast_profit_observation", lambda *args: False)
    monkeypatch.setattr(trader.core_manual_close, "get", lambda *args: None)
    monkeypatch.setattr(core_entry_events, "pending_order", lambda *args: None)
    monkeypatch.setattr(trader.trade_log, "last_unclosed_open", lambda *args: {"sl_price": 79950.0, "tp_price": 78005.0})
    monkeypatch.setattr(trader, "_classify_close_reason", lambda *args: "take_profit")
    monkeypatch.setattr(trader, "_resolve_external_close_pnl", lambda *args, **kwargs: {
        "gross_pnl": 7.0, "fee": 0.1, "net_pnl": 6.9, "funding_fee": 0.0,
        "exit_price": 78005.0, "source": "order_fills",
        "close_execution_evidence": {"order_id": "close1", "filled_contracts": 2.27},
    })
    monkeypatch.setattr(trader, "_notify_web_push_only", lambda *args: calls.append("notify"))
    monkeypatch.setattr(trader.core_short_downgrade, "record_close", lambda *args: None)
    return cfg, state, client, position, calls


def test_fast_tick_records_exchange_close_without_waiting_for_ai_cycle(close_context):
    cfg, state, client, position, calls = close_context
    assert trader._run_core_fast_tick(cfg, state, client, SYMBOL) is True
    records = [json.loads(line) for line in (Path(cfg.user_dir) / "trades_log.jsonl").read_text().splitlines()]
    assert len(records) == 1
    assert records[0]["type"] == "close"
    assert records[0]["okx_net_pnl"] == pytest.approx(6.9)
    assert records[0]["close_execution_evidence"]["order_id"] == "close1"
    assert records[0]["execution_id"]
    row = state.snapshot()["symbols"][SYMBOL]
    assert row["position"] is None and row["live_position"] is None
    assert row["entry_time"] is None
    assert row["reentry_block_side"] == "short"
    assert row["reentry_block_until"] is not None
    assert "candles" not in calls
    assert calls.count("notify") == 1
    trader._run_core_fast_tick(cfg, state, client, SYMBOL)
    assert len((Path(cfg.user_dir) / "trades_log.jsonl").read_text().splitlines()) == 1
    assert calls.count("notify") == 1


def test_failed_exchange_read_cannot_journal_a_close(close_context):
    cfg, state, client, position, _ = close_context
    client.error = ccxt.RequestTimeout("simulated read timeout")
    with pytest.raises(ccxt.RequestTimeout):
        trader._run_core_fast_tick(cfg, state, client, SYMBOL)
    assert state.snapshot()["symbols"][SYMBOL]["position"] == position
    assert not (Path(cfg.user_dir) / "trades_log.jsonl").exists()


def test_failed_journal_keeps_previous_position_for_retry(close_context, monkeypatch):
    cfg, state, client, position, _ = close_context
    def fail(*args, **kwargs):
        raise OSError("simulated journal failure")
    monkeypatch.setattr(trader.trade_log, "record_close", fail)
    with pytest.raises(OSError):
        trader._run_core_fast_tick(cfg, state, client, SYMBOL)
    assert state.snapshot()["symbols"][SYMBOL]["position"] == position


@pytest.mark.parametrize("pending", ["entry", "reduce", "add", "manual"])
def test_pending_settlement_has_priority_over_close_recording(close_context, monkeypatch, pending):
    cfg, state, client, position, _ = close_context
    if pending == "entry":
        monkeypatch.setattr(core_entry_events, "pending_order", lambda *args: {"status": "unknown"})
    elif pending == "manual":
        monkeypatch.setattr(trader.core_manual_close, "get", lambda *args: {
            "status": "pending", "journaled": True, "position": position,
        })
        monkeypatch.setattr(trader.core_manual_close, "complete_if_superseded_by_position", lambda *args: False)
    else:
        monkeypatch.setattr(trader, "_core_recovery_priority_pending", lambda *args, **kwargs: kwargs["phase"] == pending)
    trader._run_core_fast_tick(cfg, state, client, SYMBOL)
    assert state.snapshot()["symbols"][SYMBOL]["position"] == position
    assert not (Path(cfg.user_dir) / "trades_log.jsonl").exists()


def test_remaining_partial_position_is_not_recorded_as_full_close(close_context):
    cfg, state, client, position, _ = close_context
    client.position = dict(position, contracts=0.5)
    trader._run_core_fast_tick(cfg, state, client, SYMBOL)
    assert not (Path(cfg.user_dir) / "trades_log.jsonl").exists()
