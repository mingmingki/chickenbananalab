from __future__ import annotations
import copy, hashlib, json, math

class AdaptiveExitPolicyError(ValueError): pass

_POLICY = {
    'schema_version': 1,
    'initial_atr_min': 1.5,
    'initial_atr_prior': 3.5,
    'initial_atr_max': 5.0,
    'trailing_atr_min': 2.0,
    'trailing_atr_prior': 6.0,
    'trailing_atr_max': 8.0,
    'profit_lock_r_min': 0.5,
    'profit_lock_r_prior': 1.0,
    'profit_lock_r_max': 1.5,
    'tp1_r_min': 0.75,
    'tp1_r_prior': 1.5,
    'tp1_r_max': 2.5,
    'tp2_r_min': 1.5,
    'tp2_r_prior': 3.0,
    'tp2_r_max': 5.0,
    'tp1_fraction': 0.25,
    'tp2_fraction': 0.25,
    'runner_fraction': 0.50,
    'min_post_cost_rr': 1.10,
    'gemini_max_risk_reduction': 0.50,
    'learning_max_modifier': 0.20,
    'reduction_cooldown_seconds': 900,
    'noise_buffer_atr_fraction': 0.25,
}

_RANGES = {
    'initial_atr_min': (0.5, 6.0), 'initial_atr_prior': (0.5, 8.0), 'initial_atr_max': (1.0, 10.0),
    'trailing_atr_min': (0.5, 10.0), 'trailing_atr_prior': (1.0, 12.0), 'trailing_atr_max': (1.0, 15.0),
    'profit_lock_r_min': (0.1, 3.0), 'profit_lock_r_prior': (0.1, 4.0), 'profit_lock_r_max': (0.1, 5.0),
    'tp1_r_min': (0.1, 5.0), 'tp1_r_prior': (0.1, 8.0), 'tp1_r_max': (0.1, 10.0),
    'tp2_r_min': (0.5, 10.0), 'tp2_r_prior': (0.5, 12.0), 'tp2_r_max': (0.5, 15.0),
    'tp1_fraction': (0.05, 0.8), 'tp2_fraction': (0.05, 0.8), 'runner_fraction': (0.05, 0.9),
    'min_post_cost_rr': (0.5, 5.0), 'gemini_max_risk_reduction': (0.0, 1.0),
    'learning_max_modifier': (0.0, 0.5), 'reduction_cooldown_seconds': (60, 86400),
    'noise_buffer_atr_fraction': (0.0, 2.0),
}

def production_adaptive_exit_policy() -> dict:
    return copy.deepcopy(_POLICY)

def validate_adaptive_exit_policy(value: object) -> dict:
    if not isinstance(value, dict) or set(value) != set(_POLICY):
        raise AdaptiveExitPolicyError('adaptive_exit_policy schema mismatch')
    if type(value.get('schema_version')) is not int or value['schema_version'] != 1:
        raise AdaptiveExitPolicyError('schema_version invalid')
    for key, (lo, hi) in _RANGES.items():
        v = value.get(key)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(float(v)) or not lo <= float(v) <= hi:
            raise AdaptiveExitPolicyError(f'{key} out of bounds')
    if not (value['initial_atr_min'] <= value['initial_atr_prior'] <= value['initial_atr_max']):
        raise AdaptiveExitPolicyError('initial atr ordering invalid')
    if not (value['trailing_atr_min'] <= value['trailing_atr_prior'] <= value['trailing_atr_max']):
        raise AdaptiveExitPolicyError('trailing atr ordering invalid')
    if abs(value['tp1_fraction'] + value['tp2_fraction'] + value['runner_fraction'] - 1.0) > 1e-9:
        raise AdaptiveExitPolicyError('target fractions must sum to 1')
    return copy.deepcopy(value)

def policy_sha256(policy: dict) -> str:
    valid = validate_adaptive_exit_policy(policy)
    blob = json.dumps(valid, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    return hashlib.sha256(blob.encode()).hexdigest()
