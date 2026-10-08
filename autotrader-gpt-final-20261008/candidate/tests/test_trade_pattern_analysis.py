import json
import pytest

from trade_pattern_analysis import analyze_rows, summarize_group, sample_class, _stable_payload


def _rows(values, condition='tf_combo:5m+1h_bearish'):
    return [
        {'trade_id':f't{i}','symbol':'XRP/USDT:USDT','side':'short','strategy_group':'core','net_pnl':v,'gross_pnl':v+0.1,'fee':0.1,'holding_minutes':10,'coverage':'complete','condition':condition,'features':{'tf':{},'coverage':{'candle_finality':True,'market_structure':True,'gpt':True}}}
        for i,v in enumerate(values)
    ]


def test_group_metrics_use_net_pnl_and_completed_trade_count():
    metric = analyze_rows(_rows([10,-5,20]))['groups'][0]
    assert metric['count'] == 3
    assert metric['win_rate'] == pytest.approx(66.666, rel=1e-3)
    assert metric['profit_factor'] == pytest.approx(6.0)
    assert metric['net_pnl'] == pytest.approx(25.0)


def test_sample_tier_never_validates_small_group():
    metric = summarize_group(_rows([1]*19))
    assert metric['sample_class'] == 'exploratory'
    assert metric['validated'] is False
    assert sample_class(20) == 'watch'
    assert sample_class(50) == 'established_sample'


def test_analysis_is_deterministic_after_generated_at_removed():
    one = analyze_rows(_rows([3,-1,2]))
    two = analyze_rows(_rows([3,-1,2]))
    assert _stable_payload(one) == _stable_payload(two)


def test_no_opaque_best_strategy_score_is_emitted():
    payload = json.dumps(analyze_rows(_rows([1,-1])), sort_keys=True)
    assert 'best_strategy_score' not in payload
    assert 'causal' not in payload


def test_unknown_timeframe_combinations_are_not_ranked_as_patterns():
    row = {
        'trade_id':'t-unknown','symbol':'BTC/USDT:USDT','side':'long','strategy_group':'core',
        'net_pnl':1.0,'gross_pnl':1.1,'fee':0.1,'holding_minutes':5,'coverage':'complete',
        'features':{
            'tf':{name:{'state':'unknown'} for name in ('1m','3m','5m','1h','4h','1d')},
            'coverage':{'candle_finality':False,'market_structure':False,'gpt':False}
        },
    }
    result = analyze_rows([row])
    assert not any(g['dimension'].startswith('tf_combo:') for g in result['groups'])

def test_summary_exposes_economic_net_adjustment_and_win_loss_averages():
    rows = _rows([10, -5, 20])
    rows[0]['gross_pnl'] = 11.0; rows[0]['fee'] = 1.0
    rows[1]['gross_pnl'] = -4.0; rows[1]['fee'] = 1.0
    rows[2]['gross_pnl'] = 21.0; rows[2]['fee'] = 1.0
    metric = summarize_group(rows)
    assert metric['gross_pnl'] == pytest.approx(28.0)
    assert metric['fee'] == pytest.approx(3.0)
    assert metric['net_pnl'] == pytest.approx(25.0)
    assert metric['net_adjustment'] == pytest.approx(0.0)
    assert metric['avg_win'] == pytest.approx(15.0)
    assert metric['avg_loss'] == pytest.approx(-5.0)


def _cf_row(trade_id, *, side="long", symbol="BTC/USDT:USDT", hour=18, tf=None, net=0.0):
    return {
        "trade_id": trade_id,
        "symbol": symbol,
        "side": side,
        "strategy_group": "core",
        "net_pnl": net,
        "gross_pnl": net + 1.0,
        "fee": 1.0,
        "holding_minutes": 10,
        "coverage": "complete",
        "features": {
            "entry_hour_kst": hour,
            "tf": tf or {name:{"state":"bullish"} for name in ("1m","3m","5m","1h","4h","1d")},
            "coverage":{"candle_finality":True,"market_structure":True,"gpt":True},
        },
    }


