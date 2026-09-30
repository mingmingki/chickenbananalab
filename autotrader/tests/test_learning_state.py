import json
from pathlib import Path

import learning_state as ls


def ev(**kw):
    base = dict(sample_count=50, coverage=0.9, resolved_count=20,
                shadow_benefit_net=5.0, recent_benefit_net=2.0,
                recent_direction='positive', long_direction='positive',
                outlier_share=0.1, checkpoint_streak=2,
                deteriorating_checkpoints=0, material_pf_reversal=False,
                data_integrity_issue=False)
    base.update(kw)
    return base


def test_append_and_replay_are_deterministic(tmp_path):
    ls.append_transition(str(tmp_path),'p1',None,'DISCOVERY','first',{'sample_count': 3})
    ls.append_transition(str(tmp_path),'p1','DISCOVERY','SHADOW_LEARNING','sample20',{'sample_count': 20})
    state = ls.replay_state(str(tmp_path))
    assert state['p1']['state'] == 'SHADOW_LEARNING'
    assert state['p1']['reason'] == 'sample20'
    assert len(Path(tmp_path/'learning_state.jsonl').read_text().splitlines()) == 2


def test_snapshot_free_and_corrupt_snapshot_replay_parity(tmp_path):
    ls.append_transition(str(tmp_path),'p1',None,'VALIDATED','validated',ev())
    expected = ls.replay_state(str(tmp_path))
    state, safe = ls.load_active_state(str(tmp_path))
    assert safe is True and state == expected
    (tmp_path/'learning_active_snapshot.json').write_text('{bad json')
    rebuilt, safe = ls.load_active_state(str(tmp_path))
    assert safe is True and rebuilt == expected


def test_malformed_jsonl_is_degraded_not_authoritative(tmp_path):
    p = tmp_path/'learning_state.jsonl'
    p.write_text('{bad}\n' + json.dumps({'pattern_id':'p1','new_state':'LIVE_BOUNDED','reason':'x','evidence':{}})+'\n')
    state, safe = ls.load_active_state(str(tmp_path))
    assert state['p1']['state'] == 'LIVE_BOUNDED'
    assert safe is False


def test_advance_promotes_then_live_and_demotes_on_toggle(tmp_path):
    user = str(tmp_path)
    t1 = ls.advance_patterns(user, {'p1': ev(sample_count=20, resolved_count=0, checkpoint_streak=0)}, False)
    assert t1[-1]['new_state'] == 'SHADOW_LEARNING'
    t2 = ls.advance_patterns(user, {'p1': ev()}, False)
    assert t2[-1]['new_state'] == 'VALIDATED'
    t3 = ls.advance_patterns(user, {'p1': ev()}, True)
    assert t3[-1]['new_state'] == 'LIVE_BOUNDED'
    t4 = ls.advance_patterns(user, {'p1': ev()}, False)
    assert t4[-1]['new_state'] == 'SHADOW_LEARNING'


def test_ai_text_cannot_promote(tmp_path):
    user = str(tmp_path)
    rows = ls.advance_patterns(user, {'p1': ev(sample_count=3, ai_recommendation='promote to live now')}, True)
    assert rows[-1]['new_state'] == 'DISCOVERY'
