from pathlib import Path
from strategy_learning import update_hypotheses, recent_hypotheses, latest_hypotheses


def _analysis(count, pf=1.2, net=10.0, condition='tf_combo:3m+5m+1h:bearish'):
    sample = 'exploratory' if count < 20 else 'watch' if count < 50 else 'established_sample'
    return {'groups':[{'condition':condition,'dimension':'tf_combo:3m+5m+1h','value':'bearish','count':count,'sample_class':sample,'win_rate':55.0,'profit_factor':pf,'net_pnl':net,'coverage_ratio':1.0}], 'coverage':{'canonical_completed_trades':count,'lifecycle_matched':count,'feature_complete':count,'partially_enriched':0,'unmatched_or_excluded':0}}


def test_update_appends_new_version_without_rewriting_history(tmp_path):
    update_hypotheses(str(tmp_path), _analysis(22, 1.2, 10))
    p = Path(tmp_path)/'strategy_learning.jsonl'; before = p.read_text()
    update_hypotheses(str(tmp_path), _analysis(31, 1.3, 14)); after = p.read_text()
    assert after.startswith(before)
    assert len(after.splitlines()) == len(before.splitlines()) + 1
    assert recent_hypotheses(str(tmp_path), 2)[0]['version'] == 2


def test_low_sample_hypothesis_cannot_be_validated(tmp_path):
    row = update_hypotheses(str(tmp_path), _analysis(12, 2.0, 20))[0]
    assert row['status'] == 'candidate'
    assert row['execution_applied'] is False


def test_established_sample_needs_previous_stable_version(tmp_path):
    first = update_hypotheses(str(tmp_path), _analysis(52, 1.3, 20))[0]
    second = update_hypotheses(str(tmp_path), _analysis(60, 1.4, 24))[0]
    assert first['status'] == 'watch'
    assert second['status'] == 'validated_observation'


def test_malformed_last_line_is_skipped(tmp_path):
    update_hypotheses(str(tmp_path), _analysis(22))
    p = Path(tmp_path)/'strategy_learning.jsonl'
    with p.open('a') as f: f.write('{bad json\n')
    rows = recent_hypotheses(str(tmp_path), 10)
    assert len(rows) == 1
    assert len(latest_hypotheses(str(tmp_path))) == 1


def test_identical_observation_does_not_create_fake_new_learning_version(tmp_path):
    analysis=_analysis(22,1.2,10)
    first=update_hypotheses(str(tmp_path),analysis)
    second=update_hypotheses(str(tmp_path),analysis)
    assert len(first)==1
    assert second==[]
    assert len((Path(tmp_path)/'strategy_learning.jsonl').read_text().splitlines())==1
