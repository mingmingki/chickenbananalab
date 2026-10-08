import contextlib
import time
from types import SimpleNamespace

import core_manual_close
import symbol_entry_control
import trader
import web_app

SYMBOL = "BTC/USDT:USDT"


class FakeState:
    def __init__(self):
        self.symbols = {SYMBOL: {}}

    def snapshot(self):
        return {"symbols": self.symbols}

    def update_symbol(self, symbol, **kwargs):
        self.symbols.setdefault(symbol, {}).update(kwargs)


class FakeExchange:
    def market(self,symbol):return {"precision":{"price":.01}}
    def fetch_open_orders(self, symbol):
        return []


class FakeClient:
    exchange = FakeExchange()
    def __init__(self):
        self.entry_calls = []

    def fetch_position(self):
        return None

    def fetch_pending_protection_algo_ids(self):
        return []

    def fetch_usdt_equity(self):
        return 1000.0

    def fetch_last_price(self):
        return 100.0

    def ensure_leverage(self):
        pass

    def contract_size(self):
        return 1.0

    def create_position_with_sl_tp(self, side, amount, sl_price, tp_price, **kwargs):
        self.entry_calls.append((side, amount, sl_price, tp_price))
        self.order=dict(timestamp=int(time.time()*1000)-10,lastTradeTimestamp=int(time.time()*1000),id="manual-order-1",symbol=SYMBOL,side="buy" if side=="long" else "sell",clientOrderId=kwargs["client_order_id"],status="closed",filled=amount,remaining=0,average=100.)
        return self.order

    def fetch_entry_order_fills(self,order_id):
        return [dict(id="manual-fill-1",order=order_id,symbol=SYMBOL,side="buy",amount=self.order["filled"],price=100,timestamp=self.order["lastTradeTimestamp"])]


def _cfg(tmp_path):
    cfg = SimpleNamespace(
        EXECUTION_MODE="LIVE", user_dir=str(tmp_path), POSITION_SIZE_MODE="RISK",
        LEVERAGE=5, RISK_PER_TRADE_PCT=1.0, STOP_LOSS_PCT=1.5, TAKE_PROFIT_PCT=3.0,
        MAX_DAILY_LOSS_PCT=5.0, ACCOUNT_HARD_DAILY_LOSS_PCT=10.0,
        logger=SimpleNamespace(warning=lambda *a, **k: None, info=lambda *a, **k: None,
                               error=lambda *a, **k: None, exception=lambda *a, **k: None),
    )
    cfg._core_loss_guards = {SYMBOL: SimpleNamespace(allow_new_entry=lambda equity: True)}
    return cfg


def _expired_confirmed_close(user_dir):
    now = time.time()
    record = core_manual_close.reserve(user_dir, SYMBOL, now=now - 1000)
    core_manual_close.confirm(user_dir, SYMBOL, record["close_id"], 900, now=now - 1000)
    return core_manual_close.get(user_dir, SYMBOL)


def test_manual_entry_after_elapsed_cooldown_bypasses_ai_fresh_bar_only(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    state = FakeState()
    client = FakeClient()
    record = _expired_confirmed_close(cfg.user_dir)
    assert core_manual_close.block_reason(record) == "new_closed_bar_required"
    assert core_manual_close.block_reason(record, require_fresh=False) is None

    monkeypatch.setattr(trader.cc_ownership, "account_order_lock", lambda _u: contextlib.nullcontext())
    monkeypatch.setattr(symbol_entry_control, "is_paused", lambda *_: False)
    monkeypatch.setattr(trader.core_kill_switch, "is_active", lambda *_: False)
    monkeypatch.setattr(trader.risk_manager, "DailyLossGuard", lambda *a, **k: SimpleNamespace(allow_new_entry=lambda equity: True))
    monkeypatch.setattr(trader.risk_manager, "calculate_position_size", lambda cfg, equity, price: 1.0)
    monkeypatch.setattr(trader.risk_manager, "quantize_coin_amount_to_market", lambda client, symbol, amount: amount)
    position = {"side": "long", "contracts": 1.0, "entry_price": 100.0, "mark_price": 100.0,
                "unrealized_pnl": 0.0, "pnl_pct": 0.0, "position_id":"manual-life-1", "entry_timestamp_ms":int(time.time()*1000), "last_trade_id":"manual-fill-1"}
    client.fetch_position=lambda:position if client.entry_calls else None
    monkeypatch.setattr(trader.order_safety, "verify_protection", lambda *a, **k: {"ok": True, "position": position})
    monkeypatch.setattr(trader.trade_log, "record_open", lambda *a, **k: None)
    monkeypatch.setattr(trader.reduce_v2_state, "clear", lambda *a, **k: None)
    monkeypatch.setattr(trader.reduce_v2_state, "ensure_position", lambda *a, **k: None)
    monkeypatch.setattr(trader.core_add_position_state, "clear", lambda *a, **k: None)
    monkeypatch.setattr(trader, "_notify_telegram", lambda *a, **k: None)

    result = trader.manual_entry_now(cfg, state, client, SYMBOL, "long")
    assert result["ok"] is True
    assert len(client.entry_calls) == 1
    assert core_manual_close.get(cfg.user_dir, SYMBOL)["status"] == "completed"


def test_dashboard_status_separates_manual_entry_gate_from_ai_fresh_bar(tmp_path):
    cfg = _cfg(tmp_path)
    state = FakeState()
    _expired_confirmed_close(cfg.user_dir)

    status = web_app._manual_close_status_for_api(cfg, state, SYMBOL)

    assert status["reentry_block_reason"] == "new_closed_bar_required"
    assert status["manual_entry_block_reason"] is None
