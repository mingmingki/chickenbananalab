"""Append-only strategy hypothesis memory. Advisory only; never mutates trading config."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import threading

_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock(user_dir: str):
    key = os.path.abspath(user_dir)
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.RLock())


def _path(user_dir: str) -> str:
    return os.path.join(user_dir, 'strategy_learning.jsonl')


def _read(user_dir: str) -> list[dict]:
    p = _path(user_dir)
    if not os.path.exists(p):
        return []
    rows = []
    with open(p, encoding='utf-8') as f:
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


def _hypothesis_id(group: dict) -> str:
    condition = {
        'dimension': group.get('dimension'),
        'value': group.get('value'),
        'condition': group.get('condition'),
    }
    raw = json.dumps(condition, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def _sample_class(group: dict) -> str:
    count = int(group.get('count') or 0)
    return 'exploratory' if count < 20 else 'watch' if count < 50 else 'established_sample'


def _direction(metrics: dict) -> int:
    net = float(metrics.get('net_pnl') or 0.0)
    pf = metrics.get('profit_factor')
    if net > 0 and (pf is None or float(pf) >= 1.0):
        return 1
    if net < 0 and pf is not None and float(pf) < 1.0:
        return -1
    return 0


def _trend(current: dict, previous: dict | None) -> str:
    if not previous:
        return 'insufficient'
    prev = previous.get('metrics') or {}
    cur_net = float(current.get('net_pnl') or 0.0)
    prev_net = float(prev.get('net_pnl') or 0.0)
    cur_pf = current.get('profit_factor')
    prev_pf = prev.get('profit_factor')
    if cur_net > prev_net and (cur_pf is None or prev_pf is None or float(cur_pf) >= float(prev_pf)):
        return 'improving'
    if cur_net < prev_net and (cur_pf is None or prev_pf is None or float(cur_pf) <= float(prev_pf)):
        return 'deteriorating'
    return 'stable'


def _status(group: dict, previous: dict | None, degraded: bool) -> str:
    count = int(group.get('count') or 0)
    if count < 20:
        return 'candidate'
    if count < 50:
        return 'watch'
    current_metrics = {'net_pnl':group.get('net_pnl'), 'profit_factor':group.get('profit_factor')}
    if previous:
        prev_metrics = previous.get('metrics') or {}
        if _direction(prev_metrics) > 0 and _direction(current_metrics) < 0:
            return 'rejected'
        if not degraded and _direction(prev_metrics) == _direction(current_metrics) != 0:
            return 'validated_observation'
    return 'watch'


def latest_hypotheses(user_dir: str) -> dict[str, dict]:
    latest = {}
    for row in _read(user_dir):
        hid = row.get('hypothesis_id')
        if not hid:
            continue
        if hid not in latest or int(row.get('version') or 0) >= int(latest[hid].get('version') or 0):
            latest[hid] = row
    return latest


def recent_hypotheses(user_dir: str, limit: int = 100) -> list[dict]:
    rows = _read(user_dir)
    return list(reversed(rows))[:max(0, int(limit))]


def update_hypotheses(user_dir: str, analysis: dict) -> list[dict]:
    os.makedirs(user_dir, exist_ok=True)
    with _lock(user_dir):
        previous_by_id = latest_hypotheses(user_dir)
        coverage = analysis.get('coverage') or {}
        degraded = bool(coverage.get('degraded')) or int(coverage.get('unmatched_or_excluded') or 0) > 0
        created = []
        for group in analysis.get('groups') or analysis.get('dimensions') or []:
            if not isinstance(group, dict) or not group.get('condition'):
                continue
            hid = _hypothesis_id(group)
            previous = previous_by_id.get(hid)
            metrics = {
                'win_rate': group.get('win_rate'),
                'profit_factor': group.get('profit_factor'),
                'net_pnl': group.get('net_pnl'),
                'count': group.get('count'),
                'coverage_ratio': group.get('coverage_ratio'),
            }
            if (previous and int(previous.get('sample_count') or 0) == int(group.get('count') or 0)
                    and (previous.get('metrics') or {}) == metrics
                    and previous.get('data_quality') == ('degraded' if degraded else 'normal')):
                continue
            row = {
                'hypothesis_id': hid,
                'version': int((previous or {}).get('version') or 0) + 1,
                'condition': {'dimension':group.get('dimension'),'value':group.get('value'),'label':group.get('condition')},
                'sample_count': int(group.get('count') or 0),
                'metrics': metrics,
                'previous_metrics': (previous or {}).get('metrics'),
                'previous_version': (previous or {}).get('version'),
                'trend': _trend(metrics, previous),
                'sample_class': _sample_class(group),
                'status': _status(group, previous, degraded),
                'gemini_assessment': None,
                'gpt_assessment': None,
                'consensus': 'insufficient',
                'recommended_action_text': '관찰 유지; 실매매 설정 자동 변경 없음',
                'execution_applied': False,
                'data_quality': 'degraded' if degraded else 'normal',
                'created_at': dt.datetime.now().isoformat(timespec='seconds'),
                'observed_through': analysis.get('generated_at'),
            }
            with open(_path(user_dir), 'a', encoding='utf-8') as f:
                f.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n')
            previous_by_id[hid] = row
            created.append(row)
        return created
