import json
from pathlib import Path

import self_learning_replay


def test_replay_is_read_only_and_reports_shadow_eligibility(tmp_path,monkeypatch):
    # Existing learning memory is input only.
    h={'hypothesis_id':'p1','condition':{'dimension':'side','value':'short','label':'side:short'},
       'sample_count':60,'metrics':{'count':60,'coverage_ratio':.9,'profit_factor':.5,'net_pnl':-100}}
    memory=tmp_path/'strategy_learning.jsonl'
    memory.write_text(json.dumps(h)+'\n')
    before={p.name:p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    monkeypatch.setattr(self_learning_replay.trade_pattern_analysis,'analyze',lambda _:{'summary':{'completed_trades':60},'coverage':{'lifecycle_matched':60}})
    out=self_learning_replay.simulate(str(tmp_path))
    after={p.name:p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    assert before==after
    assert out['patterns_total']==1
    assert out['state_counts']['SHADOW_LEARNING']==1
    assert out['resolved_counterfactual_count']==0
    assert out['would_be_live_patterns']==[]


def test_replay_does_not_guess_missing_coverage(tmp_path,monkeypatch):
    h={'hypothesis_id':'p1','condition':{'dimension':'side','value':'short'},'sample_count':100,
       'metrics':{'count':100,'coverage_ratio':None,'profit_factor':.5,'net_pnl':-100}}
    (tmp_path/'strategy_learning.jsonl').write_text(json.dumps(h)+'\n')
    monkeypatch.setattr(self_learning_replay.trade_pattern_analysis,'analyze',lambda _:{'summary':{},'coverage':{}})
    out=self_learning_replay.simulate(str(tmp_path))
    assert out['state_counts']['DISCOVERY']==1
