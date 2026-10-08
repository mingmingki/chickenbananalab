"""Bounded execution of an approved plan; no strategy, stop widening or budget growth."""
from dataclasses import replace
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
import hashlib
import json
import math


def normalize_prices(client, symbol, side, entry, reviewed, stop, target, plan, leverage, policy, max_drift, *, verified_ai=False, atr=None, anchor=None):
    if side not in ('long','short'):
        raise ValueError('final_exit_price_direction_invalid')
    client.ensure_markets_loaded()
    market=client.exchange.market(symbol)
    precision=market.get('precision',{}).get('price')
    if precision is None:
        raise ValueError('final_price_precision_unavailable')
    tick=Decimal(str(precision))
    if getattr(client.exchange,'precisionMode',None)==2:  # ccxt DECIMAL_PLACES
        tick=Decimal(10)**-int(precision)
    if not tick.is_finite() or tick<=0:
        raise ValueError('final_price_precision_unavailable')
    anchor=anchor or {}
    reviewed=anchor.get('entry_price',reviewed)
    e,r=Decimal(str(entry)),Decimal(str(reviewed))
    sign=Decimal(1 if side=='long' else -1)
    toward=ROUND_FLOOR if side=='long' else ROUND_CEILING
    protective=ROUND_CEILING if side=='long' else ROUND_FLOOR
    def rounded(value,mode):
        price=(Decimal(str(value))/tick).to_integral_value(rounding=mode)*tick
        # Check the actual exchange serializer will send this same price.
        if Decimal(str(client.exchange.price_to_precision(symbol,float(price))))!=price:
            raise ValueError('final_exchange_price_precision_mismatch')
        return float(price)
    sl=rounded(stop,protective)
    approved_stop=float(anchor.get('stop',stop))
    if verified_ai:
        if atr is None or not math.isfinite(float(atr)) or float(atr)<=0:
            raise ValueError('final_ai_atr_unavailable')
        minimum=float(policy['initial_atr_min'])*float(atr)
        maximum=min(Decimal(str(policy['initial_atr_max']))*Decimal(str(atr)),
                    e*Decimal(str(policy['max_leveraged_stop_loss_pct']))/100/Decimal(str(leverage)))
        distance=float(sign)*(entry-sl)
        if distance < minimum-1e-12:
            raise ValueError('final_ai_stop_below_atr_min')
        if sign*(e-Decimal(str(sl))) > maximum:
            sl=rounded(e-sign*maximum,protective)
        distance=float(sign)*(entry-sl)
        if distance<minimum-1e-12 or sign*(e-Decimal(str(sl)))>maximum:
            raise ValueError('final_ai_stop_atr_bounds_unrepresentable')
    if abs(sl-approved_stop)>float(r)*max_drift:
        raise ValueError('final_plan_normalization_exceeds_approval')
    if not ((side=='long' and sl<entry and sl>=approved_stop) or (side=='short' and sl>entry and sl<=approved_stop)):
        raise ValueError('final_exit_price_direction_invalid')
    risk=abs(e-Decimal(str(sl)))
    def target_leg(value,key,leg):
        original=Decimal(str(anchor.get(leg,value)))
        cap=Decimal(str(policy[key]))/100/Decimal(str(leverage))
        if sign*(original-r)>r*cap+Decimal('1e-12'):
            raise ValueError('approved_target_above_leverage_cap')
        minimum=Decimal(0)
        maximum=e*cap
        if verified_ai:
            minimum=risk*Decimal(str(policy[leg+'_r_min']))
            maximum=min(maximum,risk*Decimal(str(policy[leg+'_r_max'])))
        if minimum>maximum:
            raise ValueError('final_target_r_and_cap_incompatible')
        desired=sign*(Decimal(str(value))-e)
        distance=min(maximum,max(minimum,desired))
        result=rounded(e+sign*distance,toward)
        final_distance=sign*(Decimal(str(result))-e)
        if final_distance<minimum:
            result=rounded(e+sign*minimum,protective)
            final_distance=sign*(Decimal(str(result))-e)
        if not minimum<=final_distance<=maximum or final_distance<=0:
            raise ValueError('final_target_r_bounds_unrepresentable')
        if abs(Decimal(str(result))-original)>r*Decimal(str(max_drift))*(1+cap):
            raise ValueError('final_plan_normalization_exceeds_approval')
        return result
    if plan.tp1 is None or plan.tp2 is None:
        raise ValueError('final_target_ladder_unavailable')
    if not ((side=='long' and approved_stop<reviewed<float(anchor.get('tp1',plan.tp1.price))<=float(anchor.get('tp2',target)))
            or (side=='short' and approved_stop>reviewed>float(anchor.get('tp1',plan.tp1.price))>=float(anchor.get('tp2',target)))):
        raise ValueError('approved_target_ladder_invalid')
    # Preserve the reviewed ladder; only cap excess and exchange tick rounding change it.
    t1=target_leg(plan.tp1.price,'max_leveraged_tp1_gain_pct','tp1')
    t2=target_leg(target,'max_leveraged_tp2_gain_pct','tp2')
    if not (t1<=t2 if side=='long' else t1>=t2):
        raise ValueError('final_target_ladder_invalid')
    return sl,t1,t2,float(tick)


def finalized_plan(plan, *, stop, tp1, tp2, quantity, entry, loss, budget, context):
    snapshot=hashlib.sha256(json.dumps(dict(entry=entry,stop=stop,tp1=tp1,tp2=tp2,
        quantity=quantity,equity=context.equity_usdt,budget=budget),sort_keys=True).encode()).hexdigest()
    p=replace(plan,entry_allowed=True,reason_code='ok',stop_price=stop,
        tp1=replace(plan.tp1,price=tp1),tp2=replace(plan.tp2,price=tp2),
        effective_notional=quantity*entry,planned_loss_usdt=loss,trade_risk_budget_usdt=budget,
        input_snapshot_hash=snapshot,plan_hash='')
    return replace(p,plan_hash=hashlib.sha256(json.dumps(p.audit_record(),sort_keys=True).encode()).hexdigest())
