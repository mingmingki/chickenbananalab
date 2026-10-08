import importlib
import datetime as dt

import analysis_report


def mod():
    return importlib.import_module('entry_counterfactual_shadow')


def life(side='long', strategy='core', symbol='BTC/USDT:USDT', entry=100.0, sl=95.0, tp=110.0):
    return {
        'trade_id':'t1','symbol':symbol,'side':side,'strategy_group':strategy,
        'entry_time':'2026-09-29T10:00:00','exit_time':'2026-09-29T13:00:00',
        'entry_price':entry,'net_pnl':-3.0,
        'events':[{'type':'open','price':entry,'sl_price':sl,'tp_price':tp,
                   'market_regime':'bullish','trade_alignment':'with_regime'}],
    }


def bars(*rows):
    return [{'time':f'2026-09-29T{hhmm}:00','high':hi,'low':lo,'close':cl} for hhmm,hi,lo,cl in rows]


def test_long_entry_path_mfe_mae_and_tp_first():
    out=mod().evaluate_lifecycle(life(),bars(('10:05',102,98,101),('10:30',104,96,103),('11:00',111,99,108),('12:00',108,101,105)))
    assert out['resolved'] is True
    assert out['barrier_outcome']=='tp_first'
    assert round(out['horizons']['60']['mfe_r'],2)==2.20
    assert round(out['horizons']['60']['mae_r'],2)==0.80


def test_short_path_is_symmetric():
    out=mod().evaluate_lifecycle(life(side='short',entry=100,sl=105,tp=90),bars(('10:05',102,98,99),('10:30',104,96,97),('11:00',104,89,92),('12:00',101,92,96)))
    assert out['barrier_outcome']=='tp_first'
    assert round(out['horizons']['60']['mfe_r'],2)==2.20
    assert round(out['horizons']['60']['mae_r'],2)==0.80


def test_same_bar_sl_tp_is_ambiguous_not_guessed():
    out=mod().evaluate_lifecycle(life(),bars(('10:05',111,94,100),('12:00',105,99,102)))
    assert out['barrier_outcome']=='ambiguous_same_bar'
    assert out['barrier_resolved'] is False


def test_candidate_c_late_entry_signature_is_shadow_only():
    row=life(strategy='candidate_c',symbol='SOL/USDT:USDT')
    out=mod().evaluate_lifecycle(row,bars(('10:05',100.5,96.5,97),('10:30',100.5,96,98),('11:00',102,96,101),('12:00',103,97,102)))
    assert out['late_entry_signature'] is True
    summary=mod().summarize_evaluations([out])
    assert summary['mode']=='shadow_only'
    assert summary['live_authority'] is False
    assert summary['late_entry_signature_count']==1


def test_refresh_persists_once_and_does_not_refetch(tmp_path):
    calls=[]
    fakebars=bars(('10:05',102,98,101),('10:30',104,96,103),('11:00',111,99,108),('12:00',108,101,105))
    def fetcher(symbol,start,end): calls.append((symbol,start,end)); return fakebars
    m=mod(); assert m.refresh(str(tmp_path),[life()],fetcher,limit=150)==1
    assert len(calls)==1
    assert m.refresh(str(tmp_path),[life()],fetcher,limit=150)==0
    assert len(calls)==1
    assert m.summary(str(tmp_path),150)['sample_count']==1


def test_analysis_report_renders_entry_counterfactual_shadow():
    snap={
        'release':'r','period':'all','coverage':{},'overall':{},'symbols':[],'sides':[],
        'top_positive':[],'top_negative':[],'tf':[],'confidence':[],
        'self_learning':{},'review':{},'settings':{},'positions':[],'system_issues':{},
        'entry_counterfactual_shadow':{
            'mode':'shadow_only','live_authority':False,'sample_count':12,'resolved_count':10,
            'barrier_counts':{'sl_first':4,'tp_first':3,'ambiguous_same_bar':1,'none_120m':2},
            'late_entry_signature_count':5,
            'horizon_summary':{'30':{'avg_mfe_r':0.25,'avg_mae_r':0.71},'60':{'avg_mfe_r':0.44,'avg_mae_r':0.93},'120':{'avg_mfe_r':0.76,'avg_mae_r':1.22}},
        },
    }
    text=analysis_report.build_report(snap)['text']
    assert '[19. Entry Counterfactual Shadow]' in text
    assert '실전 영향 없음' in text
    assert 'SL first 4' in text and 'TP first 3' in text
    assert 'late-entry signature 5' in text


