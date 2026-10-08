import contextlib
from types import SimpleNamespace

import trader
import symbol_entry_control


class FakeState:
    def __init__(self):
        self.symbols = {"BTC/USDT:USDT": {}}

    def snapshot(self):
        return {"symbols": self.symbols}

    def update_symbol(self, symbol, **kwargs):
        self.symbols.setdefault(symbol, {}).update(kwargs)


class FakeExchange:
    def fetch_open_orders(self, symbol):
        return []


class FakeClient:
    def __init__(self, position=None):
        self._position = position
        self.exchange = FakeExchange()

    def fetch_position(self):
        return self._position

    def fetch_pending_protection_algo_ids(self):
        return []

    def fetch_usdt_equity(self):
        return 1000.0

    def fetch_last_price(self):
        return 100.0


def _cfg():
    return SimpleNamespace(
        EXECUTION_MODE="LIVE",
        user_dir="/tmp/manual-entry-test",
        logger=SimpleNamespace(
            warning=lambda *a, **k: None,
            info=lambda *a, **k: None,
            exception=lambda *a, **k: None,
        ),
        POSITION_SIZE_MODE="RISK",
        LEVERAGE=5,
        RISK_PER_TRADE_PCT=1.0,
        STOP_LOSS_PCT=1.5,
        TAKE_PROFIT_PCT=3.0,
        MAX_DAILY_LOSS_PCT=5.0,
        ACCOUNT_HARD_DAILY_LOSS_PCT=10.0,
    )


def _common(monkeypatch):
    monkeypatch.setattr(trader.cc_ownership, "account_order_lock", lambda _u: contextlib.nullcontext())
    monkeypatch.setattr(symbol_entry_control, "is_paused", lambda *_: False)
    monkeypatch.setattr(trader.core_kill_switch, "is_active", lambda *_: False)
    monkeypatch.setattr(trader, "_reentry_blocked", lambda *_a, **_k: (False, 0.0))

    class Guard:
        def __init__(self, *a, **k):
            pass
        def allow_new_entry(self, equity):
            return True

    monkeypatch.setattr(trader.risk_manager, "DailyLossGuard", Guard)
    monkeypatch.setattr(trader.risk_manager, "calculate_position_size", lambda cfg, equity, price: 2.0)
    monkeypatch.setattr(trader.risk_manager, "quantize_coin_amount_to_market", lambda client, symbol, amount: 1.5)


def test_manual_entry_uses_current_core_leverage_sizing_and_sl_tp(monkeypatch):
    _common(monkeypatch)
    state = FakeState()
    client = FakeClient()
    captured = {}

    def fake_execute(cfg, state, client, symbol, side, amount, entry_price, sl_price, tp_price, **kwargs):
        captured.update(
            side=side, amount=amount, entry_price=entry_price,
            sl_price=sl_price, tp_price=tp_price, decision=kwargs.get("decision"),
        )
        state.update_symbol(symbol, position={"contracts": 1.5, "side": side})
        return True

    monkeypatch.setattr(trader, "_execute_entry", fake_execute)
    result = trader.manual_entry_now(_cfg(), state, client, "BTC/USDT:USDT", "long")

    assert result["ok"] is True
    assert captured["side"] == "long"
    assert captured["amount"] == 1.5
    assert captured["entry_price"] == 100.0
    assert round(captured["sl_price"], 4) == 98.5
    assert round(captured["tp_price"], 4) == 103.0
    assert captured["decision"]["manual_entry"] is True
    assert result["leverage"] == 5
    assert result["position_size_mode"] == "RISK"
    assert result["margin_estimate_usdt"] == 30.0


def test_manual_entry_blocks_when_position_already_exists(monkeypatch):
    _common(monkeypatch)
    called = {"execute": False}
    monkeypatch.setattr(
        trader, "_execute_entry",
        lambda *a, **k: called.__setitem__("execute", True),
    )
    result = trader.manual_entry_now(
        _cfg(), FakeState(), FakeClient(position={"side": "long", "contracts": 1}),
        "BTC/USDT:USDT", "short",
    )
    assert result["ok"] is False
    assert result["reason"] == "position_already_open"
    assert called["execute"] is False


def test_manual_entry_keeps_daily_loss_guard(monkeypatch):
    _common(monkeypatch)

    class BlockGuard:
        def __init__(self, *a, **k):
            pass
        def allow_new_entry(self, equity):
            return False

    monkeypatch.setattr(trader.risk_manager, "DailyLossGuard", BlockGuard)
    result = trader.manual_entry_now(_cfg(), FakeState(), FakeClient(), "BTC/USDT:USDT", "long")
    assert result == {"ok": False, "reason": "daily_loss_guard"}


def test_manual_entry_keeps_symbol_pause(monkeypatch):
    _common(monkeypatch)
    monkeypatch.setattr(symbol_entry_control, "is_paused", lambda *_: True)
    result = trader.manual_entry_now(_cfg(), FakeState(), FakeClient(), "BTC/USDT:USDT", "long")
    assert result == {"ok": False, "reason": "symbol_entry_paused"}


def test_manual_entry_rejects_non_live_execution(monkeypatch):
    _common(monkeypatch)
    cfg = _cfg()
    cfg.EXECUTION_MODE = "SHADOW"
    result = trader.manual_entry_now(cfg, FakeState(), FakeClient(), "BTC/USDT:USDT", "long")
    assert result == {"ok": False, "reason": "live_mode_required"}


def test_manual_entry_pending_receipt_is_not_reported_as_failed(monkeypatch):
    import core_entry_events
    _common(monkeypatch)
    monkeypatch.setattr(trader, "_execute_entry", lambda *a, **k: False)
    monkeypatch.setattr(core_entry_events, "order_receipt",
                        lambda user_dir, decision_id: {
                            "status": "ORDER_PENDING", "symbol": "BTC/USDT:USDT",
                            "decision_id": decision_id, "payload": {}})
    result = trader.manual_entry_now(_cfg(), FakeState(), FakeClient(),
                                     "BTC/USDT:USDT", "long")
    assert result["ok"] is True
    assert result["pending"] is True
    assert result["decision_id"].startswith("manual-BTC/USDT:USDT-long-")


def test_manual_entry_without_pending_receipt_does_not_claim_submission(monkeypatch):
    import core_entry_events
    _common(monkeypatch)
    monkeypatch.setattr(trader, "_execute_entry", lambda *a, **k: False)
    monkeypatch.setattr(core_entry_events, "order_receipt", lambda *a: None)
    result = trader.manual_entry_now(_cfg(), FakeState(), FakeClient(),
                                     "BTC/USDT:USDT", "long")
    assert result == {"ok": False, "reason": "entry_execution_or_protection_failed"}
