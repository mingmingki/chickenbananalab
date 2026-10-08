import math
import analysis_report
import fee_aware_entry_shadow as shadow


def life(side='long', strategy='core', symbol='BTC/USDT:USDT', entry=100.0, tp=102.0, amount=10.0, net=-1.0, execution_id=None):
    op={'type':'open','symbol':symbol,'side':side,'price':entry,'amount':amount,'tp_price':tp,'strategy_group':strategy,'time':'2026-09-29T10:00:00'}
    if execution_id: op['execution_id']=execution_id
    return {'trade_id':f'{symbol}-{side}-{entry}-{tp}','symbol':symbol,'side':side,'strategy_group':strategy,
            'entry_time':op['time'],'entry_price':entry,'lifecycle_net':net,'net_pnl':net,'events':[op]}


def test_core_fee_only_long_edge_uses_base_quantity_and_roundtrip_taker_fee(tmp_path):
    out=shadow.evaluate_lifecycle(str(tmp_path),life())
    assert out['resolved'] is True
    assert out['cost_basis']=='fee_only_core'
    assert math.isclose(out['raw_tp_gross_usdt'],20.0)
    assert math.isclose(out['entry_fee_usdt'],0.5)
    assert math.isclose(out['exit_fee_usdt'],0.51)
    assert math.isclose(out['expected_net_at_tp_usdt'],18.99)
    assert out['spread_slippage_proven'] is False


def test_short_is_symmetric(tmp_path):
    out=shadow.evaluate_lifecycle(str(tmp_path),life(side='short',tp=98.0))
    assert out['resolved'] is True
    assert math.isclose(out['raw_tp_gross_usdt'],20.0)
    assert math.isclose(out['expected_net_at_tp_usdt'],19.01)


def test_missing_tp_is_unresolved(tmp_path):
    row=life(); row['events'][0]['tp_price']=None
    out=shadow.evaluate_lifecycle(str(tmp_path),row)
    assert out['resolved'] is False
    assert out['reason']=='tp_unproven'
    assert out['live_authority'] is False


def test_candidate_c_uses_epoch_contract_size_and_execution_costs(tmp_path):
    epoch={'position_id':'e1','raw_entry_price':100.0,'contract_size':1000.0,'fee_rate':0.0005,
           'spread_bps':3.0,'slippage_bps':3.0,'entry_fee_usdt':5.003,'target_price':102.0}
    (tmp_path/'candidate_c_epoch_store.jsonl').write_text(__import__('json').dumps(epoch)+'\n')
    row=life(strategy='candidate_c',symbol='DOGE/USDT:USDT',amount=0.1,execution_id='e1')
    out=shadow.evaluate_lifecycle(str(tmp_path),row)
    assert out['resolved'] is True
    assert out['cost_basis']=='fee_spread_slippage_candidate_c'
    assert out['spread_slippage_proven'] is True
    assert out['amount_coin']==100.0
    assert out['expected_net_at_tp_usdt'] < out['raw_tp_gross_usdt']


def test_threshold_grid_reports_filtered_actual_net(tmp_path):
    rows=[life(symbol='BTC/USDT:USDT',entry=100,tp=100.2,amount=10,net=-3.0),
          life(symbol='XRP/USDT:USDT',entry=100,tp=102,amount=10,net=4.0)]
    out=shadow.summarize(str(tmp_path),rows,limit=150)
    assert out['mode']=='shadow_only' and out['live_authority'] is False
    assert out['sample_count']==2 and out['resolved_count']==2
    t5=out['thresholds']['5x']
    assert t5['passed_count']==1 and t5['blocked_count']==1
    assert math.isclose(t5['filtered_actual_net'],4.0)
    assert math.isclose(t5['baseline_actual_net'],1.0)
    assert math.isclose(t5['net_improvement_if_blocked'],3.0)


def test_symbol_breakdown_surfaces_btc_xrp(tmp_path):
    rows=[life(symbol='BTC/USDT:USDT',net=-2.0),life(symbol='XRP/USDT:USDT',net=3.0)]
    out=shadow.summarize(str(tmp_path),rows)
    by={r['symbol']:r for r in out['symbols']}
    assert by['BTC/USDT:USDT']['count']==1
    assert by['XRP/USDT:USDT']['actual_net']==3.0


def test_report_renders_fee_aware_entry_shadow():
    snap={'release':'r','period':'all','coverage':{},'overall':{},'symbols':[],'sides':[],
          'top_positive':[],'top_negative':[],'tf':[],'confidence':[],'self_learning':{},
          'review':{},'settings':{},'positions':[],'system_issues':{},
          'fee_aware_entry_shadow':{'mode':'shadow_only','live_authority':False,'sample_count':10,
          'resolved_count':8,'unresolved_count':2,'cost_basis_counts':{'fee_only_core':7,'fee_spread_slippage_candidate_c':1},
          'thresholds':{'1x':{'passed_count':8,'blocked_count':0,'filtered_actual_net':-5,'baseline_actual_net':-5,'net_improvement_if_blocked':0},
                        '3x':{'passed_count':6,'blocked_count':2,'filtered_actual_net':1,'baseline_actual_net':-5,'net_improvement_if_blocked':6}}}}
    text=analysis_report.build_report(snap)['text']
    assert '[21. Fee-aware Entry Gate Shadow]' in text
    assert '실전 영향 없음' in text
    assert '3x' in text and 'Net 개선 +6.00' in text
