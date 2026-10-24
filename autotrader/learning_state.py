"""Append-only self-learning state and deterministic replay."""
from __future__ import annotations

import datetime as dt
import json
import os
import tempfile

import learning_policy

STATE_LOG = 'learning_state.jsonl'
SNAPSHOT = 'learning_active_snapshot.json'
VALID_STATES = {'DISCOVERY','SHADOW_LEARNING','VALIDATED','LIVE_BOUNDED','REJECTED'}


def _log_path(user_dir: str) -> str:
    return os.path.join(user_dir, STATE_LOG)


def _snapshot_path(user_dir: str) -> str:
    return os.path.join(user_dir, SNAPSHOT)


def append_transition(user_dir, pattern_id, old_state, new_state, reason, evidence):
    if new_state not in VALID_STATES:
        raise ValueError('invalid_learning_state')
    os.makedirs(user_dir, exist_ok=True)
    row = {
        'pattern_id': str(pattern_id),
        'old_state': old_state,
        'new_state': new_state,
        'reason': str(reason),
        'evidence': dict(evidence or {}),
        'created_at': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'),
    }
    with open(_log_path(user_dir), 'a', encoding='utf-8') as f:
        f.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n')
        f.flush(); os.fsync(f.fileno())
    return row


def _replay_with_quality(user_dir: str):
    p = _log_path(user_dir)
    state = {}
    malformed = 0
    if not os.path.exists(p):
        return state, malformed
    with open(p, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                malformed += 1
                continue
            if not isinstance(row, dict) or not row.get('pattern_id') or row.get('new_state') not in VALID_STATES:
                malformed += 1
                continue
            state[row['pattern_id']] = {
                'state': row['new_state'],
                'reason': row.get('reason'),
                'evidence': row.get('evidence') or {},
                'created_at': row.get('created_at'),
            }
    return state, malformed


def replay_state(user_dir: str):
    return _replay_with_quality(user_dir)[0]


def write_snapshot(user_dir, state):
    os.makedirs(user_dir, exist_ok=True)
    log_path = _log_path(user_dir)
    payload = {
        'version': 1,
        'log_size': os.path.getsize(log_path) if os.path.exists(log_path) else 0,
        'state': state,
    }
    target = _snapshot_path(user_dir)
    fd, tmp = tempfile.mkstemp(prefix='.learning_snapshot.', dir=user_dir, text=True)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False, allow_nan=False)
            f.flush(); os.fsync(f.fileno())
        os.replace(tmp, target)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return payload


def load_active_state(user_dir):
    replayed, malformed = _replay_with_quality(user_dir)
    safe = malformed == 0
    snap_path = _snapshot_path(user_dir)
    log_size = os.path.getsize(_log_path(user_dir)) if os.path.exists(_log_path(user_dir)) else 0
    try:
        with open(snap_path, encoding='utf-8') as f:
            snap = json.load(f)
        if snap.get('version') == 1 and int(snap.get('log_size', -1)) == log_size and snap.get('state') == replayed:
            return replayed, safe
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        pass
    write_snapshot(user_dir, replayed)
    return replayed, safe


def _target_state(current: str | None, ev: dict, live_enabled: bool) -> tuple[str, str]:
    if current == 'REJECTED':
        if ev.get('new_evidence_epoch'):
            return learning_policy.sample_state(ev), 'new_evidence_epoch'
        return 'REJECTED', 'rejected_stable'
    if current == 'LIVE_BOUNDED':
        if learning_policy.should_demote(ev, live_enabled=live_enabled):
            return 'SHADOW_LEARNING', 'live_demotion'
        return 'LIVE_BOUNDED', 'live_stable'
    if current == 'VALIDATED':
        if learning_policy.should_demote(ev, live_enabled=True):
            return 'SHADOW_LEARNING', 'validation_demotion'
        if learning_policy.eligible_for_live(ev, live_enabled=live_enabled, safety_ok=not bool(ev.get('data_integrity_issue'))):
            return 'LIVE_BOUNDED', 'live_eligible'
        return 'VALIDATED', 'validated_stable'
    if current == 'SHADOW_LEARNING':
        if learning_policy.eligible_for_validation(ev):
            return 'VALIDATED', 'validation_eligible'
        return 'SHADOW_LEARNING', 'shadow_stable'
    if current in (None, 'DISCOVERY'):
        if learning_policy.eligible_for_shadow(ev):
            return 'SHADOW_LEARNING', 'shadow_eligible'
        return 'DISCOVERY', 'initial_evidence' if current is None else 'discovery_stable'
    return 'DISCOVERY', 'initial_evidence'


