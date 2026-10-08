import datetime as dt
from types import SimpleNamespace

import ai_strategy_review as review
import learning_control
import learning_state


def evidence(**kw):
    base=dict(dimension='side',value='short',sample_count=60,coverage=.9,resolved_count=20,
              shadow_benefit_net=10.0,recent_benefit_net=4.0,recent_direction='negative',
              long_direction='negative',outlier_share=.1,profit_factor=.5,
              data_integrity_issue=False)
    base.update(kw); return base


def test_checkpoint_streak_requires_two_consecutive_windows_and_duplicate_is_idempotent(tmp_path):
    e1,dup1=learning_state.prepare_checkpoint_evidence(str(tmp_path),'20260924-12',{'p1':evidence()})
    e2,dup2=learning_state.prepare_checkpoint_evidence(str(tmp_path),'20260924-18',{'p1':evidence()})
    again,dup3=learning_state.prepare_checkpoint_evidence(str(tmp_path),'20260924-18',{'p1':evidence()})
    assert dup1 is False and e1['p1']['checkpoint_streak']==1
    assert dup2 is False and e2['p1']['checkpoint_streak']==2
    assert dup3 is True and again['p1']['checkpoint_streak']==2
    lines=(tmp_path/'learning_checkpoints.jsonl').read_text().splitlines()
    assert len(lines)==2


def test_two_checkpoints_validate_then_operator_enable_can_promote_live(tmp_path):
    user=str(tmp_path)
    e1,_=learning_state.prepare_checkpoint_evidence(user,'20260924-12',{'p1':evidence()})
    t1=learning_state.advance_patterns(user,e1,False)
    assert t1[-1]['new_state']=='SHADOW_LEARNING'
    e2,_=learning_state.prepare_checkpoint_evidence(user,'20260924-18',{'p1':evidence()})
    t2=learning_state.advance_patterns(user,e2,False)
    assert t2[-1]['new_state']=='VALIDATED'
    learning_control.set_live_enabled(user,True)
    e3,_=learning_state.prepare_checkpoint_evidence(user,'20260925-00',{'p1':evidence()})
    t3=learning_state.advance_patterns(user,e3,True)
    assert t3[-1]['new_state']=='LIVE_BOUNDED'


def test_ai_review_runs_after_deterministic_learning_checkpoint(tmp_path,monkeypatch):
    events=[]
    analysis={'summary':{'completed_trades':0},'coverage':{},'groups':[],'trades':[]}
    monkeypatch.setattr(review.trade_pattern_analysis,'analyze',lambda _:analysis)
    monkeypatch.setattr(review.strategy_learning,'update_hypotheses',lambda *_:[])
    monkeypatch.setattr(review.strategy_learning,'latest_hypotheses',lambda _:{})
    monkeypatch.setattr(review,'_prepare_self_learning_checkpoint',lambda *a,**k: events.append('checkpoint') or {'state_counts':{},'transitions':[]})
    monkeypatch.setattr(review.gemini_analyzer,'review_strategy_report',lambda *a,**k: events.append('gemini') or {'proposed_operator_actions':['promote p1'],'confidence':.9})
    monkeypatch.setattr(review.openai_analyzer,'review_strategy_report',lambda *a,**k: events.append('gpt') or {'proposed_operator_actions':['promote p1'],'confidence':.9})
    start=dt.datetime(2026,9,24,12,tzinfo=dt.timezone(dt.timedelta(hours=9)))
    result=review.generate_review(str(tmp_path),SimpleNamespace(),start,start+dt.timedelta(hours=6),review_window_id='20260924-12')
    assert result['status']=='complete'
    assert events[0]=='checkpoint'
    assert events.count('checkpoint')==1
    assert result['self_learning']['transitions']==[]


def test_merge_evidence_uses_pattern_specific_coverage_not_global_degraded(monkeypatch,tmp_path):
    hypotheses={'p1':{'hypothesis_id':'p1','condition':{'dimension':'side','value':'short','label':'side:short'},
                       'sample_count':60,'metrics':{'count':60,'coverage_ratio':.9,'profit_factor':.5,'net_pnl':-100},
                       'data_quality':'degraded'}}
    shadow={'p1':{'sample_count':25,'coverage':.8,'resolved_count':20,'shadow_benefit_net':10,
                  'recent_benefit_net':4,'recent_direction':'negative','long_direction':'negative',
                  'outlier_share':.1,'data_integrity_issue':False}}
    merged=review._merge_learning_evidence(hypotheses,shadow)['p1']
    assert merged['sample_count']==60
    assert merged['coverage']==.9
    assert merged['resolved_count']==20
    assert merged['data_integrity_issue'] is False
    assert merged['dimension']=='side' and merged['value']=='short'

def test_manual_ai_review_is_read_only_and_does_not_create_learning_checkpoint(tmp_path,monkeypatch):
    events=[]
    analysis={'summary':{'completed_trades':0},'coverage':{},'groups':[],'trades':[]}
    monkeypatch.setattr(review.trade_pattern_analysis,'analyze',lambda _:analysis)
    monkeypatch.setattr(review.strategy_learning,'latest_hypotheses',lambda _:{})
    monkeypatch.setattr(review,'_prepare_self_learning_checkpoint',lambda *a,**k: events.append('checkpoint') or {'transitions':['unexpected']})
    monkeypatch.setattr(review.gemini_analyzer,'review_strategy_report',lambda *a,**k:{'observations':['g'],'confidence':.7})
    monkeypatch.setattr(review.openai_analyzer,'review_strategy_report',lambda *a,**k:{'observations':['o'],'confidence':.8})
    start=dt.datetime(2026,9,25,6,tzinfo=dt.timezone(dt.timedelta(hours=9)))
    result=review.generate_review(str(tmp_path),SimpleNamespace(),start,start+dt.timedelta(hours=6),review_window_id='manual-20260925-062300')
    assert result['status']=='complete'
    assert events==[]
    assert result['self_learning']['transitions']==[]
    assert not (tmp_path/'learning_checkpoints.jsonl').exists()
