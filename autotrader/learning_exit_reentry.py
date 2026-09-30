"""Shadow-only learning evidence for AI exits and same-side re-entry churn."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os

SAMPLES_LOG = 'learning_exit_reentry_samples.jsonl'
STATE_LOG = 'learning_exit_reentry_state.jsonl'
VALID_STATES = {'DISCOVERY', 'SHADOW_LEARNING', 'VALIDATED_SHADOW', 'REJECTED'}


def _path(user_dir, name):
    return os.path.join(user_dir, name)


def _read(path):
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, encoding='utf-8') as stream:
        for line in stream:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def _append(path, row):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'a', encoding='utf-8') as stream:
        stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n')
        stream.flush(); os.fsync(stream.fileno())


def _latest_samples(user_dir):
    latest = {}
    for row in _read(_path(user_dir, SAMPLES_LOG)):
        if row.get('exit_id'):
            latest[row['exit_id']] = row
    return latest


def _direction(row):
    churn = row.get('churn_cycle_net')
    if churn is not None:
        try:
            churn = float(churn)
            return 'positive' if churn > 0 else 'negative' if churn < 0 else 'flat'
        except (TypeError, ValueError):
            pass
    outcome = row.get('analytical_outcome') or row.get('outcome')
    if outcome in ('exit_saved_loss', 'reentry_profitable'):
        return 'positive'
    if outcome in ('exit_missed_recovery', 'reentry_loss'):
        return 'negative'
    return None


def _pattern_id(row):
    assessment = row.get('assessment') or 'unknown'
    correction = '1' if row.get('correction_active') else '0'
    regime = row.get('market_regime') or 'unknown'
    return f"exit:{row.get('symbol')}:{row.get('side')}:{assessment}:corr={correction}:regime={regime}"


def _sample_from_row(row):
    exit_id = str(row.get('exit_id') or '')
    if not exit_id:
        return None
    outcome = row.get('analytical_outcome') or 'unresolved'
    sample = {
        'exit_id': exit_id,
        'exit_reason': row.get('final_close_reason') or row.get('exit_reason'),
        'assessment': row.get('assessment'),
        'correction_active': row.get('correction_active'),
        'symbol': row.get('symbol'),
        'side': row.get('side'),
        'market_regime': row.get('market_regime'),
        'reentry_within_30m': bool(row.get('reentry_within_30m')),
        'reentry_within_60m': bool(row.get('reentry_within_60m')),
        'reentry_within_120m': bool(row.get('reentry_within_120m')),
        'outcome': outcome,
        'actual_exit_lifecycle_id': row.get('actual_exit_lifecycle_id'),
        'actual_exit_lifecycle_net': row.get('actual_exit_lifecycle_net'),
        'next_lifecycle_id': row.get('next_lifecycle_id'),
        'next_lifecycle_net': row.get('next_lifecycle_net'),
        'churn_cycle_net': row.get('churn_cycle_net'),
        'exit_time': row.get('exit_time'),
        'resolved': outcome != 'unresolved',
    }
    sample['observed_direction'] = _direction(sample)
    sample['pattern_id'] = _pattern_id(sample)
    revision_raw = '|'.join(str(sample.get(k)) for k in (
        'exit_id','actual_exit_lifecycle_id','next_lifecycle_id','outcome','churn_cycle_net'
    ))
    sample['revision_id'] = hashlib.sha256(revision_raw.encode()).hexdigest()[:24]
    sample['recorded_at'] = dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')
    return sample


def ingest_resolved(user_dir, resolved_exit_rows):
    latest = _latest_samples(user_dir)
    created = []
    for raw in resolved_exit_rows or []:
        sample = _sample_from_row(raw)
        if sample is None:
            continue
        prior = latest.get(sample['exit_id'])
        if prior and prior.get('revision_id') == sample.get('revision_id'):
            continue
        _append(_path(user_dir, SAMPLES_LOG), sample)
        latest[sample['exit_id']] = sample
        created.append(sample)
    return created


def recent_samples(user_dir, limit=50):
    rows = list(_latest_samples(user_dir).values())
    rows.sort(key=lambda r: str(r.get('exit_time') or ''), reverse=True)
    return rows[:max(0, int(limit))]


def summarize_evidence(user_dir):
    grouped = {}
    for row in _latest_samples(user_dir).values():
        pid = row.get('pattern_id')
        if not pid:
            continue
        agg = grouped.setdefault(pid, {'rows': []})
        agg['rows'].append(row)
    result = {}
    for pid, agg in grouped.items():
        rows = agg['rows']
        resolved = [r for r in rows if r.get('resolved')]
        churn_values = [float(r['churn_cycle_net']) for r in resolved if r.get('churn_cycle_net') is not None]
        churn_net = sum(churn_values)
        direction = 'positive' if churn_net > 0 else 'negative' if churn_net < 0 else None
        result[pid] = {
            'sample_count': len(rows),
            'resolved_count': len(resolved),
            'coverage': (len(resolved) / len(rows)) if rows else 0.0,
            'churn_cycle_net': churn_net,
            'recent_direction': direction,
            'positive_count': sum(r.get('observed_direction') == 'positive' for r in resolved),
            'negative_count': sum(r.get('observed_direction') == 'negative' for r in resolved),
        }
    return result


def load_shadow_state(user_dir):
    state = {}
    for row in _read(_path(user_dir, STATE_LOG)):
        pid = row.get('pattern_id')
        new_state = row.get('new_state')
        if pid and new_state in VALID_STATES:
            state[pid] = {
                'state': new_state,
                'reason': row.get('reason'),
                'evidence': row.get('evidence') or {},
                'created_at': row.get('created_at'),
            }
    return state


def _target_state(ev):
    sample_count = int(ev.get('sample_count') or 0)
    resolved_count = int(ev.get('resolved_count') or 0)
    coverage = float(ev.get('coverage') or 0.0)
    if sample_count >= 50 and resolved_count >= 20 and coverage >= 0.8:
        return 'VALIDATED_SHADOW'
    if sample_count >= 20:
        return 'SHADOW_LEARNING'
    return 'DISCOVERY'


def advance_shadow_state(user_dir):
    evidence = summarize_evidence(user_dir)
    current = load_shadow_state(user_dir)
    for pid in sorted(evidence):
        target = _target_state(evidence[pid])
        old = (current.get(pid) or {}).get('state')
        if old == target:
            continue
        row = {
            'pattern_id': pid,
            'old_state': old,
            'new_state': target,
            'reason': 'shadow_evidence_update',
            'evidence': evidence[pid],
            'created_at': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'),
        }
        _append(_path(user_dir, STATE_LOG), row)
        current[pid] = {
            'state': target, 'reason': row['reason'],
            'evidence': evidence[pid], 'created_at': row['created_at'],
        }
    return current


def shadow_snapshot(user_dir):
    state = load_shadow_state(user_dir)
    counts = {name: 0 for name in VALID_STATES}
    for row in state.values():
        if row.get('state') in counts:
            counts[row['state']] += 1
    return {
        'mode': 'shadow_only',
        'live_authority': False,
        'state_counts': counts,
        'evidence': summarize_evidence(user_dir),
        'recent_samples': recent_samples(user_dir, 20),
    }
