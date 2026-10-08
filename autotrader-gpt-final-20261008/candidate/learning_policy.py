"""Deterministic self-learning promotion/demotion policy.

Pure functions only: no filesystem, network, config, or trading imports.
"""
from __future__ import annotations

MAX_DELTA = 0.10
MIN_SHADOW_SAMPLES = 20
MIN_VALIDATION_SAMPLES = 50
MIN_COVERAGE = 0.80
MIN_RESOLVED = 20
MAX_OUTLIER_SHARE = 0.35
MIN_CHECKPOINT_STREAK = 2
MIN_POST_EPOCH_SAMPLES = 20
MIN_POST_EPOCH_RESOLVED = 10
MIN_POST_EPOCH_CHECKPOINT_STREAK = 2


def clip_delta(value: float) -> float:
    return max(-MAX_DELTA, min(MAX_DELTA, float(value)))


def sample_state(metrics: dict) -> str:
    n = int(metrics.get("sample_count") or 0)
    return "DISCOVERY" if n < MIN_SHADOW_SAMPLES else "SHADOW_LEARNING"


def eligible_for_shadow(evidence: dict) -> bool:
    return bool(
        int(evidence.get("sample_count") or 0) >= MIN_SHADOW_SAMPLES
        and float(evidence.get("coverage") or 0.0) >= MIN_COVERAGE
        and not bool(evidence.get("data_integrity_issue"))
    )


def _direction_agrees(evidence: dict) -> bool:
    recent = evidence.get("recent_direction")
    long = evidence.get("long_direction")
    return recent in {"positive", "negative"} and recent == long


def eligible_for_validation(evidence: dict) -> bool:
    base = bool(
        int(evidence.get("sample_count") or 0) >= MIN_VALIDATION_SAMPLES
        and float(evidence.get("coverage") or 0.0) >= MIN_COVERAGE
        and int(evidence.get("resolved_count") or 0) >= MIN_RESOLVED
        and float(evidence.get("shadow_benefit_net") or 0.0) > 0.0
        and float(evidence.get("outlier_share") or 0.0) <= MAX_OUTLIER_SHARE
        and int(evidence.get("checkpoint_streak") or 0) >= MIN_CHECKPOINT_STREAK
        and not bool(evidence.get("data_integrity_issue"))
        and _direction_agrees(evidence)
    )
    if not base:
        return False
    if not bool(evidence.get("post_epoch_required")):
        return True
    return bool(
        int(evidence.get("post_epoch_sample_count") or 0) >= MIN_POST_EPOCH_SAMPLES
        and int(evidence.get("post_epoch_resolved_count") or 0) >= MIN_POST_EPOCH_RESOLVED
        and int(evidence.get("post_epoch_checkpoint_streak") or 0) >= MIN_POST_EPOCH_CHECKPOINT_STREAK
    )


def eligible_for_live(evidence: dict, live_enabled: bool, safety_ok: bool) -> bool:
    return bool(live_enabled and safety_ok and eligible_for_validation(evidence))


def should_demote(evidence: dict, live_enabled: bool = True) -> bool:
    if not live_enabled:
        return True
    if bool(evidence.get("data_integrity_issue")):
        return True
    if float(evidence.get("coverage") or 0.0) < MIN_COVERAGE:
        return True
    if bool(evidence.get("material_pf_reversal")):
        return True
    if int(evidence.get("deteriorating_checkpoints") or 0) >= 2:
        return True
    if float(evidence.get("outlier_share") or 0.0) > MAX_OUTLIER_SHARE:
        return True
    if int(evidence.get("resolved_count") or 0) >= MIN_RESOLVED and float(evidence.get("recent_benefit_net") or 0.0) <= 0.0:
        return True
    if evidence.get("recent_direction") and evidence.get("long_direction") and not _direction_agrees(evidence):
        return True
    return False


def score_contribution(evidence: dict) -> float:
    direction = evidence.get("long_direction")
    if direction not in {"positive", "negative"}:
        return 0.0
    n = max(0, int(evidence.get("sample_count") or 0))
    coverage = max(0.0, min(1.0, float(evidence.get("coverage") or 0.0)))
    reliability = min(1.0, n / 100.0) * coverage
    sign = 1.0 if direction == "positive" else -1.0
    return clip_delta(sign * MAX_DELTA * reliability)
