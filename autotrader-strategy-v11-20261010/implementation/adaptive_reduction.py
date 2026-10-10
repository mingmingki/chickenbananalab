"""Explicit risk-based sizing; never creates a trigger or sends an order."""
import math
import hashlib
import json

MIN_AI_FRACTION = .05
MAX_AI_FRACTION = .50
RISK_LEVELS = frozenset({'low', 'medium', 'high'})
CHART_POLICY = {
    'schema_version': 1,
    'weights': {'loss': .25, 'giveback': .25, 'volatility': .20, 'one_h': .20, 'five_m': .10},
    'giveback_min_peak_r': .5, 'volatility_floor_r': .25, 'volatility_range_r': 1.25,
    'profit_initial_fraction': [.10, .50], 'defense_remaining_fraction': [.25, .75],
    'structure_mfe_cumulative_cap': .50,
    'dust_policy': 'keep_minimum_residual',
}


def chart_policy_hash():
    return hashlib.sha256(json.dumps(CHART_POLICY, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def management_config_hash(base_hash):
    return hashlib.sha256(json.dumps({'base_config_hash': base_hash,
        'chart_reduction_policy_hash': chart_policy_hash()}, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def enabled(cfg):
    return getattr(cfg, 'RISK_ADAPTIVE_PARTIAL_ENABLED', False) is True


def number(value):
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and math.isfinite(value))


def valid_gemini_proposal(review):
    fraction = review.get('suggested_reduce_fraction')
    return (review.get('risk_level') in RISK_LEVELS and number(fraction)
            and (fraction == 0 or MIN_AI_FRACTION <= fraction <= MAX_AI_FRACTION))


def valid_gpt_reduction(review):
    fraction = review.get('reduce_fraction')
    return (review.get('risk_level') in RISK_LEVELS and number(fraction)
            and MIN_AI_FRACTION <= fraction <= MAX_AI_FRACTION)


def ai_target(state, fraction):
    """Absolute cumulative initial-size goal. A terminal partial fill keeps its goal.

    A fresh AI approval is required for every submission. Larger approvals can
    only finish the old goal; smaller approvals limit that attempt's amount.
    The original goal remains durable until filled; no old approval is replayed.
    """
    initial = state['initial_contracts']
    actual = state.get('actual_reduced_contracts', 0.)
    previous = state.get('adaptive_reduce_target_contracts')
    stage = state.get('reduce_stage', 0) + 1
    if (number(previous) and previous > actual
            and state.get('adaptive_reduce_target_stage') == stage):
        return min(previous, actual + initial*fraction, initial * MAX_AI_FRACTION)
    return min(actual + initial * fraction, initial * MAX_AI_FRACTION)


def goal_completed(state, pending, filled):
    goal = pending.get('adaptive_goal_contracts')
    if number(goal):
        return state.get('actual_reduced_contracts', 0.) + filled >= goal - 1e-8
    return filled >= pending['contracts'] - 1e-8


def chart_fraction(event, *, current_r, mfe_r, atr_r, weakening_1h, adverse_5m):
    """Confirmed chart facts only; missing geometry is unknown, never low risk.

    Loss depth, fraction of peak given back, volatility relative to initial R,
    and adverse structure contribute independently. Fractions vary continuously.
    Bounds constrain execution size; they are not an optimized profit claim.
    """
    if (any(not number(v) for v in (current_r, mfe_r, atr_r))
            or mfe_r < 0 or atr_r <= 0):
        return None
    loss = min(1., max(0., -current_r))
    giveback = min(1., max(0., mfe_r-current_r) / max(mfe_r, CHART_POLICY['giveback_min_peak_r']))
    volatility = min(1., max(0., (atr_r-CHART_POLICY['volatility_floor_r'])/CHART_POLICY['volatility_range_r']))
    w = CHART_POLICY['weights']
    score = min(1., w['loss']*loss + w['giveback']*giveback + w['volatility']*volatility
                + w['one_h']*bool(weakening_1h) + w['five_m']*bool(adverse_5m))
    defense = event == 'structural_derisk'
    lower, upper = CHART_POLICY['defense_remaining_fraction' if defense else 'profit_initial_fraction']
    return {'authority': 'CHART_RISK', 'risk_score': round(score, 6),
            'policy_hash': chart_policy_hash(),
            'fraction': round(lower+(upper-lower)*score, 6),
            'basis': 'remaining' if defense else 'initial', 'event': event,
            'evidence': {'loss_r': loss, 'giveback_ratio': giveback,
                         'volatility_risk': volatility,
                         'weakening_1h': bool(weakening_1h), 'adverse_5m': bool(adverse_5m)}}
