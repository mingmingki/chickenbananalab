from types import SimpleNamespace
from pathlib import Path

import config
import web_app


def _client(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text(
        "\n".join([
            "CANDIDATE_C_ENABLED=true",
            "CANDIDATE_C_LIVE_EXECUTE=true",
            "CANDIDATE_C_SYMBOLS=DOGE/USDT:USDT,SOL/USDT:USDT",
            "CANDIDATE_C_FIXED_MARGIN_USDT=150",
            "CANDIDATE_C_LEVERAGE=5",
            "CANDIDATE_C_MAX_ORDER_NOTIONAL_USDT=750",
            "CANDIDATE_C_MAX_CONCURRENT_POSITIONS=2",
            "CANDIDATE_C_MAX_DAILY_LOSS_PCT=5",
            "ACCOUNT_HARD_DAILY_LOSS_PCT=10",
            "CANDIDATE_C_GPT_ENTRY_GATE_ENABLED=false",
        ]) + "\n", encoding="utf-8",
    )
    cfg = config.UserConfig(str(tmp_path))
    ctx = SimpleNamespace(cfg=cfg, dir=str(tmp_path))
    monkeypatch.setattr(web_app, "get_context", lambda _u: ctx)
    monkeypatch.setattr(
        web_app.candidate_c_runtime, "snapshot",
        lambda *_a, **_k: {"thread_alive": False, "start_reserved": False},
    )
    client = web_app.app.test_client()
    with client.session_transaction() as sess:
        sess["authenticated"] = True
        sess["username"] = "tester"
    return client, cfg


def _payload(**overrides):
    data = {
        "mode": "live",
        "symbols": ["DOGE/USDT:USDT", "SOL/USDT:USDT"],
        "fixed_margin_usdt": 150,
        "leverage": 5,
        "max_order_notional_usdt": 750,
        "max_concurrent_positions": 2,
        "max_daily_loss_pct": 5,
        "account_max_daily_loss_pct": 10,
        "gpt_entry_gate_enabled": False,
    }
    data.update(overrides)
    return data


def test_values_above_all_old_application_caps_can_be_saved_while_stopped(tmp_path, monkeypatch):
    client, cfg = _client(tmp_path, monkeypatch)
    payload = _payload(
        fixed_margin_usdt=500,
        leverage=20,
        max_order_notional_usdt=10000,
        max_concurrent_positions=7,
        max_daily_loss_pct=250,
        account_max_daily_loss_pct=300,
    )
    res = client.post("/api/candidate_c_settings", json=payload)
    assert res.status_code == 200, res.get_json()
    assert cfg.CANDIDATE_C_FIXED_MARGIN_USDT == 500
    assert cfg.CANDIDATE_C_LEVERAGE == 20
    assert cfg.CANDIDATE_C_MAX_ORDER_NOTIONAL_USDT == 10000
    assert cfg.CANDIDATE_C_MAX_CONCURRENT_POSITIONS == 7
    assert cfg.CANDIDATE_C_MAX_DAILY_LOSS_PCT == 250
    assert cfg.ACCOUNT_HARD_DAILY_LOSS_PCT == 300


def test_nonpositive_values_and_fractional_integer_fields_are_rejected(tmp_path, monkeypatch):
    client, _cfg = _client(tmp_path, monkeypatch)
    for field, value in (
        ("fixed_margin_usdt", 0),
        ("max_order_notional_usdt", -1),
        ("leverage", 5.5),
        ("max_concurrent_positions", 2.5),
    ):
        res = client.post("/api/candidate_c_settings", json=_payload(**{field: value}))
        assert res.status_code == 400, (field, res.get_json())


def test_running_candidate_c_still_blocks_settings_change(tmp_path, monkeypatch):
    client, _cfg = _client(tmp_path, monkeypatch)
    monkeypatch.setattr(
        web_app.candidate_c_runtime, "snapshot",
        lambda *_a, **_k: {"thread_alive": True, "start_reserved": False},
    )
    res = client.post("/api/candidate_c_settings", json=_payload(fixed_margin_usdt=500))
    assert res.status_code == 409
    assert "실행 중 설정 변경" in res.get_json()["error"]


def test_dashboard_has_no_old_loss_percent_maximum():
    text = (Path(__file__).resolve().parents[1] / "templates/dashboard.html").read_text()
    assert 'id="cc-group-loss" type="number" min="0.01" max="100"' not in text
    assert 'id="cc-account-loss" type="number" min="0.01" max="100"' not in text
