"""v33-1 true trading fill price must not be inferred from gross=0."""
import pytest
import exit_reentry_shadow as shadow

S="ETH/USDT:USDT"

def lifecycle(*,gross=0.0,price=None,side="short"):
    entry=dict(type="open",symbol=S,side=side,time="2026-10-15T01:00:00",
               amount=3.6,price=2429.41,sl_price=2455.5,tp_price=2368.)
    close=dict(type="close",symbol=S,side=side,time="2026-10-15T02:00:14",
               amount=3.6,entry_price=2429.41,pnl=gross,fee=.7,
               pnl_source="okx_realized",okx_net_pnl=-.6749,
               close_price=price,reason="position_ai_close_all")
    return dict(trade_id="a",symbol=S,side=side,entry_time=entry["time"],
                exit_time=close["time"],final_close_reason="position_ai_close_all",
                events=[entry,close],entry_price=2429.41,
                lifecycle_net=-.6749,final_close_net=-.6749,reduce_net=0,
                coverage="complete")

def test_zero_gross_placeholder_never_constructs_exit_price():
    m=shadow._reconstructed_lifecycle_exit_meta(lifecycle())
    assert m["exit_price"] is None
    assert m["exit_price_basis"]=="unavailable"
    assert m["final_entry_price"]==2429.41

def test_exact_journal_fill_is_authoritative_even_when_gross_zero():
    m=shadow._reconstructed_lifecycle_exit_meta(lifecycle(price=2421.8))
    assert m["exit_price"]==pytest.approx(2421.8)
    assert m["exit_price_basis"]=="journal_fill"

def test_nonzero_gross_is_labeled_estimate_not_verified():
    m=shadow._reconstructed_lifecycle_exit_meta(lifecycle(gross=-7.2))
    assert m["exit_price"]==pytest.approx(2431.41)
    assert m["exit_price_basis"]=="gross_pnl_implied_estimate"

def test_legacy_bad_exit_price_is_cleared_without_touching_realized_net(tmp_path):
    u=str(tmp_path); life=lifecycle()
    first=shadow.record_ai_exit(u,dict(symbol=S,side="short",exit_time=life["exit_time"],
                                      exit_price=2429.41,original_sl=2455.5,original_tp=2368.))
    assert first["exit_price"]==2429.41
    changed=shadow.backfill_ai_close_lifecycles(u,[life])
    assert changed==1
    last=shadow.recent(u,1)[0]
    assert last["exit_price"] is None
    assert last["exit_price_basis"]=="unavailable"
    assert last["actual_exit_lifecycle_net"]==pytest.approx(-.6749)
    assert shadow.three_way_counterfactual(last)["resolved"] is False
    assert shadow.backfill_ai_close_lifecycles(u,[life])==0

def test_missing_exit_price_rejects_nonfinite_price():
    m=shadow._reconstructed_lifecycle_exit_meta(lifecycle(price=float("nan")))
    assert m["exit_price"] is None and m["exit_price_basis"]=="unavailable"

def test_explicit_verified_three_way_uses_existing_api(tmp_path):
    x=shadow.record_ai_exit(str(tmp_path),dict(symbol=S,side="short",
        exit_time="2026-10-15T01:00:00",exit_price=2440.,
        original_sl=2455.,original_tp=2380.))
    assert shadow.three_way_counterfactual(x)["resolved"] is False  # no forward path
