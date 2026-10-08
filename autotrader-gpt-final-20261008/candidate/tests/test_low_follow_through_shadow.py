import datetime as dt
import json
import low_follow_through_shadow as shadow
import analysis_report


def life(trade_id='t1', symbol='BTC/USDT:USDT', side='long', net=-5.0):
    return {
        'trade_id': trade_id, 'symbol': symbol, 'side': side,
        'entry_time': '2026-09-29T10:00:00', 'net_pnl': net,
        'lifecycle_net': net, 'gross_pnl': net + 1.0, 'fee': 1.0,
    }


def bars(entry='2026-09-29T10:00:00', bullish=True):
    end = dt.datetime.fromisoformat(entry)
    rows=[]
    base=100.0
    for i in range(80):
        open_time=end-dt.timedelta(minutes=(80-i)*5)
        close=base + (i*0.1 if bullish else -i*0.1)
        rows.append({'time': open_time.isoformat(), 'close_time': (open_time+dt.timedelta(minutes=5)).isoformat(),
                     'open':close-.05,'high':close+.1,'low':close-.1,'close':close,'volume':1})
    return rows

def test_causal_snapshot_excludes_future_bar():
    entry=dt.datetime.fromisoformat('2026-09-29T10:00:00')
    rows=bars()
    rows.append({'time':'2026-09-29T10:00:00','close_time':'2026-09-29T10:05:00',
                 'open':200,'high':201,'low':199,'close':200,'volume':1})
    frame=shadow.causal_indicator_frame(rows, entry)
    assert frame.iloc[-1]['timestamp'] < entry


def test_alignment_score_long_short_symmetry():
    states={'3m':'bullish','5m':'bullish','1h':'bearish','4h':'mixed'}
    long=shadow.alignment_score('long',states)
    short=shadow.alignment_score('short',states)
    assert long == {'score':2,'available':4}
    assert short == {'score':1,'available':4}


def test_evaluate_requires_all_four_tf_and_uses_future_only_as_label():
    row=life()
    entry_shadow={'trade_id':'t1','resolved':True,'horizons':{'60':{'mfe_r':0.2,'mae_r':0.4}}}
    out=shadow.evaluate_trade(row, {'3m':'bullish','5m':'bullish','1h':'mixed','4h':'bullish'}, entry_shadow)
    assert out['resolved'] is True
    assert out['alignment_score'] == 3
    assert out['follow_through_025r'] is False
    assert out['follow_through_050r'] is False
    assert 'mfe_r' not in out['gate_inputs']
    assert out['live_authority'] is False

def test_missing_tf_is_unresolved():
    out=shadow.evaluate_trade(life(), {'3m':'bullish','5m':'bullish','1h':'bullish'},
                              {'trade_id':'t1','resolved':True,'horizons':{'60':{'mfe_r':0.8,'mae_r':0.1}}})
    assert out['resolved'] is False
    assert out['reason'] == 'entry_tf_incomplete'


def test_threshold_grid_reports_net_pf_and_follow_through_rates():
    rows=[]
    for score, net, mfe in [(4,10,0.7),(3,4,0.3),(2,-6,0.1),(1,-8,0.05),(0,-3,0.02)]:
        states={tf:('bullish' if i < score else 'bearish') for i,tf in enumerate(('3m','5m','1h','4h'))}
        rows.append(shadow.evaluate_trade(life(trade_id=f't{score}',net=net), states,
                    {'trade_id':f't{score}','resolved':True,'horizons':{'60':{'mfe_r':mfe,'mae_r':0.2}}}))
    s=shadow.summarize_rows(rows)
    assert s['thresholds']['score>=3']['passed_count'] == 2
    assert s['thresholds']['score>=3']['blocked_count'] == 3
    assert s['thresholds']['score>=3']['filtered_actual_net'] == 14
    assert s['thresholds']['score>=3']['follow_through_025_rate'] == 100.0
    assert s['thresholds']['score>=3']['profit_factor'] is None


def test_summary_keeps_live_authority_false():
    s=shadow.summarize_rows([])
    assert s['mode']=='shadow_only'
    assert s['live_authority'] is False

def test_report_renders_low_follow_through_shadow_section():
    snap={'release':'r','period':'all','coverage':{},'overall':{},'symbols':[],'sides':[],
          'top_positive':[],'top_negative':[],'tf':[],'confidence':[],'self_learning':{},
          'review':{},'settings':{},'positions':[],'system_issues':{},
          'low_follow_through_shadow':{
              'mode':'shadow_only','live_authority':False,'sample_count':20,'resolved_count':18,'unresolved_count':2,
              'thresholds':{'score>=3':{'passed_count':8,'blocked_count':10,'filtered_actual_net':12.3,
              'baseline_actual_net':-20.0,'net_improvement_if_blocked':32.3,'profit_factor':1.4,
              'follow_through_025_rate':75.0,'follow_through_050_rate':50.0}}}}
    text=analysis_report.build_report(snap)['text']
    assert '[22. Low Follow-through Entry Shadow]' in text
    assert 'score>=3' in text
    assert '실전 영향 없음' in text

def test_exact_score_buckets_preserve_non_monotonic_result():
    rows=[]
    for score,net,mfe in [(4,-20,0.1),(3,15,0.6),(3,5,0.3),(2,-4,0.1)]:
        states={tf:('bullish' if i < score else 'bearish') for i,tf in enumerate(('3m','5m','1h','4h'))}
        rows.append(shadow.evaluate_trade(life(trade_id=f'{score}-{net}',net=net),states,
                    {'resolved':True,'horizons':{'60':{'mfe_r':mfe,'mae_r':0.2}}}))
    s=shadow.summarize_rows(rows)
    assert s['score_buckets']['3']['actual_net'] == 20
    assert s['score_buckets']['4']['actual_net'] == -20
    assert s['score_buckets']['3']['profit_factor'] is None

def test_report_shows_exact_score_buckets():
    snap={'release':'r','period':'all','coverage':{},'overall':{},'symbols':[],'sides':[],
          'top_positive':[],'top_negative':[],'tf':[],'confidence':[],'self_learning':{},
          'review':{},'settings':{},'positions':[],'system_issues':{},
          'low_follow_through_shadow':{'mode':'shadow_only','live_authority':False,'sample_count':4,'resolved_count':4,
          'unresolved_count':0,'thresholds':{},'score_buckets':{'3':{'count':2,'actual_net':20,'profit_factor':1.5,
          'follow_through_025_rate':70,'follow_through_050_rate':40},'4':{'count':2,'actual_net':-20,'profit_factor':0.3,
          'follow_through_025_rate':30,'follow_through_050_rate':10}}}}
    text=analysis_report.build_report(snap)['text']
    assert 'score=3' in text and 'score=4' in text
