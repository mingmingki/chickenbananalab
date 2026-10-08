"""Integrated read-only context for daily review. It never has live trading authority."""
from __future__ import annotations

def _rate(count,total):
    try:
        n=int(total)
        return (float(count or 0)/n) if n>0 else None
    except (TypeError,ValueError):
        return None

def build_daily_completion_context(entry_summary,exit_summary,coverage,learning_progress):
    e=dict(entry_summary or {}); x=dict(exit_summary or {}); c=dict(coverage or {})
    sample=e.get("sample_count")
    horizons=e.get("horizon_summary") if isinstance(e.get("horizon_summary"),dict) else {}
    completed=c.get("canonical_completed_trades",c.get("completed_trades"))
    feature_complete=c.get("feature_complete")
    progress=list(learning_progress or [])
    return {
        "mode":"integrated_shadow_review","live_authority":False,
        "entry":{
            "sample_count":sample,"resolved_count":e.get("resolved_count"),
            "late_entry_count":e.get("late_entry_signature_count"),
            "late_entry_rate":_rate(e.get("late_entry_signature_count"),sample),
            "immediate_adverse_count":e.get("immediate_adverse_count"),
            "immediate_adverse_rate":_rate(e.get("immediate_adverse_count"),sample),
            "clean_follow_through_count":e.get("clean_follow_through_count"),
            "clean_follow_through_rate":_rate(e.get("clean_follow_through_count"),sample),
            "horizons":{str(k):dict(v or {}) for k,v in horizons.items()},
        },
        "exit":{
            "sample_count":x.get("sample_count"),"resolved_count":x.get("resolved_count"),
            "winner_counts":dict(x.get("winner_counts") or {}),
            "close_all_net_sum":x.get("close_all_net_sum"),"reduce50_net_sum":x.get("reduce50_net_sum"),
            "hold_net_sum":x.get("hold_net_sum"),
        },
        "data_quality":{
            "completed_trades":completed,"feature_complete":feature_complete,
            "partially_enriched":c.get("partially_enriched"),"unmatched_or_excluded":c.get("unmatched_or_excluded"),
            "feature_complete_rate":_rate(feature_complete,completed),
            "feature_missing_reasons":dict(c.get("feature_missing_reasons") or {}),
            "by_strategy_group":dict(c.get("by_strategy_group") or {}),
        },
        "learning":{
            "pattern_count":len(progress),
            "eligible_next_count":sum(bool(r.get("eligible_next")) for r in progress),
            "blocked_count":sum(not bool(r.get("eligible_next")) for r in progress),
            "progress":progress[:30],
        },
    }
