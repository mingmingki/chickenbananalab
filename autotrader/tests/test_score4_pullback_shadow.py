import datetime as dt
import analysis_report
import score4_pullback_shadow as shadow

def bars():
    return [
        {'close_time':'2026-09-29T10:05:00','close':101.0,'ema_20':100.0},
        {'close_time':'2026-09-29T10:10:00','close':99.0,'ema_20':100.0},
        {'close_time':'2026-09-29T10:15:00','close':100.5,'ema_20':100.0},
    ]

def life(side='long',net=-10.0,trade_id='t1'):
    return {'trade_id':trade_id,'symbol':'XRP/USDT:USDT','side':side,
            'entry_time':'2026-09-29T10:00:00','entry_price':100.0,'lifecycle_net':net}

def low(score=4):
    return {'trade_id':'t1','resolved':True,'alignment_score':score,'actual_net':-10.0}
def test_long_pullback_then_reclaim_confirms():
    out=shadow.detect_confirm(bars(),dt.datetime.fromisoformat('2026-09-29T10:00:00'),'long',60)
    assert out['confirmed'] is True
    assert out['confirm_price']==100.5

def test_short_is_symmetric():
    rows=[{'close_time':'2026-09-29T10:05:00','close':99.0,'ema_20':100.0},
          {'close_time':'2026-09-29T10:10:00','close':101.0,'ema_20':100.0},
          {'close_time':'2026-09-29T10:15:00','close':99.5,'ema_20':100.0}]
    out=shadow.detect_confirm(rows,dt.datetime.fromisoformat('2026-09-29T10:00:00'),'short',60)
    assert out['confirmed'] is True

def test_score_not_four_is_ineligible():
    out=shadow.evaluate_trade(life(),low(3),{'allowed':True,'extension_atr':1.0},bars())
    assert out['eligible'] is False and out['reason']=='alignment_score_not_4'

def test_existing_extreme_guard_is_not_new_candidate():
    out=shadow.evaluate_trade(life(),low(4),{'allowed':False,'reason':'entry_overextended_extreme'},bars())
    assert out['eligible'] is False and out['reason']=='current_guard_blocks'
def test_evaluate_records_30_60_90_windows_without_live_authority():
    out=shadow.evaluate_trade(life(),low(4),{'allowed':True,'extension_atr':1.0,'directional_24h_pct':2.0},bars())
    assert out['eligible'] is True
    assert out['windows']['60']['confirmed'] is True
    assert out['live_authority'] is False

def test_summary_separates_confirmed_and_unconfirmed_actual_net():
    a=shadow.evaluate_trade(life(net=-10,trade_id='a'),{'alignment_score':4,'actual_net':-10},
                            {'allowed':True,'extension_atr':1.0},bars())
    b=shadow.evaluate_trade(life(net=-30,trade_id='b'),{'alignment_score':4,'actual_net':-30},
                            {'allowed':True,'extension_atr':1.0},[])
    s=shadow.summarize_rows([a,b])
    assert s['windows']['60']['confirmed_count']==1
    assert s['windows']['60']['unconfirmed_count']==1
    assert s['windows']['60']['confirmed_actual_net']==-10
    assert s['windows']['60']['unconfirmed_actual_net']==-30
    assert s['windows']['60']['net_improvement_if_block_unconfirmed']==30

def test_report_renders_score4_pullback_shadow():
    snap={'release':'r','period':'all','coverage':{},'overall':{},'symbols':[],'sides':[],
          'top_positive':[],'top_negative':[],'tf':[],'confidence':[],'self_learning':{},
          'review':{},'settings':{},'positions':[],'system_issues':{},
          'score4_pullback_shadow':{'mode':'shadow_only','live_authority':False,'sample_count':10,
          'eligible_count':8,'windows':{'60':{'confirmed_count':3,'unconfirmed_count':5,
          'confirmed_actual_net':-12,'unconfirmed_actual_net':-40,'net_improvement_if_block_unconfirmed':40}}}}
    text=analysis_report.build_report(snap)['text']
    assert '[23. Score=4 Pullback-confirm Shadow]' in text
    assert '실전 권한이 없습니다' in text
