import logging
import threading
from types import SimpleNamespace

import trader
import web_app


class FakeState:
    def __init__(self):
        self.data = {"running": False, "clients": {}}
    def update(self, **kwargs):
        self.data.update(kwargs)
    def snapshot(self):
        return dict(self.data)


class FakeThread:
    def __init__(self, target=None, args=(), daemon=None, event=None):
        self._alive = False
        self._event = event
    def start(self):
        self._alive = True
    def join(self, timeout=None):
        if self._event is None or self._event.is_set():
            self._alive = False
    def is_alive(self):
        return self._alive


def _logged_in_client(ctx, monkeypatch):
    monkeypatch.setattr(web_app, "get_context", lambda _username: ctx)
    client = web_app.app.test_client()
    with client.session_transaction() as sess:
        sess["authenticated"] = True
        sess["username"] = "operator"
    return client


def test_core_run_all_does_not_start_candidate_engine(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        trader.candidate_c_trader_adapter,
        "run_candidate_c_engine",
        lambda *args, **kwargs: calls.append((args, kwargs)) or [],
    )
    monkeypatch.setattr(trader.market_context, "start", lambda: None)
    monkeypatch.setattr(trader.threading, "Thread", FakeThread)
    monkeypatch.setattr(trader.pnl_store, "load_baseline", lambda _user_dir: 1000.0)
    monkeypatch.setattr(trader.risk_manager, "DailyLossGuard", lambda *a, **k: object())

    cfg = SimpleNamespace(
        ENABLED_SYMBOLS=[], user_dir=str(tmp_path), logger=logging.getLogger("test-core-only"),
        POLL_INTERVAL_SECONDS=300, RISK_PER_TRADE_PCT=1.0, STOP_LOSS_PCT=1.5,
        TAKE_PROFIT_PCT=3.0, MAX_DAILY_LOSS_PCT=5.0, LEVERAGE=5,
        validate=lambda: None,
    )
    state = FakeState()
    trader.run_all(cfg, state, threading.Event())
    assert calls == []


def test_candidate_stop_only_stops_candidate_and_clears_handles(monkeypatch):
    core_event = threading.Event()
    candidate_event = threading.Event()
    candidate_thread = FakeThread(event=candidate_event)
    candidate_thread.start()
    ctx = SimpleNamespace(
        stop_event=core_event,
        candidate_c_stop_event=candidate_event,
        candidate_c_threads=[candidate_thread],
        cfg=SimpleNamespace(CANDIDATE_C_SYMBOLS=["DOGE/USDT:USDT", "SOL/USDT:USDT"]),
        dir="/tmp/test-candidate-stop",
    )
    client = _logged_in_client(ctx, monkeypatch)
    response = client.post("/api/candidate_c_stop", json={})
    data = response.get_json()
    assert response.status_code == 200
    assert data["ok"] is True
    assert data["stopped"] is True
    assert candidate_event.is_set() is True
    assert core_event.is_set() is False
    assert ctx.candidate_c_stop_event is None
    assert ctx.candidate_c_threads == []


def test_core_stop_does_not_stop_candidate(monkeypatch):
    core_event = threading.Event()
    candidate_event = threading.Event()
    ctx = SimpleNamespace(
        stop_event=core_event,
        candidate_c_stop_event=candidate_event,
        candidate_c_threads=[],
        state=FakeState(),
        cfg=SimpleNamespace(),
        dir="/tmp/test-core-stop",
    )
    client = _logged_in_client(ctx, monkeypatch)
    response = client.post("/api/stop", json={})
    assert response.status_code == 200
    assert core_event.is_set() is True
    assert candidate_event.is_set() is False
