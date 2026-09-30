"""Append-only storage for advisory six-hour strategy review reports."""
from __future__ import annotations
import json, os, threading

_LOCKS={}; _GUARD=threading.Lock()
def _path(user_dir): return os.path.join(user_dir,'ai_review_reports.jsonl')
def _lock(user_dir):
    key=os.path.abspath(user_dir)
    with _GUARD: return _LOCKS.setdefault(key, threading.RLock())

def _read(user_dir):
    path=_path(user_dir)
    if not os.path.exists(path): return []
    out=[]
    with open(path,encoding='utf-8') as f:
        for line in f:
            try: row=json.loads(line)
            except json.JSONDecodeError: continue
            if isinstance(row,dict): out.append(row)
    return out

def append_report(user_dir, report):
    os.makedirs(user_dir,exist_ok=True)
    with _lock(user_dir):
        with open(_path(user_dir),'a',encoding='utf-8') as f:
            f.write(json.dumps(report,ensure_ascii=False,allow_nan=False)+'\n')
    return report

def recent(user_dir, limit=100): return list(reversed(_read(user_dir)))[:max(0,int(limit))]
def has_window(user_dir, window_id): return any(r.get('review_window_id')==window_id for r in _read(user_dir))
