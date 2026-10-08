from types import SimpleNamespace

import config
import web_app


def _client(tmp_path, monkeypatch, *, live=True):
    env = tmp_path / ".env"
    env.write_text(
        "\n".join([
            "CANDIDATE_C_ENABLED=true",
            f"CANDIDATE_C_LIVE_EXECUTE={'true' if live else 'false'}",
            "CANDIDATE_C_SYMBOLS=DOGE/USDT:USDT,SOL/USDT:USDT",
            "CANDIDATE_C_FIXED_MARGIN_USDT=50",
            "CANDIDATE_C_LEVERAGE=3",
            "CANDIDATE_C_MAX_ORDER_NOTIONAL_USDT=200",
            "CANDIDATE_C_MAX_CONCURRENT_POSITIONS=2",
            "CANDIDATE_C_MAX_DAILY_LOSS_PCT=5",
            "ACCOUNT_HARD_DAILY_LOSS_PCT=10",
            "CANDIDATE_C_GPT_ENTRY_GATE_ENABLED=false",
        ]) + "\n",
        encoding="utf-8",
    )
    cfg = config.UserConfig(str(tmp_path))
    ctx = SimpleNamespace(cfg=cfg, dir=str(tmp_path))
    monkeypatch.setattr(web_app, "get_context", lambda _u: ctx)
    monkeypatch.setattr(web_app.candidate_c_runtime, "snapshot", lambda *_a, **_k: {"thread_alive": False, "start_reserved": False})
    client = web_app.app.test_client()
    with client.session_transaction() as sess:
        sess["authenticated"] = True
        sess["username"] = "tester"
    return client, cfg


def test_already_live_can_save_risk_reduction_even_with_activation_blocker(tmp_path, monkeypatch):
    client, cfg = _client(tmp_path, monkeypatch, live=True)
    monkeypatch.setattr(
        web_app.candidate_c_runtime, "live_activation_blockers",
        lambda *_a, **_k: ["live_backtest_ema_seed_parity_unverified"],
    )
    res = client.post("/api/candidate_c_settings", json={
        "mode": "live",
        "symbols": ["DOGE/USDT:USDT", "SOL/USDT:USDT"],
        "fixed_margin_usdt": 40,
        "leverage": 2,
        "max_order_notional_usdt": 180,
        "max_concurrent_positions": 2,
        "max_daily_loss_pct": 5,
        "account_max_daily_loss_pct": 10,
        "gpt_entry_gate_enabled": False,
    })
    assert res.status_code == 200, res.get_json()
    assert cfg.CANDIDATE_C_FIXED_MARGIN_USDT == 40
    assert cfg.CANDIDATE_C_LEVERAGE == 2


def test_switching_shadow_to_live_still_requires_activation_validation(tmp_path, monkeypatch):
    client, _cfg = _client(tmp_path, monkeypatch, live=False)
    monkeypatch.setattr(
        web_app.candidate_c_runtime, "live_activation_blockers",
        lambda *_a, **_k: ["live_backtest_ema_seed_parity_unverified"],
    )
    res = client.post("/api/candidate_c_settings", json={
        "mode": "live",
        "symbols": ["DOGE/USDT:USDT", "SOL/USDT:USDT"],
        "gpt_entry_gate_enabled": False,
    })
    assert res.status_code == 409
    assert res.get_json()["blockers"] == ["live_backtest_ema_seed_parity_unverified"]
