"""CORE-only bounded SL/TP reconciliation before the GPT entry gate.

Never treats GPT WAIT/REJECT as approval. No exchange writes occur here.
Keeps the approved cost/RR and leveraged-stop limits; a feasible protected
price plan replaces an over-wide structural baseline instead of discarding
the Gemini LONG/SHORT candidate before GPT can decide.
"""
from dataclasses import asdict, replace
import hashlib
import json
import math

from adaptive_exit_engine import solve_risk_capped_size


def reconcile_core_plan(plan, ctx, policy):
    def rejected(reason):
        if plan is None:
            return plan, reason
        updated=replace(plan,entry_allowed=False,reason_code=reason,plan_hash='')
        audit=updated.audit_record()
        audit.pop('plan_hash',None)
        return replace(updated,plan_hash=hashlib.sha256(json.dumps(
            audit,sort_keys=True,separators=(',',':'),ensure_ascii=False,
            default=str).encode()).hexdigest()),reason
    reason = getattr(plan, 'reason_code', 'missing_plan')
    if (plan is None or plan.stop_price is None or plan.tp1 is None or plan.tp2 is None
            or (not plan.entry_allowed and reason != 'post_cost_rr_below_minimum')):
        return rejected('unrecoverable_baseline')

    try:
        entry = float(ctx.entry_price)
        leverage = float(ctx.leverage)
        atr = float(ctx.atr)
        fee_rate = float(ctx.estimated_roundtrip_cost_rate)
        stop = float(plan.stop_price)
        tp1 = float(plan.tp1.price)
        tp2 = float(plan.tp2.price)
        required_rr = float(policy['min_post_cost_rr'])
        if not all(math.isfinite(v) for v in
                   (entry, leverage, atr, fee_rate, stop, tp1, tp2, required_rr)):
            return rejected('nonfinite_geometry')
        if min(entry, leverage, atr, required_rr) <= 0 or fee_rate < 0:
            return rejected('invalid_geometry')
        sign = 1 if ctx.side == 'long' else -1 if ctx.side == 'short' else 0
        if sign == 0 or not (sign * (entry - stop) > 0
                              and sign * (tp1 - entry) > 0
                              and sign * (tp2 - tp1) > 0):
            return rejected('invalid_ladder_direction')
        if (sign * (tp1 - entry) > entry * float(policy['max_leveraged_tp1_gain_pct']) / (100*leverage) + 1e-8
                or sign * (tp2 - entry) > entry * float(policy['max_leveraged_tp2_gain_pct']) / (100*leverage) + 1e-8):
            return rejected('target_above_leverage_cap')

        cost = entry * fee_rate
        max_leveraged_distance = entry * float(policy['max_leveraged_stop_loss_pct']) / (100*leverage)
        max_rr_distance = ((abs(tp2-entry)-cost) / required_rr) - cost
        if max_rr_distance <= 0 or max_leveraged_distance <= 0:
            return rejected('unrepresentable_rr_or_stop_cap')
        raw_distance = abs(entry-stop)
        raw_rr = (abs(tp2-entry)-cost) / (raw_distance+cost)
        if (plan.entry_allowed and raw_distance <= max_leveraged_distance * (1+1e-12)
                and raw_rr >= required_rr):
            return plan, 'unchanged_valid_tp2'

        # Do not widen or invent an ambitious target. Tighten only the stop,
        # with headroom for exchange tick rounding and entry price drift.
        distance = min(raw_distance, max_leveraged_distance*0.995,
                       max_rr_distance*0.995)
        # Reject unrealistic needle-width stops in very volatile/noisy markets.
        if distance < max(entry*0.002, atr*0.5):
            return rejected('stop_too_tight_for_market')
        new_stop = entry-sign*distance
        sizing = solve_risk_capped_size(ctx, new_stop, float(plan.configured_notional))
        if not sizing.entry_allowed or sizing.effective_notional <= 0:
            return rejected('risk_capped_sizing_failed')
        new_rr = (abs(tp2-entry)-cost) / (distance+cost)
        if new_rr < required_rr or distance > max_leveraged_distance:
            return rejected('bounded_rr_validation_failed')
        why = ('core_pre_gpt_stop_bounded' if distance < raw_distance-1e-12
               else 'core_pre_gpt_tp2_rr_aligned')
        updated = replace(
            plan, entry_allowed=True, reason_code=why,
            stop_price=new_stop,
            effective_notional=float(sizing.effective_notional),
            planned_loss_usdt=float(sizing.planned_loss_usdt),
        )
        audit = updated.audit_record()
        audit.pop('plan_hash', None)
        return replace(updated, plan_hash=hashlib.sha256(
            json.dumps(audit, sort_keys=True, separators=(',', ':'),
                       ensure_ascii=False, default=str).encode()).hexdigest()), why
    except (ValueError, TypeError, ZeroDivisionError, OverflowError):
        return rejected('reconciliation_input_invalid')