def test_counterfactual_filter_union_deduplicates_trade_id_and_overlap():
    from trade_pattern_analysis import counterfactual_filter_analysis
    mixed={name:{"state":"bullish"} for name in ("1m","3m","5m","1h","4h","1d")}
    mixed["1h"]={"state":"bearish"}
    rows=[
        _cf_row("t1", side="short", symbol="PI/USDT:USDT", hour=13, tf=mixed, net=-10),
        _cf_row("t1", side="short", symbol="PI/USDT:USDT", hour=13, tf=mixed, net=-10),
        _cf_row("t2", net=5),
        _cf_row("t3", side="short", net=-4),
        _cf_row("t4", symbol="ETH/USDT:USDT", hour=14, net=-2),
        _cf_row("t5", symbol="XRP/USDT:USDT", hour=20, tf=mixed, net=3),
    ]
    result=counterfactual_filter_analysis(rows)
    assert result["baseline"]["count"] == 5
    assert result["baseline"]["net_pnl"] == pytest.approx(-8.0)
    assert result["filters"]["short"]["excluded_count"] == 2
    assert result["filters"]["pi"]["excluded_count"] == 1
    assert result["filters"]["hour_12_17"]["excluded_count"] == 2
    assert result["filters"]["mixed_tf"]["excluded_count"] == 2
    union=result["union"]
    assert union["excluded_count"] == 4
    assert union["remaining_count"] == 1
    assert union["remaining"]["net_pnl"] == pytest.approx(5.0)
    assert union["net_improvement"] == pytest.approx(13.0)
    assert result["overlap_by_match_count"] == {"0":1,"1":3,"4":1}


def test_counterfactual_filter_metrics_reconcile_economic_accounting():
    from trade_pattern_analysis import counterfactual_filter_analysis
    rows=[_cf_row("a", net=-3), _cf_row("b", side="short", net=7)]
    rows[0]["gross_pnl"]=-1; rows[0]["fee"]=1
    rows[1]["gross_pnl"]=9; rows[1]["fee"]=1
    result=counterfactual_filter_analysis(rows)
    base=result["baseline"]
    assert base["gross_pnl"] == pytest.approx(8.0)
    assert base["fee"] == pytest.approx(2.0)
    assert base["net_pnl"] == pytest.approx(4.0)
    assert base["net_adjustment"] == pytest.approx(-2.0)
    kept=result["filters"]["short"]["remaining"]
    assert kept["net_pnl"] == pytest.approx(-3.0)
    assert result["filters"]["short"]["net_improvement"] == pytest.approx(-7.0)


def test_analyze_rows_embeds_deduplicated_filter_counterfactual():
    result=analyze_rows([_cf_row('x1',side='short',net=-2),_cf_row('x2',net=3)])
    cf=result['filter_counterfactual']
    assert cf['baseline']['count']==2
    assert cf['union']['excluded_count']==1
    assert cf['union']['remaining']['net_pnl']==pytest.approx(3.0)


def test_exit_reentry_summary_uses_lifecycle_net_not_final_close_net():
    from trade_pattern_analysis import summarize_exit_reentry
    rows=[{
        'trade_id':'xrp-1109','symbol':'XRP/USDT:USDT','side':'long','coverage':'complete',
        'final_close_reason':'position_ai_close_all','lifecycle_net':-35.29,'net_pnl':-35.29,
        'final_close_net':-18.63,'reduce_net':-16.66,'gross_pnl':0.0,'fee':0.0,
    }]
    shadow=[{
        'exit_id':'e1','reentry_within_30m':False,'reentry_within_60m':True,'reentry_within_120m':True,
        'next_lifecycle_net':-6.41,'churn_cycle_net':-41.70,'analytical_outcome':'reentry_loss',
    }]
    result=summarize_exit_reentry(rows,shadow)
    assert result['ai_close_lifecycle_count'] == 1
    assert result['ai_close_lifecycle_net'] == pytest.approx(-35.29)
    assert result['ai_close_final_close_net'] == pytest.approx(-18.63)
    assert result['ai_close_reduce_net'] == pytest.approx(-16.66)
    assert result['same_side_reentry_rate_30m'] == pytest.approx(0.0)
    assert result['same_side_reentry_rate_60m'] == pytest.approx(100.0)
    assert result['same_side_reentry_rate_120m'] == pytest.approx(100.0)
    assert result['subsequent_lifecycle_net'] == pytest.approx(-6.41)
    assert result['churn_cycle_net'] == pytest.approx(-41.70)
