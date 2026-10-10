"""Same-request held analysis reuse; no persistent cache or order authority."""
import copy
import hashlib
import json
import math
import re
import time

import adaptive_reduction
import strategy_authority

MAX_REVIEW_AGE_SECONDS = 60


def with_evidence(summary, rendered):
    """Replace only our complete evidence block; preserve all unrelated facts."""
    pattern = (r'\n\[POSITION_MANAGEMENT_EVIDENCE\]\n[^\n]*\n'
               r'Consider observed peak/giveback, prior reductions and confirmed structure together\. '
               r'Unknown evidence is not zero\. Respect existing order and protection guards\.\n')
    return re.sub(pattern, '', summary) + rendered


def management_facts(summary):
    """Only omit a complete NEW-entry price contract from held GPT management.

    Actual held SL/TP is supplied separately. Reversal entry still uses the
    original summary and its separate entry gate, never this projection.
    """
    return re.sub(r'\[AI_EXIT_EXECUTION_CONTRACT\]\n.*?유효 가격을 계산할 수 없으면 reject하세요\.\n',
                  '', summary, flags=re.DOTALL)


def _fingerprint(cfg, symbol, tfs, summary, position, protection):
    if not position or not isinstance(protection, dict):
        return None
    data = dict(symbol=symbol, tfs=list(tfs), summary=summary, position=position,
                protection=protection, adaptive=adaptive_reduction.enabled(cfg),
                min_confidence=getattr(cfg, 'MIN_CONFIDENCE', .6))
    try:
        encoded = json.dumps(data, sort_keys=True, separators=(',', ':'), allow_nan=False)
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(encoded.encode()).hexdigest()


def _valid(cfg, review):
    return (isinstance(review, dict) and strategy_authority.held_review_valid(cfg, review)
            and isinstance(review.get('reasoning'), str)
            and (not adaptive_reduction.enabled(cfg) or adaptive_reduction.valid_gemini_proposal(review)))


def bind(decision, cfg, symbol, tfs, summary, position, protection, started_at):
    review = decision.get('position_review')
    fingerprint = _fingerprint(cfg, symbol, tfs, summary, position, protection)
    if not strategy_authority.core_ai(cfg) or not fingerprint or not _valid(cfg, review):
        return
    decision['_held_review'] = copy.deepcopy(review)
    decision['_held_review_snapshot'] = dict(created_at=started_at, fingerprint=fingerprint)


def reuse(decision, cfg, symbol, tfs, summary, position, protection, *, now=None):
    if not strategy_authority.core_ai(cfg):
        return None
    snapshot = decision.get('_held_review_snapshot') or {}
    review = decision.get('_held_review')
    created = snapshot.get('created_at')
    if not adaptive_reduction.number(created):
        return None
    age = (time.time() if now is None else now) - created
    if not math.isfinite(age) or not 0 <= age <= MAX_REVIEW_AGE_SECONDS:
        return None
    fingerprint = _fingerprint(cfg, symbol, tfs, summary, position, protection)
    if not fingerprint or snapshot.get('fingerprint') != fingerprint or not _valid(cfg, review):
        return None
    return copy.deepcopy(review)
