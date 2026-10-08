"""Append-only learning decisions and counterfactual resolution."""
from __future__ import annotations

import datetime as dt
import json
import os

DECISIONS = 'learning_decisions.jsonl'
COUNTERFACTUALS = 'learning_counterfactuals.jsonl'
MAX_MATCH_SECONDS = 15 * 60


def _path(user_dir, name):
    return os.path.join(user_dir, name)


def _read(path):
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def _append(path, row):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'a', encoding='utf-8') as f:
        f.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n')
        f.flush(); os.fsync(f.fileno())


KST = dt.timezone(dt.timedelta(hours=9))


def _time(value):
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace('Z', '+00:00')) if value else None
    except (TypeError, ValueError):
        return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=KST)
    return parsed.astimezone(KST)


def record_decision(user_dir: str, record: dict) -> dict:
    required = ('decision_id','symbol','side','timestamp','learner_action','confidence_delta','live_applied')
    missing = [k for k in required if k not in record]
    if missing:
        raise ValueError('missing_learning_decision_fields:' + ','.join(missing))
    row = dict(record)
    row.setdefault('matched_patterns', [])
    row.setdefault('baseline_order_executed', False)
    row.setdefault('upstream_blocked', False)
    _append(_path(user_dir, DECISIONS), row)
    return row


def _match(decision, lifecycles):
    if decision.get('upstream_blocked') or not decision.get('baseline_order_executed'):
        return None
    when = _time(decision.get('timestamp'))
    if when is None:
        return None
    candidates = []
    for life in lifecycles:
        if life.get('coverage') != 'complete':
            continue
        if life.get('symbol') != decision.get('symbol') or life.get('side') != decision.get('side'):
            continue
        entry = _time(life.get('entry_time'))
        exit_ = _time(life.get('exit_time'))
        if entry is None or exit_ is None or exit_ < entry:
            continue
        delta = (entry - when).total_seconds()
        if delta < 0 or delta > MAX_MATCH_SECONDS:
            continue
        candidates.append((delta, life))
    return min(candidates, key=lambda x: x[0])[1] if candidates else None


def resolve_counterfactuals(user_dir: str, lifecycles: list[dict]) -> list[dict]:
    decisions = _read(_path(user_dir, DECISIONS))
    existing = _read(_path(user_dir, COUNTERFACTUALS))
    seen = {(r.get('decision_id'), r.get('lifecycle_id')) for r in existing}
    used_decisions = {r.get('decision_id') for r in existing if r.get('decision_id')}
    used_lifecycles = {r.get('lifecycle_id') for r in existing if r.get('lifecycle_id')}
    created = []
    for d in decisions:
        if d.get('decision_id') in used_decisions:
            continue
        available = [life for life in lifecycles if life.get('trade_id') not in used_lifecycles]
        life = _match(d, available)
        if not life:
            continue
        key = (d.get('decision_id'), life.get('trade_id'))
        if key in seen:
            continue
        actual = float(life.get('net_pnl') or 0.0)
        action = d.get('learner_action')
        if action == 'HOLD_BY_LEARNING' and not d.get('live_applied'):
            benefit = -actual
            outcome = 'avoided_loss' if actual < 0 else 'missed_profit' if actual > 0 else 'flat_avoided'
        else:
            benefit = 0.0
            outcome = 'calibration_only'
        row = {
            'decision_id': d.get('decision_id'),
            'lifecycle_id': life.get('trade_id'),
            'symbol': life.get('symbol'),
            'side': life.get('side'),
            'decision_time': d.get('timestamp'),
            'entry_time': life.get('entry_time'),
            'exit_time': life.get('exit_time'),
            'matched_patterns': d.get('matched_patterns') or [],
            'learner_action': action,
            'live_applied': bool(d.get('live_applied')),
            'actual_net_pnl': actual,
            'policy_benefit_net': benefit,
            'outcome': outcome,
            'resolved_at': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'),
        }
        _append(_path(user_dir, COUNTERFACTUALS), row)
        seen.add(key)
        used_decisions.add(d.get('decision_id'))
        used_lifecycles.add(life.get('trade_id'))
        created.append(row)
    return created


