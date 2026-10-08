"""Fail-open six-hour advisory review scheduler."""
from __future__ import annotations
import datetime as dt, threading, time
import ai_strategy_review, ai_strategy_review_log, learning_state
from trade_learning_lifecycle import build_completed_lifecycles

KST=dt.timezone(dt.timedelta(hours=9))

def window_bounds(now=None):
    now=(now or dt.datetime.now(KST)).astimezone(KST)
    start=now.replace(hour=(now.hour//6)*6,minute=0,second=0,microsecond=0)
    return start,start+dt.timedelta(hours=6),start.strftime('%Y%m%d-%H')

def completed_window_bounds(now=None):
    current_start, _, _ = window_bounds(now)
    start = current_start - dt.timedelta(hours=6)
    end = current_start
    return start, end, start.strftime('%Y%m%d-%H')

def _parse(value):
    if not value: return None
    try: x=dt.datetime.fromisoformat(str(value).replace('Z','+00:00'))
    except (TypeError,ValueError): return None
    return x.replace(tzinfo=KST) if x.tzinfo is None else x.astimezone(KST)

def _count_completed(user_dir,start,end):
    n=0
    for row in build_completed_lifecycles(user_dir):
        stamp=_parse(row.get('exit_time'))
        if stamp is not None and start <= stamp < end: n+=1
    return n

def run_due_review_once(user_dir,cfg,*,now=None,new_completed_trades=None,material_late_change=False):
    start,end,window_id=completed_window_bounds(now)
    if ai_strategy_review_log.has_window(user_dir,window_id):
        existing = next((r for r in ai_strategy_review_log.recent(user_dir, 100)
                         if r.get('review_window_id') == window_id), {})
        result = {'status':'duplicate_window','review_window_id':window_id}
        completed = int((existing.get('window_stats') or {}).get('completed_trades') or 0)
        if completed > 0 and not learning_state.has_checkpoint(user_dir, window_id):
            try:
                result['self_learning_checkpoint'] = ai_strategy_review.ensure_self_learning_checkpoint(user_dir, window_id=window_id)
            except Exception as exc:
                result['self_learning_checkpoint'] = {'status':'error','error':type(exc).__name__}
        return result
    if new_completed_trades is None:
        new_completed_trades=_count_completed(user_dir,start,end)
    if int(new_completed_trades or 0)==0 and not material_late_change:
        report={'review_window_id':window_id,'window_start':start.isoformat(),'window_end':end.isoformat(),'status':'no_new_sample','window_stats':{'completed_trades':0},'gemini':{'status':'skipped'},'gpt':{'status':'skipped'},'consensus':{'status':'no_new_sample','agreements':[],'disagreements':[]},'created_at':dt.datetime.now(KST).isoformat(timespec='seconds')}
        return ai_strategy_review_log.append_report(user_dir,report)
    try:
        report=ai_strategy_review.generate_review(user_dir,cfg,start,end,review_window_id=window_id)
    except Exception as exc:
        report={'review_window_id':window_id,'window_start':start.isoformat(),'window_end':end.isoformat(),'status':'error','error':type(exc).__name__,'created_at':dt.datetime.now(KST).isoformat(timespec='seconds')}
    return ai_strategy_review_log.append_report(user_dir,report)

def start_review_scheduler(context_provider, stop_event, interval_seconds=60):
    def _loop():
        while not stop_event.is_set():
            try:
                contexts=context_provider() or []
                if not isinstance(contexts,(list,tuple,set)): contexts=[contexts]
                for ctx in contexts:
                    if ctx is None: continue
                    user_dir=getattr(ctx,'dir',None) or getattr(getattr(ctx,'cfg',None),'user_dir',None)
                    cfg=getattr(ctx,'cfg',None)
                    if user_dir and cfg: run_due_review_once(user_dir,cfg)
            except Exception:
                pass
            stop_event.wait(interval_seconds)
    thread=threading.Thread(target=_loop,name='trade-learning-review',daemon=True)
    thread.start(); return thread
