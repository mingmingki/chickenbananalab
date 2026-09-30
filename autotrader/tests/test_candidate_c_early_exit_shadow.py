import json
from pathlib import Path

import analysis_report
import candidate_c_early_exit_shadow as shadow


def _life(side='long', reason='4h_direction_invalidated'):
    return {
        'trade_id':'t1','symbol':'DOGE/USDT:USDT','side':side,
        'strategy_group':'candidate_c','entry_time':'2026-09-26T05:10:23',
        'exit_time':'2026-09-27T09:00:31','net_pnl':-80.0,
        'gross_pnl':-75.0,'final_close_reason':reason,
        'events':[
            {'type':'open','time':'2026-09-26T05:10:23','amount':10.0,'price':100.0,'execution_id':'entry-1'},
            {'type':'reduce','time':'2026-09-26T15:00:23','amount':5.0,'close_price':95.0},
            {'type':'close','time':'2026-09-27T09:00:31','amount':5.0,'close_price':90.0,'reason':reason},
        ],
    }


def _ledger(tmp_path, contract_size=1.0):
    p=Path(tmp_path)/'candidate_c_intent_ledger_DOGE_USDT_USDT.jsonl'
    p.write_text(json.dumps({'event':'created','intent_id':'entry-1','contract_size':contract_size})+'\n')


def test_three_way_reconstructs_gross_from_exact_prices_and_contract_size(tmp_path):
    _ledger(tmp_path, contract_size=1.0)
    entry_cf={'trade_id':'t1','horizons':{'60':{'mfe_r':0.10}}}
    out=shadow.evaluate_lifecycle(str(tmp_path), _life(), entry_cf)
    assert out['eligible'] is True
    assert out['follow_through_failed_60m'] is True
    assert out['current_policy_gross'] == -75.0
    assert out['hold_full_gross'] == -100.0
    assert out['early_full_exit_gross'] == -50.0
    assert out['winner'] == 'EARLY_FULL_EXIT'
    assert out['live_authority'] is False


def test_short_math_is_symmetric(tmp_path):
    _ledger(tmp_path, contract_size=2.0)
    life=_life(side='short')
    life['events'][1]['close_price']=105.0
    life['events'][2]['close_price']=110.0
    out=shadow.evaluate_lifecycle(str(tmp_path), life, {'trade_id':'t1','horizons':{'60':{'mfe_r':0.05}}})
    assert out['current_policy_gross'] == -150.0
    assert out['hold_full_gross'] == -200.0
    assert out['early_full_exit_gross'] == -100.0


def test_requires_4h_exit_half_reduce_and_failed_60m_followthrough(tmp_path):
    _ledger(tmp_path)
    good_cf={'trade_id':'t1','horizons':{'60':{'mfe_r':0.24}}}
    assert shadow.evaluate_lifecycle(str(tmp_path), _life(reason='manual_close_15m'), good_cf)['eligible'] is False
    no_reduce=_life(); no_reduce['events']=[no_reduce['events'][0],no_reduce['events'][2]]
    assert shadow.evaluate_lifecycle(str(tmp_path), no_reduce, good_cf)['eligible'] is False
    strong_cf={'trade_id':'t1','horizons':{'60':{'mfe_r':0.30}}}
    assert shadow.evaluate_lifecycle(str(tmp_path), _life(), strong_cf)['eligible'] is False


def test_refresh_is_idempotent_and_summary_is_shadow_only(tmp_path):
    _ledger(tmp_path)
    cf=[{'trade_id':'t1','horizons':{'60':{'mfe_r':0.10}}}]
    assert shadow.refresh(str(tmp_path), [_life()], cf) == 1
    assert shadow.refresh(str(tmp_path), [_life()], cf) == 0
    s=shadow.summary(str(tmp_path))
    assert s['mode']=='shadow_only' and s['live_authority'] is False
    assert s['resolved_count']==1
    assert s['winner_counts']['EARLY_FULL_EXIT']==1
    assert s['early_vs_current_gross_improvement']==25.0


def test_report_renders_candidate_c_early_exit_shadow_section():
    snap={'release':'r','period':'all','coverage':{},'overall':{},'symbols':[],'sides':[],
          'top_positive':[],'top_negative':[],'tf':[],'confidence':[],'self_learning':{},
          'review':{},'settings':{},'positions':[],'system_issues':{},
          'candidate_c_early_exit_shadow':{
              'mode':'shadow_only','live_authority':False,'sample_count':16,'eligible_count':3,'resolved_count':3,
              'winner_counts':{'HOLD_FULL':0,'CURRENT_POLICY':0,'EARLY_FULL_EXIT':2,'TIE':1},
              'current_policy_gross_sum':-116.56,'hold_full_gross_sum':-125.22,
              'early_full_exit_gross_sum':-107.89,'early_vs_current_gross_improvement':8.67}}
    text=analysis_report.build_report(snap)['text']
    assert '[20. Candidate C Early-exit 3-way Shadow]' in text
    assert '실전 영향 없음' in text
    assert 'eligible 3' in text
    assert 'EARLY_FULL_EXIT -107.89' in text
    assert '현재 대비 +8.67' in text
