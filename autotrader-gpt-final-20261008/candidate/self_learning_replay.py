"""Read-only historical self-learning simulation. Never writes user state."""
from __future__ import annotations

import ai_strategy_review
import learning_policy
import learning_shadow
import strategy_learning
import trade_pattern_analysis


def simulate(user_dir: str) -> dict:
    analysis = trade_pattern_analysis.analyze(user_dir)
    hypotheses = strategy_learning.latest_hypotheses(user_dir)
    shadow = learning_shadow.summarize_pattern_evidence(user_dir)
    evidence = ai_strategy_review._merge_learning_evidence(hypotheses, shadow)
    counts = {name:0 for name in ('DISCOVERY','SHADOW_LEARNING','VALIDATED','LIVE_BOUNDED','REJECTED')}
    would_live=[]
    eligible_samples=0
    resolved=0
    for pid, ev in evidence.items():
        resolved += int(ev.get('resolved_count') or 0)
        if not learning_policy.eligible_for_shadow(ev):
            state='DISCOVERY'
        else:
            eligible_samples += 1
            if learning_policy.eligible_for_validation(ev):
                state='VALIDATED'
                if learning_policy.eligible_for_live(ev, live_enabled=True, safety_ok=True):
                    would_live.append(pid)
            else:
                state='SHADOW_LEARNING'
        counts[state]+=1
    return {
        'patterns_total':len(evidence),
        'state_counts':counts,
        'eligible_shadow_patterns':eligible_samples,
        'resolved_counterfactual_count':resolved,
        'would_be_live_patterns':would_live,
        'analysis_summary':analysis.get('summary') or {},
        'coverage':analysis.get('coverage') or {},
    }
