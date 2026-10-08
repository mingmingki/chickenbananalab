def test_context_combines_entry_exit_coverage_and_learning_without_live_authority():
    from daily_completion_context import build_daily_completion_context
    entry={"mode":"shadow_only","live_authority":False,"sample_count":100,"resolved_count":90,
        "late_entry_signature_count":12,"immediate_adverse_count":8,"clean_follow_through_count":40,
        "horizon_summary":{"30":{"avg_mfe_r":0.20,"avg_mae_r":0.31},"60":{"avg_mfe_r":0.42,"avg_mae_r":0.38},
        "120":{"avg_mfe_r":0.55,"avg_mae_r":0.44}}}
    exit3={"mode":"shadow_only","live_authority":False,"sample_count":79,"resolved_count":79,
           "winner_counts":{"CLOSE_ALL":38,"REDUCE_50":0,"HOLD":41}}
    coverage={"canonical_completed_trades":582,"feature_complete":13,"partially_enriched":569,"unmatched_or_excluded":0}
    learning=[{"pattern_id":"side:short","state":"SHADOW_LEARNING","next_state":"VALIDATED","eligible_next":False,
               "blockers":[{"code":"sample_count","label":"표본","actual":42,"required":50}]}]
    out=build_daily_completion_context(entry,exit3,coverage,learning)
    assert out["live_authority"] is False
    assert out["entry"]["late_entry_rate"]==0.12
    assert out["entry"]["immediate_adverse_rate"]==0.08
    assert out["entry"]["horizons"]["60"]["avg_mfe_r"]==0.42
    assert out["exit"]["winner_counts"]["HOLD"]==41
    assert out["data_quality"]["feature_complete_rate"]==13/582
    assert out["learning"]["blocked_count"]==1

def test_context_never_converts_missing_measurement_to_zero():
    from daily_completion_context import build_daily_completion_context
    out=build_daily_completion_context({}, {}, {}, [])
    assert out["entry"]["late_entry_rate"] is None
    assert out["entry"]["immediate_adverse_rate"] is None
    assert out["data_quality"]["feature_complete_rate"] is None
