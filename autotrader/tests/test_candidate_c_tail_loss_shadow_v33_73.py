"""v33-73 evidence-only Candidate C tail-loss and residual scenarios."""
from pathlib import Path
import copy
import json
import math
import pytest
import candidate_c_tail_loss_shadow_v33_73 as m

USER='/opt/autotrader/users/chickenbananalab'

def life(*,side='long',entry=100.,stop=90.,partial_price=97.,close_price=94.,
         original=10.,partial_qty=5.,remaining=5.,symbol='SOL/USDT:USDT'):
    return dict(strategy_group='candidate_c',coverage='complete',symbol=symbol,
        entry_time='2026-09-23T12:50:21',exit_time='2026-09-23T23:15:28',
        net_pnl=-40,events=[
            dict(type='open',side=side,time='2026-09-23T12:50:21',
                price=entry,sl_price=stop,amount=original),
            dict(type='reduce',side=side,time='2026-09-23T17:00:19',
                close_price=partial_price,amount=partial_qty),
            dict(type='close',side=side,time='2026-09-23T23:15:28',
                close_price=close_price,amount=remaining,reason='test')])

def test_exact_loss_math_conservative_20bps():
    value,err=m.one(life())
    assert err is None
    assert value['first_reduce_is_losing'] is True
    assert value['first_reduce_adverse_r']==.3
    assert round(value['retrospective_net_delta_after_20bp_usdt'],5)==round(15-97*5*.002,5)
    assert value['initial_stop_distance_pct']==10

def test_recovery_makes_full_exit_worse():
    value,err=m.one(life(close_price=106))
    assert value['retrospective_net_delta_after_20bp_usdt']<0

@pytest.mark.parametrize('op,reason',[
    (lambda x:x['events'][0].update(sl_price=None),'MISSING_PRICE_OR_STOP'),
    (lambda x:x['events'][0].update(sl_price=103),'INITIAL_STOP_NOT_ADVERSE'),
    (lambda x:x['events'][2].update(side='short'),'SIDE_MISMATCH'),
    (lambda x:x['events'][1].update(amount=4),'EXCHANGE_QUANTITY_SPLIT_AMBIGUOUS'),
    (lambda x:x['events'].append(dict(type='reduce')),'MULTI_REDUCE_OR_INCOMPLETE'),
    (lambda x:x.update(symbol='UNKNOWN/USDT:USDT'),'UNKNOWN_CONTRACT_SIZE'),
])
def test_fail_closed_for_ambiguous_ex_post_cases(op,reason):
    row=life();op(row)
    value,err=m.one(row)
    assert value is None and err==reason

def test_source_journal_sample_and_negative_strategy_choice():
    results=m.run(USER)
    assert results['total_completed_lifecycles']==62
    assert results['matched_one_reduce_lifecycles']==29
    assert results['recommendation']=='DO_NOT_DEPLOY_IMMEDIATE_FULL_EXIT_POLICY'
    assert results['cohorts']['all']['ex_post_delta_usdt']<0
    assert results['adverse_reduce_thresholds']['0.25']['since_2026_10_13']['n']<=1
    assert results['strategy_changed'] is False
    assert len(results['journal_sha256'])==64

def test_old_largest_loss_plan_no_equity_assertion():
    results=m.run(USER)
    row=next(x for x in results['top_losing_lifecycles'] if x['entry_time']=='2026-09-23T12:50:21')
    assert row['planned_stop_gross_usdt']>240
    assert row['initial_stop_distance_pct']>9
    assert row['first_reduce_adverse_r']<.75
    assert row['actual_lifecycle_net_usdt']<-160
    assert any('historical equity' in x for x in results['notes'])

def test_authenticated_endpoint_and_no_trade_code_mutation():
    import web_app
    with web_app.app.test_client() as client:
        assert client.get('/api/ops/candidate-c-tail-loss-shadow-v33-73').status_code==401
        with client.session_transaction() as sess:
            sess['authenticated']=True;sess['username']='chickenbananalab'
        rsp=client.get('/api/ops/candidate-c-tail-loss-shadow-v33-73')
        assert rsp.status_code==200
        body=rsp.get_json()
        assert body['trading_policy_changed'] is False
        assert 'all_scenarios' not in body['report']
        details=client.get('/api/ops/candidate-c-tail-loss-shadow-v33-73?details=1').get_json()
        assert len(details['report']['all_scenarios'])==29

def test_previous_execution_code_bitwise_unchanged():
    prev=Path('/opt/autotrader-releases/candidate_c_profit_proof_v33_72_20261020KST')
    for name in ('candidate_c_trader_adapter.py','candidate_c_hybrid_cycle.py',
                 'candidate_c_decision_engine.py','candidate_c_hybrid_live_adapter.py',
                 'risk_manager.py','candidate_c_exit_management.py'):
        assert Path(name).read_bytes()==(prev/name).read_bytes(),name

def test_no_implicit_market_order():
    code=Path('candidate_c_tail_loss_shadow_v33_73.py').read_text()
    for bad in ('create_order(', 'close_position(', 'cancel_order(', 'amend_protective_stop('):
        assert bad not in code