def _pattern_id(item):
    return item.get('pattern_id') if isinstance(item, dict) else item


def _pattern_direction(item):
    return item.get('direction') if isinstance(item, dict) else None


def summarize_pattern_evidence(user_dir: str, since=None) -> dict[str, dict]:
    decisions = _read(_path(user_dir, DECISIONS))
    resolved = _read(_path(user_dir, COUNTERFACTUALS))
    since_dt = _time(since) if since is not None else None
    if since_dt is not None:
        if since_dt.tzinfo is None:
            since_dt = since_dt.replace(tzinfo=dt.timezone.utc)
        else:
            since_dt = since_dt.astimezone(dt.timezone.utc)
        filtered = []
        for d in decisions:
            t = _time(d.get('timestamp'))
            if t is None:
                continue
            if t.tzinfo is None:
                t = t.replace(tzinfo=dt.timezone.utc)
            else:
                t = t.astimezone(dt.timezone.utc)
            if t >= since_dt:
                filtered.append(d)
        decisions = filtered
    decision_ids = {d.get('decision_id') for d in decisions}
    by_decision = {r.get('decision_id'): r for r in resolved if r.get('decision_id') in decision_ids}
    out = {}
    for d in decisions:
        for item in d.get('matched_patterns') or []:
            pid = _pattern_id(item)
            if not pid:
                continue
            agg = out.setdefault(pid, {'sample_count':0,'resolved':[],'directions':[]})
            agg['sample_count'] += 1
            direction = _pattern_direction(item)
            if direction in ('positive','negative'):
                agg['directions'].append(direction)
            r = by_decision.get(d.get('decision_id'))
            if r:
                agg['resolved'].append(r)
    result = {}
    now = dt.datetime.now(dt.timezone.utc)
    for pid, agg in out.items():
        rows = agg['resolved']
        benefits = [float(r.get('policy_benefit_net') or 0.0) for r in rows]
        sample = agg['sample_count']
        resolved_count = len(rows)
        long_direction = None
        if agg['directions']:
            long_direction = max(set(agg['directions']), key=agg['directions'].count)
        elif rows:
            avg = sum(float(r.get('actual_net_pnl') or 0.0) for r in rows) / len(rows)
            long_direction = 'positive' if avg > 0 else 'negative' if avg < 0 else None
        recent_rows = []
        for r in rows[-30:]:
            t = _time(r.get('exit_time'))
            if t is None:
                continue
            if t.tzinfo is None:
                t = t.replace(tzinfo=dt.timezone.utc)
            if (now - t.astimezone(dt.timezone.utc)).total_seconds() <= 14*86400:
                recent_rows.append(r)
        if not recent_rows and rows:
            recent_rows = rows[-30:]
        recent_benefit = sum(float(r.get('policy_benefit_net') or 0.0) for r in recent_rows)
        recent_actual_net = sum(float(r.get('actual_net_pnl') or 0.0) for r in recent_rows)
        recent_direction = 'positive' if recent_actual_net > 0 else 'negative' if recent_actual_net < 0 else None
        denom = sum(abs(x) for x in benefits)
        outlier = (max([abs(x) for x in benefits], default=0.0) / denom) if denom else 0.0
        result[pid] = {
            'sample_count': sample,
            'coverage': (resolved_count / sample) if sample else 0.0,
            'resolved_count': resolved_count,
            'shadow_benefit_net': sum(benefits),
            'recent_benefit_net': recent_benefit,
            'recent_direction': recent_direction,
            'long_direction': long_direction,
            'outlier_share': outlier,
            'checkpoint_streak': 0,
            'deteriorating_checkpoints': 0,
            'material_pf_reversal': False,
            'data_integrity_issue': False,
        }
    return result


def recent_decisions(user_dir: str, limit: int = 50) -> list[dict]:
    return list(reversed(_read(_path(user_dir, DECISIONS))))[:max(0, int(limit))]


def recent_counterfactuals(user_dir: str, limit: int = 50) -> list[dict]:
    return list(reversed(_read(_path(user_dir, COUNTERFACTUALS))))[:max(0, int(limit))]
