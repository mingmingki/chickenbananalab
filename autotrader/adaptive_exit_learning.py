from __future__ import annotations

import math
import statistics

from adaptive_exit_engine import LearningExitSuggestion


def _finite_values(rows, key):
    values = []
    for row in rows:
        try:
            value = float(row.get(key))
        except (TypeError, ValueError):
            continue
        if math.isfinite(value):
            values.append(value)
    return values


def suggest_overlay(featured_lifecycles, prior_policy, *, min_samples: int = 20) -> LearningExitSuggestion:
    rows = [r for r in (featured_lifecycles or []) if isinstance(r, dict)]
    mae = _finite_values(rows, "mae_r")
    mfe = _finite_values(rows, "mfe_r")
    sample_count = min(len(mae), len(mfe))
    evidence_ids = tuple(str(r.get("trade_id") or r.get("evidence_id") or i) for i, r in enumerate(rows))
    delta = 0.0
    if sample_count:
        median_mae = statistics.median(mae)
        median_mfe = statistics.median(mfe)
        if median_mae < 0.60 and median_mfe >= 1.50:
            delta = -0.25
        elif median_mae > 0.90:
            delta = 0.15
    max_abs = abs(float(prior_policy["initial_atr_prior"]) * float(prior_policy["learning_max_modifier"]))
    delta = max(-max_abs, min(max_abs, delta))
    validations = [r.get("validation") or {} for r in rows]
    validated = bool(
        sample_count >= int(min_samples)
        and validations
        and all(v.get("oos_improvement") is True for v in validations)
        and all(v.get("adjacent_stable") is True for v in validations)
    )
    return LearningExitSuggestion(
        authority="VALIDATED" if validated else "SHADOW",
        state="VALIDATED" if validated else "SHADOW_LEARNING",
        initial_atr_delta=delta,
        initial_atr_multiplier_delta=delta,
        evidence_ids=evidence_ids,
    )
