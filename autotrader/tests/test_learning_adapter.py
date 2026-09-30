import learning_adapter as la
import learning_state as ls


def ev(dimension='symbol', value='BTC/USDT:USDT', direction='negative', **kw):
    base = dict(dimension=dimension, value=value, sample_count=80, coverage=.95,
                resolved_count=30, shadow_benefit_net=10.0, recent_benefit_net=4.0,
                recent_direction=direction, long_direction=direction,
                outlier_share=.10, checkpoint_streak=3, deteriorating_checkpoints=0,
                material_pf_reversal=False, data_integrity_issue=False)
    base.update(kw)
    return base


def put(tmp_path, pid, evidence, state='LIVE_BOUNDED'):
    ls.append_transition(str(tmp_path), pid, None, state, 'test', evidence)
    ls.write_snapshot(str(tmp_path), ls.replay_state(str(tmp_path)))


def candidate(**kw):
    base = dict(symbol='BTC/USDT:USDT', side='long', action='long',
                timestamp='2026-09-25T18:30:00+09:00', gemini_confidence=.74,
                gpt_confidence=.76)
    base.update(kw)
    return base


def features(**kw):
    base = dict(market_regime='bullish', trade_alignment='with_regime',
                short_level='NONE', correction_active=False,
                tf_combos=['3m+5m:bullish'])
    base.update(kw)
    return base


def test_live_negative_pattern_can_hold_already_approved_entry(tmp_path):
    put(tmp_path,'p1',ev())
    out = la.evaluate_entry(str(tmp_path), candidate(), features(), True)
    assert out['action'] == 'HOLD_BY_LEARNING'
    assert out['live_applied'] is True
    assert out['confidence_delta'] < 0
    assert out['matched_patterns'][0]['pattern_id'] == 'p1'


def test_live_off_has_zero_execution_influence_but_keeps_shadow_decision(tmp_path):
    put(tmp_path,'p1',ev())
    out = la.evaluate_entry(str(tmp_path), candidate(), features(), False)
    assert out['action'] == 'ALLOW'
    assert out['confidence_delta'] == 0
    assert out['live_applied'] is False
    assert out['shadow_action'] == 'HOLD_BY_LEARNING'


def test_positive_pattern_only_adds_bounded_confidence(tmp_path):
    put(tmp_path,'p1',ev(direction='positive'))
    out = la.evaluate_entry(str(tmp_path), candidate(), features(), True)
    assert out['action'] == 'ALLOW'
    assert 0 < out['confidence_delta'] <= .10


def test_pattern_dimensions_match_and_unknown_does_not(tmp_path):
    dims = [
        ('side','long'), ('market_regime','bullish'), ('trade_alignment','with_regime'),
        ('short_level','NONE'), ('correction_active',False), ('gemini_confidence','0.70-0.79'),
        ('gpt_confidence','0.70-0.79'), ('hour_bucket','18-23'), ('weekday','Fri'),
        ('tf_combo','3m+5m:bullish'),
    ]
    for i,(d,v) in enumerate(dims):
        put(tmp_path,f'p{i}',ev(dimension=d,value=v,direction='positive'))
    out = la.evaluate_entry(str(tmp_path), candidate(), features(), True)
    assert len(out['matched_patterns']) == len(dims)
    out2 = la.evaluate_entry(str(tmp_path), candidate(), features(market_regime=None, tf_combos=[]), True)
    ids = {p['pattern_id'] for p in out2['matched_patterns']}
    assert 'p1' not in ids  # market_regime
    assert 'p9' not in ids  # tf combo


def test_conflicting_patterns_cancel_and_delta_is_clipped(tmp_path):
    put(tmp_path,'pos',ev(direction='positive'))
    put(tmp_path,'neg',ev(dimension='side', value='long', direction='negative'))
    out = la.evaluate_entry(str(tmp_path), candidate(), features(), True)
    assert -.10 <= out['confidence_delta'] <= .10
    assert abs(out['confidence_delta']) < .10


def test_state_loader_failure_fails_open(monkeypatch, tmp_path):
    def boom(_):
        raise RuntimeError('broken')
    monkeypatch.setattr(la.learning_state, 'load_active_state', boom)
    out = la.evaluate_entry(str(tmp_path), candidate(), features(), True)
    assert out['action'] == 'ALLOW'
    assert out['confidence_delta'] == 0
    assert out['reason'].startswith('LEARNING_ERROR')

def test_real_analysis_tf_dimension_format_matches_adapter(tmp_path):
    put(tmp_path,'tf-real',ev(dimension='tf_combo:3m+5m',value='bullish',direction='positive'))
    out=la.evaluate_entry(str(tmp_path),candidate(),features(tf_combos=['3m+5m:bullish']),True)
    assert any(p['pattern_id']=='tf-real' for p in out['matched_patterns'])


def test_real_analysis_boolean_value_format_matches_adapter(tmp_path):
    put(tmp_path,'corr-real',ev(dimension='correction_active',value='False',direction='positive'))
    out=la.evaluate_entry(str(tmp_path),candidate(),features(correction_active=False),True)
    assert any(p['pattern_id']=='corr-real' for p in out['matched_patterns'])

def test_real_analysis_confidence_edge_buckets_match_adapter(tmp_path):
    put(tmp_path,'high',ev(dimension='gpt_confidence',value='>=0.90',direction='positive'))
    put(tmp_path,'low',ev(dimension='gemini_confidence',value='<0.60',direction='positive'))
    out=la.evaluate_entry(str(tmp_path),candidate(gpt_confidence=.95,gemini_confidence=.55),features(),True)
    ids={p['pattern_id'] for p in out['matched_patterns']}
    assert {'high','low'} <= ids
