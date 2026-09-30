import copy, math, pytest
from adaptive_exit_policy import (
    AdaptiveExitPolicyError, production_adaptive_exit_policy,
    validate_adaptive_exit_policy, policy_sha256,
)

def test_policy_rejects_unknown_fields_and_out_of_bounds_modifiers():
    policy = production_adaptive_exit_policy(); policy['unknown'] = 1
    with pytest.raises(AdaptiveExitPolicyError): validate_adaptive_exit_policy(policy)
    policy = production_adaptive_exit_policy(); policy['gemini_max_risk_reduction'] = 1.5
    with pytest.raises(AdaptiveExitPolicyError): validate_adaptive_exit_policy(policy)

def test_policy_hash_is_deterministic_and_validated_copy_is_immutable_by_value():
    a = production_adaptive_exit_policy(); b = copy.deepcopy(a)
    assert policy_sha256(a) == policy_sha256(b)
    v = validate_adaptive_exit_policy(a); a['initial_atr_prior'] = 9.0
    assert v['initial_atr_prior'] != 9.0

def test_policy_rejects_non_finite_numeric_values():
    p = production_adaptive_exit_policy(); p['trailing_atr_max'] = math.inf
    with pytest.raises(AdaptiveExitPolicyError): validate_adaptive_exit_policy(p)
