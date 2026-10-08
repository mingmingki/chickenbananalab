import datetime as dt

def test_compact_payload_contains_integrated_context(monkeypatch,tmp_path):
    import ai_strategy_review
    monkeypatch.setattr(ai_strategy_review.strategy_learning,"latest_hypotheses",lambda _u:{})
    monkeypatch.setattr(ai_strategy_review.entry_counterfactual_shadow,"summary",lambda _u,_n:{
        "mode":"shadow_only","live_authority":False,"sample_count":10,"resolved_count":8,
        "late_entry_signature_count":2,"immediate_adverse_count":1,"clean_follow_through_count":4,
        "horizon_summary":{"60":{"avg_mfe_r":0.5,"avg_mae_r":0.25}}})
    monkeypatch.setattr(ai_strategy_review.exit_reentry_shadow,"recent",lambda _u,_n:[])
    monkeypatch.setattr(ai_strategy_review.exit_reentry_shadow,"summarize_three_way",lambda _r:{
        "mode":"shadow_only","live_authority":False,"sample_count":0,"resolved_count":0,"winner_counts":{}})
    analysis={"trades":[],"groups":[],"summary":{},"coverage":{"canonical_completed_trades":10,"feature_complete":2}}
    self_learning={"evidence":{},"state_counts":{"SHADOW_LEARNING":1}}
    start=dt.datetime(2026,10,6,0,0,tzinfo=dt.timezone(dt.timedelta(hours=9)))
    payload=ai_strategy_review._compact_payload(str(tmp_path),start,start+dt.timedelta(hours=6),analysis,self_learning)
    assert "integrated_trade_context" in payload
    assert payload["integrated_trade_context"]["entry"]["late_entry_rate"]==0.2
    assert payload["integrated_trade_context"]["live_authority"] is False
