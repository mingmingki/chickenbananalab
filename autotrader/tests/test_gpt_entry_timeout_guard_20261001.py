import logging
from types import SimpleNamespace

import symbol_entry_control
import trader

SYMBOL = "BTC/USDT:USDT"

class _Client:
    def __init__(self, last_price):
        self.last_price = last_price
    def fetch_usdt_equity(self):
        return 1000.0
    def fetch_last_price(self):
        return self.last_price

class _Guard:
    def __init__(self, allowed=True):
        self.allowed = allowed
    def allow_new_entry(self, equity):
        return self.allowed

def _cfg(tmp_path):
    return SimpleNamespace(
        user_dir=str(tmp_path),
        logger=logging.getLogger("test.gpt_entry_timeout_guard"),
        _core_loss_guards={SYMBOL: _Guard(True)},
    )

def test_entry_gate_budget_is_25_seconds_without_sdk_retries():
    assert trader.GPT_ENTRY_TIMEOUT_SECONDS == 25.0
    assert trader.GPT_ENTRY_MAX_RETRIES == 0

def test_flat_gpt_approval_is_blocked_when_price_drifted_over_existing_point_two_pct_guard(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    client = _Client(100.30)
    entries = []
    monkeypatch.setattr(symbol_entry_control, "is_paused", lambda *a: False)
    monkeypatch.setattr(trader.core_kill_switch, "is_active", lambda *a: False)
    monkeypatch.setattr(trader, "_execute_entry", lambda *a, **k: entries.append(True) or True)
    ok = trader._execute_approved_entry_with_optional_reversal(
        cfg, SimpleNamespace(), client, SYMBOL, "long", 1.0,
        100.0, 98.0, 104.0, reversal_position=None,
        revalidate_after_gpt=True,
    )
    assert ok is False
    assert entries == []

def test_flat_gpt_approval_executes_when_revalidation_is_still_fresh(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    client = _Client(100.10)
    entries = []
    monkeypatch.setattr(symbol_entry_control, "is_paused", lambda *a: False)
    monkeypatch.setattr(trader.core_kill_switch, "is_active", lambda *a: False)
    monkeypatch.setattr(trader, "_execute_entry", lambda *a, **k: entries.append(True) or True)
    ok = trader._execute_approved_entry_with_optional_reversal(
        cfg, SimpleNamespace(), client, SYMBOL, "long", 1.0,
        100.0, 98.0, 104.0, reversal_position=None,
        revalidate_after_gpt=True,
    )
    assert ok is True
    assert entries == [True]

def test_gate_disabled_flat_entry_keeps_existing_no_extra_revalidation_path(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    client = _Client(101.0)
    entries = []
    monkeypatch.setattr(trader, "_execute_entry", lambda *a, **k: entries.append(True) or True)
    ok = trader._execute_approved_entry_with_optional_reversal(
        cfg, SimpleNamespace(), client, SYMBOL, "long", 1.0,
        100.0, 98.0, 104.0, reversal_position=None,
    )
    assert ok is True
    assert entries == [True]

def test_gpt_enabled_order_path_wires_post_approval_revalidation():
    source = open(trader.__file__, encoding="utf-8").read()
    approved_marker = 'logger.info("[%s] GPT 승인 - %s 주문 실행", symbol, action)'
    start = source.index(approved_marker)
    tail = source[start:start + 1800]
    assert "revalidate_after_gpt=True" in tail
