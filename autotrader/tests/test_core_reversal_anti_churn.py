import datetime
import logging
from types import SimpleNamespace

from state import TraderState
import trader
import symbol_entry_control


SYMBOL = "XRP/USDT:USDT"


def _position(side="long"):
    return {
        "side": side,
        "contracts": 7.2,
        "entry_price": 1.52,
        "entry_timestamp_ms": 1790044801614,
        "position_id": "p-1",
        "mark_price": 1.51,
        "unrealized_pnl": -1.0,
    }


def _cfg(tmp_path, guard=None):
    guard = guard or SimpleNamespace(allow_new_entry=lambda equity: True)
    return SimpleNamespace(
        user_dir=str(tmp_path),
        logger=logging.getLogger("test.reversal"),
        GPT_ENTRY_GATE_ENABLED=True,
        OPENAI_API_KEY="test-key",
        REENTRY_COOLDOWN_MINUTES=15,
        _core_loss_guards={SYMBOL: guard},
    )


class _Client:
    def __init__(self, position):
        self.position = position

    def fetch_position(self):
        return self.position

    def fetch_last_price(self):
        return 1.50

    def fetch_usdt_equity(self):
        return 1000.0


def test_gpt_wait_does_not_flatten_existing_position(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    position = _position("long")
    client = _Client(position)
    closes = []

    monkeypatch.setattr(symbol_entry_control, "is_paused", lambda *a: False)
    monkeypatch.setattr(trader.core_kill_switch, "is_active", lambda *a: False)
    monkeypatch.setattr(trader, "_reentry_blocked", lambda *a, **k: (False, 0.0))
    monkeypatch.setattr(
        trader, "_gpt_entry_gate",
        lambda *a, **k: (False, "blocked_wait", {"decision": "wait", "confidence": .8}),
    )
    monkeypatch.setattr(trader, "_record_entry_gate_result", lambda *a, **k: None)
    monkeypatch.setattr(trader, "_record_veto_shadow_gate_outcome", lambda *a, **k: None)
    monkeypatch.setattr(trader, "_execute_close", lambda *a, **k: closes.append(True) or True)

    trader._handle_new_entry(
        cfg, TraderState(), client, SYMBOL, "short",
        {"action": "short", "confidence": .8}, "d1", "reversal_entry",
        ["5m"], "summary", position, 1.50, 100.0, 1.53, 1.44,
        reversal_position=position,
    )

    assert closes == []
    assert client.position is position
def test_approved_reversal_closes_then_enters_under_same_commit(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    position = _position("long")
    client = _Client(position)
    calls = []

    def fake_close(*args, **kwargs):
        calls.append("close")
        client.position = None
        return True

    def fake_entry(*args, **kwargs):
        calls.append("entry")
        return True

    monkeypatch.setattr(trader, "_execute_close", fake_close)
    monkeypatch.setattr(trader, "_execute_entry", fake_entry)

    ok = trader._execute_approved_entry_with_optional_reversal(
        cfg, TraderState(), client, SYMBOL, "short", 100.0,
        1.50, 1.53, 1.44, reversal_position=position,
        decision_id="d2", decision={"action": "short"},
    )

    assert ok is True
    assert calls == ["close", "entry"]


def test_reversal_stale_approved_price_keeps_existing_position(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    position = _position("long")
    client = _Client(position)
    client.fetch_last_price = lambda: 1.49
    closes = []
    monkeypatch.setattr(trader, "_execute_close", lambda *a, **k: closes.append(True) or True)

    ok = trader._execute_approved_entry_with_optional_reversal(
        cfg, TraderState(), client, SYMBOL, "short", 100.0,
        1.50, 1.53, 1.44, reversal_position=position,
    )

    assert ok is False
    assert closes == []
    assert client.position is position
def test_position_ai_close_sets_direction_agnostic_reentry_lock(tmp_path):
    cfg = _cfg(tmp_path)
    state = SimpleNamespace()
    # The recovery path is exercised from durable trade history in broader tests;
    # here verify the blocker semantics used by the runtime state.
    class _State:
        def __init__(self):
            self.data = {
                "symbols": {
                    SYMBOL: {
                        "reentry_block_until": datetime.datetime.now() + datetime.timedelta(minutes=10),
                        "reentry_block_side": None,
                    }
                }
            }
        def snapshot(self):
            return self.data
        def update_symbol(self, symbol, **kwargs):
            self.data["symbols"].setdefault(symbol, {}).update(kwargs)

    s = _State()
    assert trader._reentry_blocked(s, SYMBOL, cfg, "long")[0] is True
    assert trader._reentry_blocked(s, SYMBOL, cfg, "short")[0] is True

def test_post_close_daily_loss_recheck_stays_flat_without_entry(tmp_path, monkeypatch):
    class Guard:
        def __init__(self):
            self.calls = 0
        def allow_new_entry(self, equity):
            self.calls += 1
            return self.calls == 1

    guard = Guard()
    cfg = _cfg(tmp_path, guard=guard)
    position = _position("long")
    client = _Client(position)
    calls = []

    monkeypatch.setattr(symbol_entry_control, "is_paused", lambda *a: False)
    monkeypatch.setattr(trader.core_kill_switch, "is_active", lambda *a: False)

    def fake_close(*args, **kwargs):
        calls.append("close")
        client.position = None
        return True

    def fake_entry(*args, **kwargs):
        calls.append("entry")
        return True

    monkeypatch.setattr(trader, "_execute_close", fake_close)
    monkeypatch.setattr(trader, "_execute_entry", fake_entry)

    ok = trader._execute_approved_entry_with_optional_reversal(
        cfg, TraderState(), client, SYMBOL, "short", 100.0,
        1.50, 1.53, 1.44, reversal_position=position,
    )

    assert ok is False
    assert calls == ["close"]
    assert client.position is None
    assert guard.calls == 2
