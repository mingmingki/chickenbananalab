from pathlib import Path
from types import SimpleNamespace

import pytest

import web_app

HTML = Path(__file__).resolve().parents[1] / 'templates' / 'dashboard.html'


def _auth_client(monkeypatch, tmp_path):
    cfg = SimpleNamespace(
        user_dir=str(tmp_path), logger=SimpleNamespace(exception=lambda *a, **k: None),
        RISK_PER_TRADE_PCT=1.0, LEVERAGE=5, STOP_LOSS_PCT=2.0, TAKE_PROFIT_PCT=4.0,
        MAX_DAILY_LOSS_PCT=5.0, MIN_CONFIDENCE=.6, CANDIDATE_C_SYMBOLS=[],
        CANDIDATE_C_ENABLED=True, CANDIDATE_C_LIVE_EXECUTE=True,
    )
    snap = {
        'running': True,
        'symbols': {
            'BTC/USDT:USDT': {
                'position': {'side':'long','contracts':2.0,'entry_price':84000,'mark_price':84200,'unrealized_pnl':4.0},
                'live_position': {'side':'long','contracts':2.0,'entry_price':84000,'mark_price':84200,'unrealized_pnl':4.0},
                'live_protection': {'status':'VERIFIED','sl_price':82320,'tp_price':87360,'sz':2.0},
            }
        }
    }
    state = SimpleNamespace(snapshot=lambda: snap)
    ctx = SimpleNamespace(cfg=cfg, dir=str(tmp_path), username='tester', state=state, log_handler=SimpleNamespace(get_all=lambda: ['INFO ok']))
    monkeypatch.setattr(web_app, 'get_context', lambda _u: ctx)
    c = web_app.app.test_client()
    with c.session_transaction() as s:
        s['authenticated'] = True
        s['username'] = 'tester'
    return c


def _analysis_payload():
    return {
        'analysis': {
            'summary': {'completed_trades':100},
            'coverage': {'canonical_completed_trades':100,'lifecycle_matched':95,'feature_complete':40,'partially_enriched':60,'unmatched_or_excluded':5},
            'dimensions': [
                {'dimension':'symbol','value':'BTC/USDT:USDT','condition':'symbol:BTC/USDT:USDT','count':50,'win_rate':40,'profit_factor':1.2,'net_pnl':10,'sample_class':'established_sample'},
                {'dimension':'side','value':'long','condition':'side:long','count':70,'win_rate':35,'profit_factor':.9,'net_pnl':-8,'sample_class':'established_sample'},
                {'dimension':'gpt_confidence','value':'0.70-0.79','condition':'gpt_confidence:0.70-0.79','count':55,'win_rate':45,'profit_factor':1.3,'net_pnl':12,'sample_class':'established_sample'},
            ],
            'tf_combinations': [{'dimension':'tf_combo:3m+5m','value':'aligned','condition':'tf_combo:3m+5m:aligned','count':30,'win_rate':43,'profit_factor':1.1,'net_pnl':4,'sample_class':'watch'}],
        },
        'generated_at':'2026-09-25T09:00:00+09:00',
    }


def test_report_routes_require_login():
    c=web_app.app.test_client()
    assert c.post('/api/analysis/report').status_code in (302,401)
    assert c.get('/api/analysis/reports').status_code in (302,401)


def test_report_generation_uses_current_analysis_and_is_append_only(monkeypatch,tmp_path):
    c=_auth_client(monkeypatch,tmp_path)
    monkeypatch.setattr(web_app.trade_learning_cache,'run_analysis',lambda _u:_analysis_payload())
    monkeypatch.setattr(web_app.pnl_reconciliation,'canonical_trade_stats',lambda _u:{'count':100,'win_rate':37.0,'total_pnl':20,'total_fee':30,'net_pnl':-10,'profit_factor':.9,'avg_win':5,'avg_loss':-3})
    monkeypatch.setattr(web_app.learning_control,'get',lambda _u:{'live_enabled':False})
    monkeypatch.setattr(web_app.learning_state,'load_active_state',lambda _u:({},True))
    monkeypatch.setattr(web_app.learning_state,'recent_transitions',lambda _u,limit=50:[])
    monkeypatch.setattr(web_app.learning_shadow,'recent_counterfactuals',lambda _u,limit=100:[])
    monkeypatch.setattr(web_app.ai_strategy_review_log,'recent',lambda _u,limit=50:[])
    r=c.post('/api/analysis/report')
    assert r.status_code==200
    data=r.get_json()
    assert data['ok'] is True
    assert data['report']['snapshot']['coverage']['completed_trades']==100
    assert 'BTC/USDT:USDT' in data['report']['text']
    assert 'RISK_PER_TRADE_PCT' not in data['report']['text']
    recent=c.get('/api/analysis/reports?limit=5').get_json()
    assert recent['ok'] is True and len(recent['reports'])==1
    assert recent['reports'][0]['report_id']==data['report']['report_id']


