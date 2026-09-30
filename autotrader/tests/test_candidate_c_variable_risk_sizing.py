from pathlib import Path
from types import SimpleNamespace

import candidate_c_hybrid_live_adapter as live
import candidate_c_live_activation as activation
import candidate_c_runtime as runtime
import config


class FakeClient:
    def __init__(self, *, contract_size=1.0, lot_step=0.01, min_contracts=0.01):
        self._meta = {
            "contract_size": contract_size,
            "lot_step": lot_step,
            "min_contracts": min_contracts,
        }

    def instrument_metadata(self):
        return dict(self._meta)


def cfg(**overrides):
    base = dict(
        CANDIDATE_C_SIZING_MODE="VARIABLE_RISK",
        CANDIDATE_C_RISK_PER_TRADE_PCT=1.0,
        CANDIDATE_C_FIXED_MARGIN_USDT=500.0,
        CANDIDATE_C_LEVERAGE=5,
        CANDIDATE_C_MAX_ORDER_NOTIONAL_USDT=5000.0,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def intent(stop):
    return SimpleNamespace(raw_stop_price=stop)


def test_variable_risk_sizes_to_one_percent_of_equity_at_stop():
    result = live._calculate_candidate_c_entry_amount(
        cfg(), FakeClient(), intent(90.0), fresh_price=100.0, fresh_equity=2600.0,
    )
    assert result["ok"] is True
    assert result["amount_coin"] == 2.6
    assert result["notional_usdt"] == 260.0


def test_variable_risk_uses_risk_pct_and_order_safety_cap_not_fixed_margin():
    result = live._calculate_candidate_c_entry_amount(
        cfg(), FakeClient(), intent(99.5), fresh_price=100.0, fresh_equity=2600.0,
    )
    assert result["ok"] is True
    assert result["amount_coin"] == 50.0
    assert result["notional_usdt"] == 5000.0


def test_fixed_margin_mode_keeps_legacy_sizing_unchanged():
    result = live._calculate_candidate_c_entry_amount(
        cfg(CANDIDATE_C_SIZING_MODE="FIXED_MARGIN"), FakeClient(), intent(90.0),
        fresh_price=100.0, fresh_equity=2600.0,
    )
    assert result["ok"] is True
    assert result["amount_coin"] == 25.0
    assert result["notional_usdt"] == 2500.0


def test_variable_risk_below_exchange_minimum_fails_closed():
    result = live._calculate_candidate_c_entry_amount(
        cfg(CANDIDATE_C_RISK_PER_TRADE_PCT=0.01),
        FakeClient(lot_step=1.0, min_contracts=10.0), intent(90.0),
        fresh_price=100.0, fresh_equity=1000.0,
    )
    assert result["ok"] is False
    assert result["reason"] == "below_minimum"


def test_config_defaults_legacy_fixed_margin_and_loads_explicit_variable_risk(tmp_path):
    (tmp_path / ".env").write_text("", encoding="utf-8")
    c = config.UserConfig(str(tmp_path))
    assert c.CANDIDATE_C_SIZING_MODE == "FIXED_MARGIN"
    assert c.CANDIDATE_C_RISK_PER_TRADE_PCT == 1.0
    (tmp_path / ".env").write_text(
        "CANDIDATE_C_SIZING_MODE=VARIABLE_RISK\nCANDIDATE_C_RISK_PER_TRADE_PCT=0.8\n",
        encoding="utf-8",
    )
    c.reload()
    assert c.CANDIDATE_C_SIZING_MODE == "VARIABLE_RISK"
    assert c.CANDIDATE_C_RISK_PER_TRADE_PCT == 0.8


def test_runtime_settings_and_activation_fingerprint_include_risk_sizing():
    c = SimpleNamespace(
        CANDIDATE_C_ENABLED=True, CANDIDATE_C_LIVE_EXECUTE=True,
        CANDIDATE_C_SYMBOLS=["DOGE/USDT:USDT", "SOL/USDT:USDT"],
        CANDIDATE_C_SIZING_MODE="VARIABLE_RISK", CANDIDATE_C_RISK_PER_TRADE_PCT=1.0,
        CANDIDATE_C_FIXED_MARGIN_USDT=500.0, CANDIDATE_C_LEVERAGE=5,
        CANDIDATE_C_MAX_ORDER_NOTIONAL_USDT=5000.0,
        CANDIDATE_C_MAX_CONCURRENT_POSITIONS=2, CANDIDATE_C_MAX_DAILY_LOSS_PCT=5.0,
        ACCOUNT_HARD_DAILY_LOSS_PCT=10.0, CANDIDATE_C_GPT_ENTRY_GATE_ENABLED=False,
    )
    settings = runtime.effective_settings(c)
    assert settings["sizing_mode"] == "VARIABLE_RISK"
    assert settings["risk_per_trade_pct"] == 1.0
    before = activation.activation_state_fingerprint(c)
    c.CANDIDATE_C_RISK_PER_TRADE_PCT = 0.8
    after = activation.activation_state_fingerprint(c)
    assert before != after


def test_dashboard_exposes_three_clear_candidate_order_modes():
    text = (Path(__file__).resolve().parents[1] / "templates/dashboard.html").read_text()
    assert "name=\"cc-order-mode\"" in text
    assert "value=\"AUTO_ALL\"" in text
    assert "value=\"FIXED_MARGIN_AUTO_EXIT\"" in text
    assert "value=\"MANUAL_ALL\"" in text
    assert "id=\"cc-risk-per-trade\"" in text
    assert "id=\"cc-margin\"" in text
    assert "id=\"cc-stop-loss\"" in text and "id=\"cc-take-profit\"" in text
