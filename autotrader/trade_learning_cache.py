"""Cached coordinator for deterministic read-only trade analysis."""
from __future__ import annotations
import glob, json, os, threading, time
import trade_pattern_analysis, strategy_learning

_LOCKS={}; _GUARD=threading.Lock(); _MEM={}
SOURCE_PATTERNS=('trades_log.jsonl*','fast_live_trades.jsonl*','gpt_shadow_log.jsonl*','market_structure_log.jsonl*','candle_finality_log.jsonl*','position_ai_log.jsonl*','exit_reentry_shadow.jsonl*','low_follow_through_shadow.jsonl*','entry_tf_daily_backfill.jsonl*')
CACHE_NAME='trade_learning_analysis_cache.json'

def _lock(user_dir):
    key=os.path.abspath(user_dir)
    with _GUARD: return _LOCKS.setdefault(key,threading.RLock())

def _cache_path(user_dir): return os.path.join(user_dir,CACHE_NAME)

def source_fingerprint(user_dir):
    out=[]
    for pattern in SOURCE_PATTERNS:
        for path in sorted(glob.glob(os.path.join(user_dir,pattern))):
            if path.endswith('.lock'): continue
            try: st=os.stat(path)
            except OSError: continue
            out.append([os.path.basename(path),int(st.st_size),int(st.st_mtime_ns)])
    return out

def _read_disk(user_dir):
    path=_cache_path(user_dir)
    if not os.path.exists(path): return None
    try:
        with open(path,encoding='utf-8') as f: return json.load(f)
    except (OSError,json.JSONDecodeError): return None

def get_cached(user_dir):
    key=os.path.abspath(user_dir)
    payload=_MEM.get(key) or _read_disk(user_dir)
    if not payload: return None
    payload=dict(payload)
    payload['stale']=payload.get('source_fingerprint') != source_fingerprint(user_dir)
    return payload

def run_analysis(user_dir):
    os.makedirs(user_dir,exist_ok=True)
    with _lock(user_dir):
        fingerprint = source_fingerprint(user_dir)
        cached = _MEM.get(os.path.abspath(user_dir)) or _read_disk(user_dir)
        if cached and cached.get('source_fingerprint') == fingerprint:
            reused = dict(cached)
            reused['stale'] = False
            reused['reused_cache'] = True
            _MEM[os.path.abspath(user_dir)] = dict(cached)
            return reused
        analysis=trade_pattern_analysis.analyze(user_dir)
        hypotheses=strategy_learning.update_hypotheses(user_dir,analysis)
        payload={'analysis':analysis,'hypotheses_appended':len(hypotheses),'source_fingerprint':fingerprint,'generated_at':analysis.get('generated_at'),'stale':False,'reused_cache':False}
        path=_cache_path(user_dir); tmp=path+'.tmp'
        with open(tmp,'w',encoding='utf-8') as f:
            json.dump(payload,f,ensure_ascii=False,allow_nan=False,separators=(',',':'))
        os.replace(tmp,path)
        _MEM[os.path.abspath(user_dir)]=payload
        return dict(payload)