def test_dashboard_has_report_buttons_tab_and_clipboard_copy():
    text=HTML.read_text()
    assert '📋 리포트 생성' in text
    assert 'ChatGPT용 복사' in text
    assert '최근 리포트 보기' in text
    assert 'data-learning-tab="report"' in text
    assert '/api/analysis/report' in text
    assert '/api/analysis/reports' in text
    assert 'navigator.clipboard.writeText' in text

def test_report_overall_uses_completed_lifecycle_economic_metrics_not_close_only_stats(monkeypatch,tmp_path):
    c=_auth_client(monkeypatch,tmp_path)
    payload=_analysis_payload()
    payload['analysis']['summary'].update({
        'completed_trades':100,'count':100,'win_rate':35.0,'gross_pnl':10.0,'fee':30.0,
        'net_adjustment':-5.0,'net_pnl':-25.0,'profit_factor':0.8,'avg_win':4.0,'avg_loss':-2.0,
    })
    monkeypatch.setattr(web_app.trade_learning_cache,'run_analysis',lambda _u:payload)
    monkeypatch.setattr(web_app.pnl_reconciliation,'canonical_trade_stats',lambda _u:{
        'count':100,'win_rate':55.0,'total_pnl':100.0,'total_fee':10.0,'net_pnl':90.0,
        'profit_factor':2.0,'avg_win':9.0,'avg_loss':-1.0,
    })
    monkeypatch.setattr(web_app.account_reconciliation_bridge,'realized_economic_summary',lambda _u:{
        'total':{'count':100,'completed_count':100,'win_rate':35.0,'gross_pnl':10.0,'fee':30.0,
                 'net_adjustment':-5.0,'net_pnl':-25.0,'profit_factor':0.8}
    })
    monkeypatch.setattr(web_app.learning_control,'get',lambda _u:{'live_enabled':False})
    monkeypatch.setattr(web_app.learning_state,'load_active_state',lambda _u:({},True))
    monkeypatch.setattr(web_app.learning_state,'recent_transitions',lambda _u,limit=50:[])
    monkeypatch.setattr(web_app.learning_shadow,'recent_counterfactuals',lambda _u,limit=100:[])
    monkeypatch.setattr(web_app.ai_strategy_review_log,'recent',lambda _u,limit=50:[])
    r=c.post('/api/analysis/report')
    assert r.status_code==200
    report=r.get_json()['report']
    overall=report['snapshot']['overall']
    assert overall['net_pnl']==pytest.approx(-25.0)
    assert overall['gross_pnl']==pytest.approx(10.0)
    assert overall['fees']==pytest.approx(-30.0)
    assert overall['net_adjustment']==pytest.approx(-5.0)
    assert '정산조정: -5.00' in report['text']


def test_report_snapshot_carries_filter_counterfactual_from_analysis(monkeypatch,tmp_path):
    c=_auth_client(monkeypatch,tmp_path)
    payload=_analysis_payload()
    payload['analysis']['summary'].update({'count':100,'gross_pnl':0,'fee':0,'net_adjustment':0,'net_pnl':0,'profit_factor':1,'win_rate':50,'avg_win':1,'avg_loss':-1})
    payload['analysis']['filter_counterfactual']={'baseline':{'count':100,'net_pnl':0},'filters':{},'union':{'label':'U','excluded_count':1,'remaining_count':99,'remaining':{'net_pnl':5},'net_improvement':5},'overlap_by_match_count':{'0':99,'1':1}}
    monkeypatch.setattr(web_app.trade_learning_cache,'run_analysis',lambda _u:payload)
    monkeypatch.setattr(web_app.learning_control,'get',lambda _u:{'live_enabled':False})
    monkeypatch.setattr(web_app.learning_state,'load_active_state',lambda _u:({},True))
    monkeypatch.setattr(web_app.learning_state,'recent_transitions',lambda _u,limit=50:[])
    monkeypatch.setattr(web_app.learning_shadow,'recent_counterfactuals',lambda _u,limit=100:[])
    monkeypatch.setattr(web_app.ai_strategy_review_log,'recent',lambda _u,limit=50:[])
    r=c.post('/api/analysis/report')
    assert r.status_code==200
    snap=r.get_json()['report']['snapshot']
    assert snap['filter_counterfactual']['union']['net_improvement']==5


