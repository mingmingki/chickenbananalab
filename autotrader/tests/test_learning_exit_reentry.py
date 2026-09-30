import json

import pytest

import learning_exit_reentry as ler


def _row(**overrides):
    row = {
        'exit_id':'exit-1',
        'symbol':'XRP/USDT:USDT',
        'side':'long',
        'final_close_reason':'position_ai_close_all',
        'assessment':'invalidated',
        'correction_active':True,
        'market_regime':'bullish',
        'reentry_within_30m':False,
        'reentry_within_60m':True,
        'reentry_within_120m':True,
        'analytical_outcome':'reentry_loss',
        'actual_exit_lifecycle_id':'life-a',
        'actual_exit_lifecycle_net':26.02,
        'next_lifecycle_id':'life-b',
        'next_lifecycle_net':-35.29,
        'churn_cycle_net':-9.27,
        'exit_time':'2026-09-25T10:32:08',
    }
    row.update(overrides)
    return row


def test_ingest_extracts_exit_reentry_features(tmp_path):
    created = ler.ingest_resolved(str(tmp_path), [_row()])
    assert len(created) == 1
    sample = ler.recent_samples(str(tmp_path), 1)[0]
    assert sample['exit_reason'] == 'position_ai_close_all'
    assert sample['assessment'] == 'invalidated'
    assert sample['correction_active'] is True
    assert sample['symbol'] == 'XRP/USDT:USDT'
    assert sample['side'] == 'long'
    assert sample['reentry_within_60m'] is True
    assert sample['outcome'] == 'reentry_loss'
    assert sample['next_lifecycle_net'] == pytest.approx(-35.29)
    assert sample['churn_cycle_net'] == pytest.approx(-9.27)


def test_ingest_is_idempotent_and_unresolved_can_be_revised(tmp_path):
    unresolved = _row(
        analytical_outcome='unresolved', next_lifecycle_id=None,
        next_lifecycle_net=None, churn_cycle_net=None,
        reentry_within_30m=False, reentry_within_60m=False, reentry_within_120m=False,
    )
    assert len(ler.ingest_resolved(str(tmp_path), [unresolved])) == 1
    size1 = (tmp_path / ler.SAMPLES_LOG).stat().st_size
    assert ler.ingest_resolved(str(tmp_path), [unresolved]) == []
    assert (tmp_path / ler.SAMPLES_LOG).stat().st_size == size1

    assert len(ler.ingest_resolved(str(tmp_path), [_row()])) == 1
    latest = ler.recent_samples(str(tmp_path), 10)
    assert len(latest) == 1
    assert latest[0]['resolved'] is True
    assert latest[0]['next_lifecycle_id'] == 'life-b'


def test_summary_uses_lifecycle_churn_net_not_close_only(tmp_path):
    ler.ingest_resolved(str(tmp_path), [_row(churn_cycle_net=-9.27)])
    summary = ler.summarize_evidence(str(tmp_path))
    assert summary
    evidence = next(iter(summary.values()))
    assert evidence['sample_count'] == 1
    assert evidence['resolved_count'] == 1
    assert evidence['churn_cycle_net'] == pytest.approx(-9.27)
    assert evidence['recent_direction'] == 'negative'
    assert evidence['coverage'] == pytest.approx(1.0)


def test_profitable_and_losing_reentry_have_opposite_direction(tmp_path):
    ler.ingest_resolved(str(tmp_path), [
        _row(exit_id='e-loss', churn_cycle_net=-12.0, next_lifecycle_net=-20.0),
        _row(exit_id='e-win', analytical_outcome='reentry_profitable', churn_cycle_net=8.0,
             next_lifecycle_id='life-c', next_lifecycle_net=4.0),
    ])
    rows = {r['exit_id']: r for r in ler.recent_samples(str(tmp_path), 10)}
    assert rows['e-loss']['observed_direction'] == 'negative'
    assert rows['e-win']['observed_direction'] == 'positive'


def test_shadow_state_can_never_be_live_bounded(tmp_path):
    rows = [
        _row(exit_id=f'e{i}', next_lifecycle_id=f'n{i}', churn_cycle_net=-1.0,
             next_lifecycle_net=-1.0)
        for i in range(80)
    ]
    ler.ingest_resolved(str(tmp_path), rows)
    state = ler.advance_shadow_state(str(tmp_path))
    assert state
    assert all(v['state'] in {'DISCOVERY','SHADOW_LEARNING','VALIDATED_SHADOW','REJECTED'} for v in state.values())
    assert all(v['state'] != 'LIVE_BOUNDED' for v in state.values())


def test_checkpoint_integration_has_zero_trading_authority(monkeypatch, tmp_path):
    import ai_strategy_review
    import exit_reentry_shadow
    import trade_pattern_analysis

    monkeypatch.setattr(trade_pattern_analysis, 'analyze', lambda *_: {
        'trades': [], 'groups': [], 'coverage': {},
    })
    monkeypatch.setattr(ai_strategy_review.strategy_learning, 'update_hypotheses', lambda *a, **k: None)
    monkeypatch.setattr(ai_strategy_review.strategy_learning, 'latest_hypotheses', lambda *a, **k: {})
    monkeypatch.setattr(ai_strategy_review.learning_shadow, 'resolve_counterfactuals', lambda *a, **k: [])
    monkeypatch.setattr(ai_strategy_review.learning_shadow, 'summarize_pattern_evidence', lambda *a, **k: {})
    monkeypatch.setattr(ai_strategy_review.learning_shadow, 'recent_counterfactuals', lambda *a, **k: [])
    monkeypatch.setattr(ai_strategy_review.learning_shadow, 'recent_decisions', lambda *a, **k: [])
    monkeypatch.setattr(ai_strategy_review, 'build_completed_lifecycles', lambda *_: [])
    monkeypatch.setattr(exit_reentry_shadow, 'resolve_links', lambda *a, **k: [])
    monkeypatch.setattr(exit_reentry_shadow, 'recent', lambda *a, **k: [_row()])
    monkeypatch.setattr(ai_strategy_review.learning_state, 'prepare_checkpoint_evidence', lambda *a, **k: ({}, False))
    monkeypatch.setattr(ai_strategy_review.learning_state, 'advance_patterns', lambda *a, **k: [])
    monkeypatch.setattr(ai_strategy_review.learning_state, 'load_active_state', lambda *a, **k: ({}, True))
    monkeypatch.setattr(ai_strategy_review.learning_control, 'get', lambda *a, **k: {'live_enabled': True})

    result = ai_strategy_review.ensure_self_learning_checkpoint(str(tmp_path), '20260925-18')
    shadow = result['exit_reentry_shadow']
    assert shadow['mode'] == 'shadow_only'
    assert shadow['live_authority'] is False
    assert shadow['state_counts'].get('LIVE_BOUNDED', 0) == 0


def test_entry_learner_interface_is_untouched_by_shadow_module():
    import inspect
    import learning_adapter
    signature = str(inspect.signature(learning_adapter.evaluate_entry))
    assert signature
    assert 'exit' not in signature.lower()
