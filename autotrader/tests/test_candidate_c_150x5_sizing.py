import execution_units


def test_candidate_c_150_margin_5x_targets_750_notional():
    result = execution_units.calculate_candidate_entry_size(
        config_payload={
            "sizing_mode": "FIXED_MARGIN",
            "fixed_margin_usdt": 150,
            "leverage": 5,
            "max_order_notional_usdt": 750,
        },
        entry_price=100.0,
        equity=2000.0,
        stop_risk_per_coin=5.0,
        contract_size=1.0,
        lot_step=0.01,
        min_contracts=0.01,
    )
    assert result["action"] == "ORDER"
    assert result["uncapped_notional_usdt"] == 750
    assert result["max_order_notional_usdt"] == 750
    assert result["notional_usdt"] == 750
    assert result["amount_coin"] == 7.5
