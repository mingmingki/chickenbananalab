import json
from pathlib import Path

import learning_shadow as sh


def decision(**kw):
    base = dict(decision_id='d1', symbol='BTC/USDT:USDT', side='long',
                timestamp='2026-09-25T00:00:00+09:00', gemini_confidence=0.72,
                gpt_confidence=0.75,
                matched_patterns=[{'pattern_id':'p1','direction':'negative','state':'SHADOW_LEARNING'}],
                learner_action='HOLD_BY_LEARNING', confidence_delta=-0.05,
                live_applied=False, baseline_order_executed=True, upstream_blocked=False)
    base.update(kw)
    return base


def lifecycle(**kw):
    base = dict(trade_id='t1', symbol='BTC/USDT:USDT', side='long',
                entry_time='2026-09-25T00:00:10+09:00', exit_time='2026-09-25T01:00:00+09:00',
                net_pnl=-12.0, fee=1.0, coverage='complete')
    base.update(kw)
    return base


def test_record_decision_preserves_required_fields(tmp_path):
    row = sh.record_decision(str(tmp_path), decision())
    for key in ('decision_id','symbol','side','timestamp','gemini_confidence','gpt_confidence',
                'matched_patterns','learner_action','confidence_delta','live_applied'):
        assert key in row
    assert json.loads((tmp_path/'learning_decisions.jsonl').read_text().splitlines()[0])['decision_id'] == 'd1'


def test_shadow_hold_resolves_avoided_loss_and_deduplicates(tmp_path):
    sh.record_decision(str(tmp_path), decision())
    first = sh.resolve_counterfactuals(str(tmp_path), [lifecycle()])
    second = sh.resolve_counterfactuals(str(tmp_path), [lifecycle()])
    assert len(first) == 1 and second == []
    assert first[0]['outcome'] == 'avoided_loss'
    assert first[0]['policy_benefit_net'] == 12.0


def test_shadow_hold_winner_is_missed_profit(tmp_path):
    sh.record_decision(str(tmp_path), decision())
    row = sh.resolve_counterfactuals(str(tmp_path), [lifecycle(net_pnl=8.0)])[0]
    assert row['outcome'] == 'missed_profit'
    assert row['policy_benefit_net'] == -8.0


def test_allow_is_calibration_only(tmp_path):
    sh.record_decision(str(tmp_path), decision(learner_action='ALLOW', confidence_delta=0.03))
    row = sh.resolve_counterfactuals(str(tmp_path), [lifecycle(net_pnl=5.0)])[0]
    assert row['outcome'] == 'calibration_only'
    assert row['policy_benefit_net'] == 0.0
    assert row['actual_net_pnl'] == 5.0


def test_upstream_blocked_partial_and_future_leakage_do_not_resolve(tmp_path):
    sh.record_decision(str(tmp_path), decision(decision_id='u', upstream_blocked=True, baseline_order_executed=False))
    sh.record_decision(str(tmp_path), decision(decision_id='p'))
    assert sh.resolve_counterfactuals(str(tmp_path), [lifecycle(coverage='partial')]) == []
    # entry happened before learner decision: never attach hindsight
    assert sh.resolve_counterfactuals(str(tmp_path), [lifecycle(entry_time='2026-09-24T23:59:59+09:00')]) == []


def test_summary_builds_pattern_evidence_windows(tmp_path):
    for i, pnl in enumerate([-10.0,-5.0,2.0]):
        did = f'd{i}'
        ts = f'2026-09-25T00:0{i}:00+09:00'
        et = f'2026-09-25T00:0{i}:10+09:00'
        sh.record_decision(str(tmp_path), decision(decision_id=did, timestamp=ts))
        sh.resolve_counterfactuals(str(tmp_path), [lifecycle(trade_id=f't{i}', entry_time=et, net_pnl=pnl)])
    ev = sh.summarize_pattern_evidence(str(tmp_path))['p1']
    assert ev['sample_count'] == 3
    assert ev['resolved_count'] == 3
    assert ev['coverage'] == 1.0
    assert ev['shadow_benefit_net'] == 13.0
    assert ev['long_direction'] == 'negative'
    assert 0 <= ev['outlier_share'] <= 1

def test_recent_direction_comes_from_recent_actual_outcomes_not_stored_pattern_label(tmp_path):
    # The historical pattern label is negative, but recent completed outcomes are positive.
    for i,pnl in enumerate([5.0,4.0,3.0]):
        did=f'r{i}'
        sh.record_decision(str(tmp_path), decision(
            decision_id=did,
            timestamp=f'2026-09-25T02:0{i}:00+09:00',
            matched_patterns=[{'pattern_id':'p1','direction':'negative','state':'SHADOW_LEARNING'}],
        ))
        sh.resolve_counterfactuals(str(tmp_path), [lifecycle(
            trade_id=f'rt{i}', entry_time=f'2026-09-25T02:0{i}:10+09:00',
            exit_time=f'2026-09-25T03:0{i}:00+09:00', net_pnl=pnl,
        )])
    ev=sh.summarize_pattern_evidence(str(tmp_path))['p1']
    assert ev['long_direction']=='negative'
    assert ev['recent_direction']=='positive'
    assert ev['recent_benefit_net'] < 0