def advance_patterns(user_dir, evidence_by_pattern, live_enabled):
    state, _safe = load_active_state(user_dir)
    transitions = []
    for pattern_id in sorted(evidence_by_pattern):
        ev = dict(evidence_by_pattern[pattern_id] or {})
        current = (state.get(pattern_id) or {}).get('state')
        target, reason = _target_state(current, ev, bool(live_enabled))
        if current != target:
            row = append_transition(user_dir, pattern_id, current, target, reason, ev)
            transitions.append(row)
            state[pattern_id] = {'state': target, 'reason': reason, 'evidence': ev, 'created_at': row['created_at']}
    write_snapshot(user_dir, state)
    return transitions


def reconcile_shadow_only_dimension_coverage(user_dir: str, evidence_by_pattern: dict) -> dict:
    state,safe=load_active_state(user_dir)
    if not safe: return {"promoted":[],"reason":"unsafe_state"}
    promoted=[]; allowed={"symbol","side","hour_bucket","weekday","strategy_group"}
    for pid in sorted(evidence_by_pattern or {}):
        ev=dict(evidence_by_pattern[pid] or {}); current=(state.get(pid) or {}).get("state")
        if current != "DISCOVERY" or ev.get("dimension") not in allowed: continue
        if not learning_policy.eligible_for_shadow(ev): continue
        row=append_transition(user_dir,pid,"DISCOVERY","SHADOW_LEARNING","dimension_coverage_semantics_fix",ev)
        state[pid]={"state":"SHADOW_LEARNING","reason":row["reason"],"evidence":ev,"created_at":row["created_at"]}
        promoted.append(pid)
    if promoted: write_snapshot(user_dir,state)
    return {"promoted":promoted,"reason":"shadow_only_dimension_coverage_reconciled","live_authority":False}


def recent_transitions(user_dir: str, limit: int = 50) -> list[dict]:
    path = _log_path(user_dir)
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, encoding='utf-8') as f:
        for line in f:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict) and row.get('pattern_id'):
                rows.append(row)
    return list(reversed(rows))[:max(0, int(limit))]

CHECKPOINT_LOG = 'learning_checkpoints.jsonl'


def _checkpoint_path(user_dir: str) -> str:
    return os.path.join(user_dir, CHECKPOINT_LOG)


def has_checkpoint(user_dir: str, window_id: str) -> bool:
    return any(row.get('window_id') == window_id for row in _checkpoint_rows(user_dir))

def _checkpoint_rows(user_dir: str) -> list[dict]:
    path = _checkpoint_path(user_dir)
    if not os.path.exists(path):
        return []
    rows=[]
    with open(path, encoding='utf-8') as f:
        for line in f:
            try:
                row=json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row,dict) and row.get('window_id') and isinstance(row.get('evidence'),dict):
                rows.append(row)
    return rows


def _window_time(window_id: str):
    try:
        return dt.datetime.strptime(window_id, '%Y%m%d-%H').replace(tzinfo=dt.timezone.utc)
    except (TypeError, ValueError):
        return None


def _validation_core(ev: dict) -> bool:
    return bool(
        int(ev.get('sample_count') or 0) >= learning_policy.MIN_VALIDATION_SAMPLES
        and float(ev.get('coverage') or 0.0) >= learning_policy.MIN_COVERAGE
        and int(ev.get('resolved_count') or 0) >= learning_policy.MIN_RESOLVED
        and float(ev.get('shadow_benefit_net') or 0.0) > 0.0
        and float(ev.get('outlier_share') or 0.0) <= learning_policy.MAX_OUTLIER_SHARE
        and not bool(ev.get('data_integrity_issue'))
        and ev.get('recent_direction') in ('positive','negative')
        and ev.get('recent_direction') == ev.get('long_direction')
    )


