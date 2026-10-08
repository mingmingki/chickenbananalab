import inspect
import unified_trade_guard as ug
import trader
import candidate_c_decision_engine as dec


def test_entry_risk_score_counts_known_historical_bad_conditions():
    r = ug.entry_risk_score(symbol="PI/USDT:USDT", side="short", hour_kst=14, tf_mixed=True)
    assert r["score"] == 4
    assert set(r["factors"]) == {"weak_hours_12_17_kst", "pi", "short", "tf_mixed"}


def test_entry_risk_score_good_context_is_zero():
    r = ug.entry_risk_score(symbol="XRP/USDT:USDT", side="long", hour_kst=20, tf_mixed=False)
    assert r == {"score": 0, "factors": []}


def test_entry_risk_policy_is_gradual_not_blanket_block():
    assert ug.entry_risk_policy(0) == {"size_fraction": 1.0, "require_strong_confirmation": False, "blocked": False}
    assert ug.entry_risk_policy(1) == {"size_fraction": 0.5, "require_strong_confirmation": False, "blocked": False}
    assert ug.entry_risk_policy(2) == {"size_fraction": 0.5, "require_strong_confirmation": True, "blocked": False}
    assert ug.entry_risk_policy(3)["blocked"] is True
    assert ug.entry_risk_policy(4)["blocked"] is True


def test_core_historical_score_has_no_size_or_confidence_authority():
    adj = trader._core_entry_risk_adjustment(
        symbol="BTC/USDT:USDT", side="short", hour_kst=14, tf_mixed=False, amount=10.0,
    )
    assert adj["score"] == 2
    assert adj["amount"] == 10.0
    assert adj["require_strong_confirmation"] is False
    assert adj["blocked"] is False


def test_core_score3_or_more_preserves_configured_size_without_veto():
    for symbol, mixed, score in [
        ("XRP/USDT:USDT", True, 3), ("PI/USDT:USDT", True, 4),
    ]:
        adj = trader._core_entry_risk_adjustment(
            symbol=symbol, side="short", hour_kst=14, tf_mixed=mixed, amount=20.0,
        )
        assert adj["score"] == score
        assert adj["amount"] == 20.0
        assert adj["blocked"] is False
        assert adj["require_strong_confirmation"] is False


def test_core_and_candidate_entry_paths_wire_shared_score():
    assert "_core_entry_risk_adjustment" in inspect.getsource(trader._handle_new_entry)
    assert "entry_risk_score" in inspect.getsource(dec._decide_legacy)
