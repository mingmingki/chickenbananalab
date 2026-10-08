from types import SimpleNamespace

import web_app


class FakeState:
    def __init__(self, running=True):
        self.running = running
        self.client = object()

    def snapshot(self):
        return {
            "running": self.running,
            "clients": {"BTC/USDT:USDT": self.client} if self.running else {},
        }


def _client(monkeypatch, running=True):
    cfg = SimpleNamespace()
    ctx = SimpleNamespace(
        cfg=cfg,
        dir="/tmp/manual-entry-api",
        username="tester",
        state=FakeState(running=running),
    )
    monkeypatch.setattr(web_app, "get_context", lambda _u: ctx)
    monkeypatch.setattr(web_app.accounts, "is_approved", lambda _u: True)
    client = web_app.app.test_client()
    with client.session_transaction() as sess:
        sess["authenticated"] = True
        sess["username"] = "tester"
    return client, ctx


def test_manual_entry_api_calls_trader_with_explicit_side(monkeypatch):
    client, ctx = _client(monkeypatch, running=True)
    called = {}

    def fake_manual(cfg, state, okx, symbol, side):
        called.update(cfg=cfg, state=state, okx=okx, symbol=symbol, side=side)
        return {
            "ok": True,
            "symbol": symbol,
            "side": side,
            "leverage": 5,
            "margin_estimate_usdt": 100.0,
            "sl_price": 98.5,
            "tp_price": 103.0,
        }

    monkeypatch.setattr(web_app.trader, "manual_entry_now", fake_manual)
    res = client.post("/api/manual_entry", json={"symbol": "BTC/USDT:USDT", "side": "long"})
    assert res.status_code == 200
    data = res.get_json()
    assert data["ok"] is True
    assert called["symbol"] == "BTC/USDT:USDT"
    assert called["side"] == "long"
    assert called["okx"] is ctx.state.client
    assert "5x" in data["note"]


def test_manual_entry_api_requires_running_core(monkeypatch):
    client, _ctx = _client(monkeypatch, running=False)
    res = client.post("/api/manual_entry", json={"symbol": "BTC/USDT:USDT", "side": "short"})
    assert res.status_code == 409
    assert "CORE가 실행 중" in res.get_json()["error"]


def test_manual_entry_api_rejects_invalid_side(monkeypatch):
    client, _ctx = _client(monkeypatch, running=True)
    res = client.post("/api/manual_entry", json={"symbol": "BTC/USDT:USDT", "side": "close"})
    assert res.status_code == 400


def test_manual_entry_api_requires_json(monkeypatch):
    client, _ctx = _client(monkeypatch, running=True)
    res = client.post(
        "/api/manual_entry",
        data="symbol=BTC%2FUSDT%3AUSDT&side=long",
        content_type="application/x-www-form-urlencoded",
    )
    assert res.status_code == 415


def test_manual_entry_pending_returns_202_not_false_failure(monkeypatch):
    client, _ctx = _client(monkeypatch)
    monkeypatch.setattr(web_app.trader, "manual_entry_now", lambda *a, **k: {
        "ok": True, "pending": True, "symbol": "BTC/USDT:USDT",
        "side": "long", "decision_id": "manual-BTC/USDT:USDT-long-123",
    })
    response = client.post("/api/manual_entry", json={
        "symbol": "BTC/USDT:USDT", "side": "long"})
    assert response.status_code == 202
    data = response.get_json()
    assert data["ok"] is True and data["pending"] is True
    assert data["decision_id"] == "manual-BTC/USDT:USDT-long-123"
    assert "완료" not in data["note"]


def test_manual_entry_status_only_confirms_owned_protected_fill(monkeypatch):
    import core_entry_events
    client, _ctx = _client(monkeypatch)
    decision_id = "manual-BTC/USDT:USDT-long-123"
    url = "/api/manual_entry_status?symbol=BTC%2FUSDT%3AUSDT&decision_id=" + decision_id
    for receipt, expected in [
        ({"symbol": "BTC/USDT:USDT", "status": "ORDER_PENDING", "payload": {}}, "pending"),
        ({"symbol": "BTC/USDT:USDT", "status": "FILLED",
          "payload": {"reason": "protected_fill_confirmed"}}, "filled"),
        ({"symbol": "BTC/USDT:USDT", "status": "FILLED",
          "payload": {"reason": "unverified"}}, "pending"),
        ({"symbol": "BTC/USDT:USDT", "status": "ORDER_FAILED", "payload": {}}, "failed"),
    ]:
        monkeypatch.setattr(core_entry_events, "order_receipt",
                            lambda user_dir, identifier, r=receipt: r)
        response = client.get(url)
        assert response.status_code == 200
        assert response.get_json()["status"] == expected


def test_manual_entry_status_rejects_wrong_symbol_decision(monkeypatch):
    client, _ctx = _client(monkeypatch)
    res = client.get("/api/manual_entry_status?symbol=BTC%2FUSDT%3AUSDT"
                     "&decision_id=manual-ETH/USDT:USDT-long-123")
    assert res.status_code == 400
