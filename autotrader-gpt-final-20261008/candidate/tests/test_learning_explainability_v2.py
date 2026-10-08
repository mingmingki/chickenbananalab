def test_shadow_validation_blockers_are_explained_from_policy_thresholds():
    import learning_explainability
    ev={"sample_count":42,"coverage":0.95,"resolved_count":15,"shadow_benefit_net":12.0,"outlier_share":0.10,
        "checkpoint_streak":1,"recent_direction":"positive","long_direction":"positive","data_integrity_issue":False}
    out=learning_explainability.explain_pattern("SHADOW_LEARNING",ev,live_enabled=False)
    assert out["next_state"]=="VALIDATED"
    assert {"sample_count","resolved_count","checkpoint_streak"} <= {b["code"] for b in out["blockers"]}
    assert out["eligible_next"] is False

def test_validated_explains_live_switch_without_mutating_policy():
    import learning_explainability
    ev={"sample_count":80,"coverage":0.95,"resolved_count":50,"shadow_benefit_net":20.0,"recent_benefit_net":5.0,
        "outlier_share":0.10,"checkpoint_streak":3,"recent_direction":"positive","long_direction":"positive",
        "data_integrity_issue":False}
    out=learning_explainability.explain_pattern("VALIDATED",ev,live_enabled=False)
    assert out["next_state"]=="LIVE_BOUNDED"
    assert any(b["code"]=="live_switch" for b in out["blockers"])
    assert out["eligible_next"] is False
