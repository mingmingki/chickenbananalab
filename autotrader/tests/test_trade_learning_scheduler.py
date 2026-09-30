import datetime as dt
from types import SimpleNamespace

import ai_strategy_review_log
import trade_learning_scheduler as scheduler

KST = dt.timezone(dt.timedelta(hours=9))


def test_restart_same_window_does_not_duplicate_report(tmp_path, monkeypatch):
    calls=[]
    monkeypatch.setattr(scheduler.ai_strategy_review, 'generate_review', lambda *a,**k: calls.append(1) or {'status':'complete','review_window_id':k.get('review_window_id')})
    cfg=SimpleNamespace(user_dir=str(tmp_path))
    now=dt.datetime(2026,9,24,18,1,tzinfo=KST)
    one=scheduler.run_due_review_once(str(tmp_path),cfg,now=now,new_completed_trades=1)
    two=scheduler.run_due_review_once(str(tmp_path),cfg,now=now+dt.timedelta(minutes=9),new_completed_trades=1)
    assert one['status'] == 'complete'
    assert two['status'] == 'duplicate_window'
    assert len(calls) == 1
    assert len(ai_strategy_review_log.recent(str(tmp_path),10)) == 1


def test_no_new_sample_skips_expensive_ai_calls(tmp_path, monkeypatch):
    calls=[]
    monkeypatch.setattr(scheduler.ai_strategy_review, 'generate_review', lambda *a,**k: calls.append(1) or {})
    cfg=SimpleNamespace(user_dir=str(tmp_path))
    result=scheduler.run_due_review_once(str(tmp_path),cfg,now=dt.datetime(2026,9,24,12,5,tzinfo=KST),new_completed_trades=0,material_late_change=False)
    assert result['status'] == 'no_new_sample'
    assert calls == []


def test_automatic_due_window_is_previous_completed_window():
    start,end,window_id=scheduler.completed_window_bounds(dt.datetime(2026,9,24,18,1,tzinfo=KST))
    assert start == dt.datetime(2026,9,24,12,0,tzinfo=KST)
    assert end == dt.datetime(2026,9,24,18,0,tzinfo=KST)
    assert window_id == '20260924-12'

def test_existing_ai_review_backfills_missing_learning_checkpoint(tmp_path, monkeypatch):
    cfg=SimpleNamespace(user_dir=str(tmp_path))
    now=dt.datetime(2026,9,25,9,0,tzinfo=KST)
    start,end,window_id=scheduler.completed_window_bounds(now)
    ai_strategy_review_log.append_report(str(tmp_path),{
        'review_window_id':window_id,'window_start':start.isoformat(),'window_end':end.isoformat(),
        'status':'complete','window_stats':{'completed_trades':3}
    })
    calls=[]
    monkeypatch.setattr(scheduler.learning_state,'has_checkpoint',lambda *_:False)
    monkeypatch.setattr(scheduler.ai_strategy_review,'ensure_self_learning_checkpoint',lambda *a,**k: calls.append(k.get('window_id') or a[-1]) or {'state_counts':{'SHADOW_LEARNING':4}})
    result=scheduler.run_due_review_once(str(tmp_path),cfg,now=now,new_completed_trades=3)
    assert result['status']=='duplicate_window'
    assert calls==[window_id]
    assert result['self_learning_checkpoint']['state_counts']['SHADOW_LEARNING']==4


def test_existing_no_sample_review_does_not_create_learning_checkpoint(tmp_path, monkeypatch):
    cfg=SimpleNamespace(user_dir=str(tmp_path))
    now=dt.datetime(2026,9,25,9,0,tzinfo=KST)
    start,end,window_id=scheduler.completed_window_bounds(now)
    ai_strategy_review_log.append_report(str(tmp_path),{
        'review_window_id':window_id,'window_start':start.isoformat(),'window_end':end.isoformat(),
        'status':'no_new_sample','window_stats':{'completed_trades':0}
    })
    calls=[]
    monkeypatch.setattr(scheduler.learning_state,'has_checkpoint',lambda *_:False)
    monkeypatch.setattr(scheduler.ai_strategy_review,'ensure_self_learning_checkpoint',lambda *a,**k: calls.append(1) or {})
    result=scheduler.run_due_review_once(str(tmp_path),cfg,now=now,new_completed_trades=0)
    assert result['status']=='duplicate_window'
    assert calls==[]
