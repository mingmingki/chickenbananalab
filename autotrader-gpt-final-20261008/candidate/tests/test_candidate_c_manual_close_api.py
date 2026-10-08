from types import SimpleNamespace

import web_app


def test_candidate_c_close_symbol_reserves_owned_running_position(tmp_path, monkeypatch):
    cfg = SimpleNamespace(
        CANDIDATE_C_SYMBOLS=["DOGE/USDT:USDT", "SOL/USDT:USDT"],
        CANDIDATE_C_LIVE_EXECUTE=True,
        REENTRY_COOLDOWN_MINUTES=15,
        user_dir=str(tmp_path),
    )
    ctx = SimpleNamespace(cfg=cfg, dir=str(tmp_path), username="tester", state=SimpleNamespace())
    monkeypatch.setattr(web_app, "get_context", lambda _u: ctx)
    monkeypatch.setattr(
        web_app.candidate_c_runtime, "snapshot",
        lambda *_a, **_k: {"running": True, "status": "RUNNING", "observation_fresh": True},
    )

    entry = SimpleNamespace(intent_id="entry-1")
    class FakeLedger:
        pass
    monkeypatch.setattr(web_app.il.IntentLedger, "load", lambda *a, **k: FakeLedger())
    monkeypatch.setattr(web_app.cem.PositionEpochStore, "load", lambda *a, **k: object())
    monkeypatch.setattr(web_app.okx_client, "OkxClient", lambda *a, **k: object())
    monkeypatch.setattr(
        web_app.candidate_c_ownership, "validate_candidate_c_position_owner",
        lambda *a, **k: {"allowed": True, "entry_record": entry, "position": {"side": "long"}},
    )

    client = web_app.app.test_client()
    with client.session_transaction() as sess:
        sess["authenticated"] = True
        sess["username"] = "tester"

    res = client.post("/api/candidate_c_close_symbol", json={"symbol": "SOL/USDT:USDT"})
    assert res.status_code == 202, res.get_json()
    data = res.get_json()
    assert data["ok"] is True
    assert data["pending"] is True
    assert data["manual_close"]["position_epoch"] == "entry-1"


def test_candidate_c_close_symbol_rejects_when_loop_not_running(tmp_path, monkeypatch):
    cfg = SimpleNamespace(
        CANDIDATE_C_SYMBOLS=["SOL/USDT:USDT"], CANDIDATE_C_LIVE_EXECUTE=True,
        REENTRY_COOLDOWN_MINUTES=15, user_dir=str(tmp_path),
    )
    ctx = SimpleNamespace(cfg=cfg, dir=str(tmp_path), username="tester", state=SimpleNamespace())
    monkeypatch.setattr(web_app, "get_context", lambda _u: ctx)
    monkeypatch.setattr(web_app.candidate_c_runtime, "snapshot", lambda *_a, **_k: {"running": False})
    client = web_app.app.test_client()
    with client.session_transaction() as sess:
        sess["authenticated"] = True
        sess["username"] = "tester"
    res = client.post("/api/candidate_c_close_symbol", json={"symbol": "SOL/USDT:USDT"})
    assert res.status_code == 409
