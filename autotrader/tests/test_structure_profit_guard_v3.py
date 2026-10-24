import inspect
import unified_trade_guard as ug
import trader
import candidate_c_decision_engine as dec


def st(*, low=False, high=False):
    return {"swing_low_broken": low, "swing_high_broken": high}


def test_structure_profit_guard_requires_profit_arm():
    r = ug.profit_structure_protection(
        side="long", current_r=0.74,
        current_5m=st(low=True), previous_5m=st(low=True), one_h=st(low=True),
        already_reduced=False,
    )
    assert r["action"] == "none"
    assert r["reason"] == "profit_not_armed"


def test_long_two_consecutive_confirmed_5m_breaks_reduce():
    r = ug.profit_structure_protection(
        side="long", current_r=0.90,
        current_5m=st(low=True), previous_5m=st(low=True), one_h=st(),
        already_reduced=False,
    )
    assert r["action"] == "reduce_25"
    assert r["reason"] == "confirmed_5m_structure_break"


def test_one_5m_break_is_not_enough():
    r = ug.profit_structure_protection(
        side="long", current_r=1.10,
        current_5m=st(low=True), previous_5m=st(low=False), one_h=st(),
        already_reduced=False,
    )
    assert r["action"] == "none"


def test_one_hour_adverse_break_is_immediate_profit_protection():
    r = ug.profit_structure_protection(
        side="long", current_r=0.80,
        current_5m=st(), previous_5m=st(), one_h=st(low=True),
        already_reduced=False,
    )
    assert r["action"] == "reduce_25"
    assert r["reason"] == "confirmed_1h_structure_break"


def test_short_is_symmetric():
    r = ug.profit_structure_protection(
        side="short", current_r=0.82,
        current_5m=st(high=True), previous_5m=st(high=True), one_h=st(),
        already_reduced=False,
    )
    assert r["action"] == "reduce_25"


def test_structure_profit_guard_is_one_shot():
    r = ug.profit_structure_protection(
        side="short", current_r=2.0,
        current_5m=st(high=True), previous_5m=st(high=True), one_h=st(high=True),
        already_reduced=True,
    )
    assert r["action"] == "none"
    assert r["reason"] == "already_reduced"


def test_core_and_candidate_wire_same_shared_guard():
    assert hasattr(trader, "_maybe_execute_profit_structure_live")
    assert "_maybe_execute_profit_structure_live" in inspect.getsource(trader.run_cycle)
    assert "profit_structure_protection" in inspect.getsource(dec._decide_legacy)
