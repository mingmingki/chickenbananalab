import pytest

import learning_policy as lp


def evidence(**overrides):
    base = dict(
        sample_count=50,
        coverage=0.80,
        resolved_count=20,
        shadow_benefit_net=1.0,
        recent_benefit_net=1.0,
        recent_direction='positive',
        long_direction='positive',
        outlier_share=0.35,
        checkpoint_streak=2,
        deteriorating_checkpoints=0,
        material_pf_reversal=False,
        data_integrity_issue=False,
    )
    base.update(overrides)
    return base


@pytest.mark.parametrize('n,expected', [(19,'DISCOVERY'),(20,'SHADOW_LEARNING'),(49,'SHADOW_LEARNING'),(50,'SHADOW_LEARNING')])
def test_sample_state_boundaries(n, expected):
    assert lp.sample_state({'sample_count': n}) == expected


def test_validation_requires_exact_thresholds_and_agreement():
    assert not lp.eligible_for_validation(evidence(coverage=0.799))
    assert lp.eligible_for_validation(evidence(coverage=0.80))
    assert not lp.eligible_for_validation(evidence(resolved_count=19))
    assert lp.eligible_for_validation(evidence(resolved_count=20))
    assert not lp.eligible_for_validation(evidence(recent_direction='negative'))
    assert not lp.eligible_for_validation(evidence(outlier_share=0.350001))
    assert lp.eligible_for_validation(evidence(outlier_share=0.35))
    assert not lp.eligible_for_validation(evidence(checkpoint_streak=1))


def test_live_requires_operator_enable_and_safety():
    ev = evidence()
    assert not lp.eligible_for_live(ev, live_enabled=False, safety_ok=True)
    assert not lp.eligible_for_live(ev, live_enabled=True, safety_ok=False)
    assert lp.eligible_for_live(ev, live_enabled=True, safety_ok=True)


def test_demotion_rules_are_conservative():
    assert lp.should_demote(evidence(), live_enabled=False)
    assert lp.should_demote(evidence(resolved_count=20, recent_benefit_net=0.0))
    assert not lp.should_demote(evidence(resolved_count=19, recent_benefit_net=0.0))
    assert lp.should_demote(evidence(material_pf_reversal=True))
    assert lp.should_demote(evidence(coverage=0.799))
    assert lp.should_demote(evidence(deteriorating_checkpoints=2))
    assert lp.should_demote(evidence(outlier_share=0.350001))
    assert not lp.should_demote(evidence(), live_enabled=True)


def test_score_and_clip_are_bounded():
    assert lp.clip_delta(2.0) == 0.10
    assert lp.clip_delta(-2.0) == -0.10
    assert lp.score_contribution(evidence(shadow_benefit_net=10.0)) > 0
    assert lp.score_contribution(evidence(shadow_benefit_net=-10.0, recent_direction='negative', long_direction='negative')) < 0
    assert abs(lp.score_contribution(evidence(shadow_benefit_net=9999.0))) <= 0.10

def test_shadow_eligibility_requires_sample_coverage_and_clean_data():
    assert not lp.eligible_for_shadow({'sample_count':19,'coverage':1.0,'data_integrity_issue':False})
    assert not lp.eligible_for_shadow({'sample_count':20,'coverage':0.799,'data_integrity_issue':False})
    assert not lp.eligible_for_shadow({'sample_count':20,'coverage':0.80,'data_integrity_issue':True})
    assert lp.eligible_for_shadow({'sample_count':20,'coverage':0.80,'data_integrity_issue':False})