def prepare_checkpoint_evidence(user_dir: str, window_id: str, evidence_by_pattern: dict[str,dict]):
    os.makedirs(user_dir, exist_ok=True)
    rows=_checkpoint_rows(user_dir)
    for row in rows:
        if row.get('window_id') == window_id:
            return {k:dict(v) for k,v in row.get('evidence',{}).items()}, True
    prev=rows[-1] if rows else None
    prev_ev=(prev or {}).get('evidence') or {}
    now_t=_window_time(window_id)
    prev_t=_window_time((prev or {}).get('window_id'))
    consecutive=bool(now_t and prev_t and (now_t-prev_t).total_seconds()==6*3600)
    enriched={}
    for pid in sorted(evidence_by_pattern):
        cur=dict(evidence_by_pattern[pid] or {})
        old=dict(prev_ev.get(pid) or {})
        current_core=_validation_core(cur)
        previous_core=_validation_core(old)
        same_direction=(cur.get('long_direction') in ('positive','negative') and cur.get('long_direction')==old.get('long_direction'))
        if current_core:
            streak=(int(old.get('checkpoint_streak') or 0)+1) if consecutive and previous_core and same_direction else 1
        else:
            streak=0
        cur_recent=float(cur.get('recent_benefit_net') or 0.0)
        old_recent=float(old.get('recent_benefit_net') or 0.0)
        deteriorating=(int(old.get('deteriorating_checkpoints') or 0)+1) if consecutive and old and cur_recent < old_recent else 0
        pf=cur.get('profit_factor'); old_pf=old.get('profit_factor')
        material=False
        try:
            if pf is not None and old_pf is not None:
                pf=float(pf); old_pf=float(old_pf)
                if cur.get('long_direction')=='positive' and old_pf >= 1.0 and pf < 1.0:
                    material=True
                if cur.get('long_direction')=='negative' and old_pf < 1.0 and pf >= 1.0:
                    material=True
        except (TypeError,ValueError):
            material=True
        if bool(cur.get('post_epoch_required')):
            epoch = cur.get('validation_epoch_at')
            old_epoch = old.get('validation_epoch_at')
            post_core = bool(
                current_core
                and int(cur.get('post_epoch_sample_count') or 0) >= learning_policy.MIN_POST_EPOCH_SAMPLES
                and int(cur.get('post_epoch_resolved_count') or 0) >= learning_policy.MIN_POST_EPOCH_RESOLVED
            )
            old_post_core = bool(
                previous_core
                and bool(old.get('post_epoch_required'))
                and old_epoch == epoch
                and int(old.get('post_epoch_sample_count') or 0) >= learning_policy.MIN_POST_EPOCH_SAMPLES
                and int(old.get('post_epoch_resolved_count') or 0) >= learning_policy.MIN_POST_EPOCH_RESOLVED
            )
            if post_core:
                post_streak = (int(old.get('post_epoch_checkpoint_streak') or 0) + 1) if (consecutive and old_post_core) else 1
            else:
                post_streak = 0
            cur['post_epoch_checkpoint_streak'] = post_streak
        else:
            cur.pop('post_epoch_checkpoint_streak', None)
        cur['checkpoint_streak']=streak
        cur['deteriorating_checkpoints']=deteriorating
        cur['material_pf_reversal']=material
        enriched[pid]=cur
    row={'window_id':window_id,'created_at':dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'),'evidence':enriched}
    with open(_checkpoint_path(user_dir),'a',encoding='utf-8') as f:
        f.write(json.dumps(row,ensure_ascii=False,allow_nan=False)+'\n'); f.flush(); os.fsync(f.fileno())
    return enriched, False
