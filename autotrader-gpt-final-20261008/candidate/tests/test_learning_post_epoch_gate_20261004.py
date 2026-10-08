import learning_control
import learning_policy

def strong_evidence(**extra):
    base={"sample_count":80,"coverage":0.95,"resolved_count":60,"shadow_benefit_net":50.0,"recent_benefit_net":10.0,"recent_direction":"negative","long_direction":"negative","outlier_share":0.1,"checkpoint_streak":3,"data_integrity_issue":False,"post_epoch_required":True,"post_epoch_sample_count":0,"post_epoch_resolved_count":0,"post_epoch_checkpoint_streak":0}
    base.update(extra); return base

def test_control_supports_validation_epoch():
    assert hasattr(learning_control, "set_validation_epoch")

def test_old_history_cannot_validate_after_epoch_reset():
    assert learning_policy.eligible_for_validation(strong_evidence()) is False

def test_post_epoch_minimums_allow_validation_when_other_evidence_is_strong():
    ev=strong_evidence(post_epoch_sample_count=20,post_epoch_resolved_count=10,post_epoch_checkpoint_streak=2)
    assert learning_policy.eligible_for_validation(ev) is True

def test_post_epoch_checkpoint_streak_starts_fresh_and_requires_two_windows(tmp_path):
    import learning_state
    ev=strong_evidence(post_epoch_sample_count=20,post_epoch_resolved_count=10,post_epoch_checkpoint_streak=0,validation_epoch_at="2026-10-04T06:20:00+00:00")
    one,_=learning_state.prepare_checkpoint_evidence(str(tmp_path),"20261004-06",{"p":ev})
    assert one["p"].get("post_epoch_checkpoint_streak") == 1
    two,_=learning_state.prepare_checkpoint_evidence(str(tmp_path),"20261004-12",{"p":ev})
    assert two["p"].get("post_epoch_checkpoint_streak") == 2
    changed=dict(ev,validation_epoch_at="2026-10-05T00:00:00+00:00")
    three,_=learning_state.prepare_checkpoint_evidence(str(tmp_path),"20261004-18",{"p":changed})
    assert three["p"].get("post_epoch_checkpoint_streak") == 1
