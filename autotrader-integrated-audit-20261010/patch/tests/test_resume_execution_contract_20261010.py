"""Saved ADA boundary and unchanged risk/protection contracts; no live calls."""
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace
import pytest
from adaptive_exit_policy import production_adaptive_exit_policy
from entry_execution_normalization import normalize_prices
from core_entry_sltp_repair import reconcile_core_plan
from test_core_entry_sltp_repair_v7 import case


def exchange(tick):
    return SimpleNamespace(ensure_markets_loaded=lambda: None, exchange=SimpleNamespace(
        market=lambda _: {'precision': {'price': tick}},
        price_to_precision=lambda _, p: str(Decimal(str(p)).quantize(Decimal(str(tick))))))


@pytest.mark.parametrize('side', ['long', 'short'])
def test_saved_ada_tp1_at_final_quote_has_feasible_tick_and_bounded_stop(side):
    # Exact saved short: reviewed .2527, final .2524, SL .26275746,
    # TP1 .2524, TP2 .222376; mirrored long checks symmetric rounding.
    policy, ctx, base = case()
    sign = 1 if side == 'long' else -1
    reviewed = .2527
    entry = reviewed + sign * .0003
    stop = reviewed - sign * .01005746
    tp1 = entry
    target = reviewed + sign * .030324
    plan = replace(base, side=side, stop_price=stop,
                   tp1=replace(base.tp1, price=tp1), tp2=replace(base.tp2, price=target))
    sl, t1, t2, tick = normalize_prices(exchange(.0001), 'ADA/USDT:USDT',
        side, entry, reviewed, stop, target, plan, 5, policy, .002, atr=.007533)
    assert sign * (entry - sl) > 0
    assert sign * (t1 - entry) > 0 and sign * (t2 - t1) > 0
    assert abs(sl-entry)/entry*500 <= 20 + 1e-10
    assert abs(sl-stop) <= reviewed*.002
    assert sign*(sl-stop) >= 0  # protection never loosens
    assert abs(t1-tp1) <= reviewed*.002*1.06
    assert abs(t2-target) <= reviewed*.002*1.12
    assert (abs(t2-entry)-entry*.001)/(abs(sl-entry)+entry*.001) >= 1.1


@pytest.mark.parametrize('side', ['long', 'short'])
def test_native_final_quote_can_tighten_stop_only_inside_approval(side):
    _,ctx,base=case()
    sign=1 if side=='long' else -1
    stop=100-sign*3.98
    plan=replace(base,side=side,stop_price=stop,
        tp1=replace(base.tp1,price=100+sign*5),tp2=replace(base.tp2,price=100+sign*12))
    entry=100+sign*.19
    sl,t1,t2,_=normalize_prices(exchange(.0001),'ADA/USDT:USDT',side,
        entry,100,stop,plan.tp2.price,plan,5,production_adaptive_exit_policy(),.002,atr=2)
    assert sign*(sl-stop)>=0 and abs(sl-stop)<=.2
    assert abs(sl-entry)/entry*500<=20+1e-10
    assert 0 < abs(sl-entry)/2


def test_reconciliation_invalid_allowed_target_never_returns_entry_authority():
    policy,ctx,base=case()
    unsafe=replace(base,entry_allowed=True,tp2=replace(base.tp2,price=ctx.entry_price*1.5))
    result,reason=reconcile_core_plan(unsafe,ctx,policy)
    assert not result.entry_allowed
    assert reason=='target_above_leverage_cap'


@pytest.mark.parametrize('side',['long','short'])
def test_native_normalization_rejects_stop_change_outside_approval(side):
    policy,ctx,base=case()
    sign=1 if side=='long' else -1
    stop=100-sign*5
    plan=replace(base,side=side,stop_price=stop,
        tp1=replace(base.tp1,price=100+sign*5),tp2=replace(base.tp2,price=100+sign*12))
    with pytest.raises(ValueError,match='final_plan_normalization_exceeds_approval'):
        normalize_prices(exchange(.0001),'ADA/USDT:USDT',side,100,100,
            stop,plan.tp2.price,plan,5,policy,.002,atr=2)


@pytest.mark.parametrize('side',['long','short'])
def test_coarse_tick_cannot_invent_target_outside_cap(side):
    policy,ctx,base=case()
    sign=1 if side=='long' else -1
    stop=1-sign*.03
    plan=replace(base,side=side,stop_price=stop,
        tp1=replace(base.tp1,price=1+sign*.03),tp2=replace(base.tp2,price=1+sign*.1))
    with pytest.raises(ValueError):
        normalize_prices(exchange(.1),'ADA/USDT:USDT',side,1,1,
            stop,plan.tp2.price,plan,5,policy,.002,atr=.01)


def test_rejected_plan_has_reproducible_hash_and_no_authority():
    policy,ctx,base=case()
    unsafe=replace(base,entry_allowed=True,tp2=replace(base.tp2,price=ctx.entry_price*1.5))
    first,reason=reconcile_core_plan(unsafe,ctx,policy)
    second,_=reconcile_core_plan(unsafe,ctx,policy)
    assert not first.entry_allowed and first.reason_code==reason
    assert first.plan_hash==second.plan_hash and first.plan_hash!=unsafe.plan_hash


@pytest.mark.parametrize('side',['long','short'])
def test_native_noise_floor_still_blocks_unrepresentable_stop(side):
    policy,ctx,base=case()
    sign=1 if side=='long' else -1
    stop=100-sign*3.98
    plan=replace(base,side=side,stop_price=stop,
        tp1=replace(base.tp1,price=100+sign*5),tp2=replace(base.tp2,price=100+sign*12))
    with pytest.raises(ValueError,match='final_native_stop_too_tight_for_market'):
        normalize_prices(exchange(.0001),'ADA/USDT:USDT',side,100,100,
            stop,plan.tp2.price,plan,5,policy,.002,atr=10)
