import candidate_c_decision_engine as dec


def test_profit_lock_cost_metadata_ready_accepts_complete_numeric_metadata():
    position = {
        "effective_entry_price": 100.0,
        "entry_fee_usdt": 0.1,
        "contract_size": 1.0,
        "fee_rate": 0.0005,
        "spread_bps": 3.0,
        "slippage_bps": 3.0,
    }
    assert dec.profit_lock_cost_metadata_ready(position) is True


def test_profit_lock_cost_metadata_ready_rejects_missing_or_nonfinite_metadata():
    position = {
        "effective_entry_price": 100.0,
        "entry_fee_usdt": 0.1,
        "contract_size": 1.0,
        "fee_rate": 0.0005,
        "spread_bps": 3.0,
        "slippage_bps": float("nan"),
    }
    assert dec.profit_lock_cost_metadata_ready(position) is False
    position["slippage_bps"] = 3.0
    position["entry_fee_usdt"] = None
    assert dec.profit_lock_cost_metadata_ready(position) is False