def test_report_snapshot_carries_exit_reentry_analysis(monkeypatch,tmp_path):
    c=_auth_client(monkeypatch,tmp_path)
    payload=_analysis_payload()
    payload['analysis']['summary'].update({'count':100,'gross_pnl':0,'fee':0,'net_adjustment':0,'net_pnl':0,'profit_factor':1,'win_rate':50,'avg_win':1,'avg_loss':-1})
    payload['analysis']['exit_reentry']={
        'ai_close_lifecycle_count':2,'ai_close_lifecycle_net':-40.0,
        'ai_close_final_close_net':-18.0,'ai_close_reduce_net':-22.0,
        'shadow_sample_count':1,'resolved_count':1,'unresolved_count':0,
        'same_side_reentry_rate_30m':0.0,'same_side_reentry_rate_60m':100.0,
        'same_side_reentry_rate_120m':100.0,'subsequent_lifecycle_net':-7.0,'churn_cycle_net':-47.0,
    }
    monkeypatch.setattr(web_app.trade_learning_cache,'run_analysis',lambda _u:payload)
    monkeypatch.setattr(web_app.learning_control,'get',lambda _u:{'live_enabled':False})
    monkeypatch.setattr(web_app.learning_state,'load_active_state',lambda _u:({},True))
    monkeypatch.setattr(web_app.learning_state,'recent_transitions',lambda _u,limit=50:[])
    monkeypatch.setattr(web_app.learning_shadow,'recent_counterfactuals',lambda _u,limit=100:[])
    monkeypatch.setattr(web_app.ai_strategy_review_log,'recent',lambda _u,limit=50:[])
    r=c.post('/api/analysis/report')
    assert r.status_code==200
    snap=r.get_json()['report']['snapshot']
    assert snap['exit_reentry']['ai_close_lifecycle_net']==pytest.approx(-40.0)


def test_report_overall_matches_dashboard_realized_economic_summary(monkeypatch,tmp_path):
    c=_auth_client(monkeypatch,tmp_path)
    payload=_analysis_payload()
    payload['analysis']['summary'].update({
        'completed_trades':100,'count':100,'win_rate':35.0,'gross_pnl':10.0,'fee':30.0,
        'net_adjustment':-5.0,'net_pnl':-25.0,'profit_factor':0.8,'avg_win':4.0,'avg_loss':-2.0,
    })
    monkeypatch.setattr(web_app.trade_learning_cache,'run_analysis',lambda _u:payload)
    monkeypatch.setattr(web_app.account_reconciliation_bridge,'realized_economic_summary',lambda _u:{
        'total':{'count':100,'completed_count':100,'gross_pnl':7.5,'fee':31.25,
                 'net_adjustment':-2.0,'net_pnl':-25.75,'win_rate':35.0,'profit_factor':0.8}
    })
    monkeypatch.setattr(web_app.learning_control,'get',lambda _u:{'live_enabled':False})
    monkeypatch.setattr(web_app.learning_state,'load_active_state',lambda _u:({},True))
    monkeypatch.setattr(web_app.learning_state,'recent_transitions',lambda _u,limit=50:[])
    monkeypatch.setattr(web_app.learning_shadow,'recent_counterfactuals',lambda _u,limit=100:[])
    monkeypatch.setattr(web_app.ai_strategy_review_log,'recent',lambda _u,limit=50:[])
    r=c.post('/api/analysis/report')
    assert r.status_code==200
    overall=r.get_json()['report']['snapshot']['overall']
    assert overall['gross_pnl']==pytest.approx(7.5)
    assert overall['fees']==pytest.approx(-31.25)
    assert overall['net_adjustment']==pytest.approx(-2.0)
    assert overall['net_pnl']==pytest.approx(-25.75)
    # completed-lifecycle quality metrics remain analysis metrics
    assert overall['avg_win']==pytest.approx(4.0)
    assert overall['avg_loss']==pytest.approx(-2.0)
