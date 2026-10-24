import json
import datetime as dt
from types import SimpleNamespace

import ai_strategy_review as review
import learning_control
import learning_shadow


def _append(path, row):
    with open(path, 'a', encoding='utf-8') as f:
        f.write(json.dumps(row) + '\n')


def test_validation_epoch_persists_across_live_toggle(tmp_path):
    epoch='2026-10-04T06:20:00+00:00'
    learning_control.set_validation_epoch(str(tmp_path), epoch)
    one=learning_control.get(str(tmp_path))
    assert one['live_enabled'] is False
    assert one['validation_epoch_at']=='2026-10-04T06:20:00+00:00'
    learning_control.set_live_enabled(str(tmp_path), True)
    two=learning_control.get(str(tmp_path))
    assert two['live_enabled'] is True
    assert two['validation_epoch_at']==one['validation_epoch_at']
    learning_control.set_live_enabled(str(tmp_path), False)
    assert learning_control.get(str(tmp_path))['validation_epoch_at']==one['validation_epoch_at']


def test_shadow_since_counts_only_post_epoch_decisions_and_linked_resolutions(tmp_path):
    user=str(tmp_path); p='pattern-x'
    decision_path=tmp_path/learning_shadow.DECISIONS
    cf_path=tmp_path/learning_shadow.COUNTERFACTUALS
    base={'symbol':'BTC/USDT:USDT','side':'long','learner_action':'HOLD_BY_LEARNING','confidence_delta':0.0,'live_applied':False,'matched_patterns':[{'pattern_id':p,'direction':'negative'}]}
    old=dict(base,decision_id='old',timestamp='2026-10-04T06:10:00+00:00')
    new=dict(base,decision_id='new',timestamp='2026-10-04T06:30:00+00:00')
    _append(decision_path,old); _append(decision_path,new)
    for did,t in [('old','2026-10-04T06:15:00+00:00'),('new','2026-10-04T06:40:00+00:00')]:
        _append(cf_path,{'decision_id':did,'lifecycle_id':'life-'+did,'exit_time':t,'actual_net_pnl':-2.0,'policy_benefit_net':2.0})
    all_ev=learning_shadow.summarize_pattern_evidence(user)[p]
    post=learning_shadow.summarize_pattern_evidence(user,since='2026-10-04T06:20:00+00:00')[p]
    assert all_ev['sample_count']==2 and all_ev['resolved_count']==2
    assert post['sample_count']==1 and post['resolved_count']==1


def test_checkpoint_attaches_post_epoch_counts_without_enabling_live(tmp_path, monkeypatch):
    user=str(tmp_path); epoch='2026-10-04T06:20:00+00:00'
    learning_control.set_validation_epoch(user, epoch)
    assert learning_control.get(user)['live_enabled'] is False
    monkeypatch.setattr(review.strategy_learning,'update_hypotheses',lambda *a,**k:None)
    monkeypatch.setattr(review.learning_shadow,'resolve_counterfactuals',lambda *a,**k:[])
    monkeypatch.setattr(review.exit_reentry_shadow,'resolve_links',lambda *a,**k:[])
    monkeypatch.setattr(review.exit_reentry_shadow,'recent',lambda *a,**k:[])
    monkeypatch.setattr(review.learning_exit_reentry,'ingest_resolved',lambda *a,**k:[])
    monkeypatch.setattr(review.learning_exit_reentry,'advance_shadow_state',lambda *a,**k:{})
    monkeypatch.setattr(review.learning_exit_reentry,'shadow_snapshot',lambda *a,**k:{})
    monkeypatch.setattr(review,'build_completed_lifecycles',lambda *a,**k:[])
    monkeypatch.setattr(review.strategy_learning,'latest_hypotheses',lambda *a,**k:{'p':{'hypothesis_id':'p','condition':{'dimension':'side','value':'short'},'sample_count':60,'metrics':{'count':60,'coverage_ratio':.9,'profit_factor':.5,'net_pnl':-100}}})
    cumulative={'p':{'sample_count':30,'coverage':.8,'resolved_count':24,'shadow_benefit_net':20,'recent_benefit_net':5,'recent_direction':'negative','long_direction':'negative','outlier_share':.1,'data_integrity_issue':False}}
    post={'p':{'sample_count':7,'coverage':.4,'resolved_count':3}}
    def summary(_user,since=None): return post if since else cumulative
    monkeypatch.setattr(review.learning_shadow,'summarize_pattern_evidence',summary)
    captured={}
    def prep(_user,_window,evidence): captured.update(evidence); return evidence,False
    monkeypatch.setattr(review.learning_state,'prepare_checkpoint_evidence',prep)
    monkeypatch.setattr(review.learning_state,'advance_patterns',lambda _u,e,live: captured.setdefault('_live_arg',live) or [])
    monkeypatch.setattr(review.learning_state,'load_active_state',lambda *a,**k:({},True))
    monkeypatch.setattr(review.learning_shadow,'recent_counterfactuals',lambda *a,**k:[])
    monkeypatch.setattr(review.learning_shadow,'recent_decisions',lambda *a,**k:[])
    out=review._prepare_self_learning_checkpoint(user,{},'20261004-06')
    ev=captured['p']
    assert ev['post_epoch_required'] is True
    assert ev['validation_epoch_at']==epoch
    assert ev['post_epoch_sample_count']==7
    assert ev['post_epoch_resolved_count']==3
    assert captured['_live_arg'] is False