def test_okx_fetcher_uses_confirmed_candle_close_time(monkeypatch):
    m=mod()
    class Exchange:
        def fetch_ohlcv(self, symbol, timeframe, since=None, limit=None):
            open_kst=dt.datetime(2026,9,29,10,0,tzinfo=dt.timezone(dt.timedelta(hours=9)))
            return [[int(open_kst.timestamp()*1000),99,102,98,101,1]]
    class Client:
        def __init__(self,*a,**k): self.exchange=Exchange()
    import okx_client
    monkeypatch.setattr(okx_client,'OkxClient',Client)
    fetch=m.make_okx_fetcher(object())
    rows=fetch('BTC/USDT:USDT',dt.datetime(2026,9,29,9,59),dt.datetime(2026,9,29,10,6))
    assert rows[0]['time']==dt.datetime(2026,9,29,10,5)


def test_off_grid_entry_resolves_on_first_confirmed_5m_close_after_120m():
    row=life(); row['entry_time']='2026-09-29T10:00:49'
    out=mod().evaluate_lifecycle(row,bars(('10:05',101,99,100),('10:30',102,98,101),('11:00',103,97,102),('12:05',104,98,103)))
    assert out['resolved'] is True
    assert out['horizon_basis']=='confirmed_5m_close_max_5m_slop'


def test_okx_fetcher_includes_first_confirmed_close_within_5m_after_end(monkeypatch):
    m=mod()
    class Exchange:
        def fetch_ohlcv(self, symbol, timeframe, since=None, limit=None):
            z=dt.timezone(dt.timedelta(hours=9))
            a=dt.datetime(2026,9,29,10,0,tzinfo=z); b=dt.datetime(2026,9,29,10,5,tzinfo=z)
            return [[int(a.timestamp()*1000),99,102,98,101,1],[int(b.timestamp()*1000),101,103,100,102,1]]
    class Client:
        def __init__(self,*a,**k): self.exchange=Exchange()
    import okx_client
    monkeypatch.setattr(okx_client,'OkxClient',Client)
    rows=m.make_okx_fetcher(object())('BTC/USDT:USDT',dt.datetime(2026,9,29,9,59),dt.datetime(2026,9,29,10,6))
    assert [r['time'] for r in rows]==[dt.datetime(2026,9,29,10,5),dt.datetime(2026,9,29,10,10)]


def test_report_always_surfaces_focus_entry_groups_even_if_not_top8():
    groups=[{'dimension':'symbol','value':f'X{i}','count':10,'net_pnl':-100+i,'avg_mfe_60_r':.2,'avg_mae_60_r':.5,'late_entry_signature_count':0} for i in range(9)]
    groups += [
        {'dimension':'symbol','value':'PI/USDT:USDT','count':40,'net_pnl':-20,'avg_mfe_60_r':.2,'avg_mae_60_r':.6,'late_entry_signature_count':0},
        {'dimension':'side','value':'long','count':80,'net_pnl':-30,'avg_mfe_60_r':.3,'avg_mae_60_r':.4,'late_entry_signature_count':0},
        {'dimension':'side','value':'short','count':30,'net_pnl':-10,'avg_mfe_60_r':.3,'avg_mae_60_r':.3,'late_entry_signature_count':0},
        {'dimension':'trade_alignment','value':'counter_regime','count':20,'net_pnl':-15,'avg_mfe_60_r':.2,'avg_mae_60_r':.4,'late_entry_signature_count':0},
        {'dimension':'strategy_group','value':'candidate_c','count':16,'net_pnl':-25,'avg_mfe_60_r':.1,'avg_mae_60_r':.1,'late_entry_signature_count':0},
    ]
    lines=analysis_report._entry_counterfactual_lines({'sample_count':1,'resolved_count':1,'groups':groups})
    text='\n'.join(lines)
    for token in ('PI/USDT:USDT','side=long','side=short','trade_alignment=counter_regime','strategy_group=candidate_c'):
        assert token in text
