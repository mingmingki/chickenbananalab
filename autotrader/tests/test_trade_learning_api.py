from types import SimpleNamespace
import web_app


def _auth_client(monkeypatch, tmp_path):
    cfg=SimpleNamespace(user_dir=str(tmp_path), GEMINI_API_KEY='g', OPENAI_API_KEY='o', GEMINI_MODEL='gm', OPENAI_MODEL='om')
    ctx=SimpleNamespace(cfg=cfg,dir=str(tmp_path),username='tester')
    monkeypatch.setattr(web_app,'get_context',lambda _u:ctx)
    c=web_app.app.test_client()
    with c.session_transaction() as s:
        s['authenticated']=True; s['username']='tester'
    return c,ctx


def test_analysis_endpoints_require_login():
    c=web_app.app.test_client()
    assert c.get('/api/analysis/summary').status_code in (302,401)
    assert c.post('/api/analysis/run',json={}).status_code in (302,401)


def test_run_analysis_uses_local_coordinator_only(tmp_path,monkeypatch):
    c,ctx=_auth_client(monkeypatch,tmp_path)
    called=[]
    monkeypatch.setattr(web_app.trade_learning_cache,'run_analysis',lambda user_dir: called.append(user_dir) or {'analysis':{'summary':{'completed_trades':3},'trades':[]},'stale':False})
    res=c.post('/api/analysis/run',json={})
    assert res.status_code==200
    assert res.get_json()['ok'] is True
    assert called==[str(tmp_path)]


def test_analysis_get_routes_return_cached_data(tmp_path,monkeypatch):
    c,ctx=_auth_client(monkeypatch,tmp_path)
    cache={'analysis':{'summary':{'completed_trades':1},'trades':[{'trade_id':'abc','symbol':'BTC'}],'tf_combinations':[],'dimensions':[],'coverage':{}},'stale':False,'generated_at':'x'}
    monkeypatch.setattr(web_app.trade_learning_cache,'get_cached',lambda _u:cache)
    monkeypatch.setattr(web_app.ai_strategy_review_log,'recent',lambda *_a,**_k:[{'status':'complete'}])
    monkeypatch.setattr(web_app.strategy_learning,'recent_hypotheses',lambda *_a,**_k:[{'hypothesis_id':'h'}])
    monkeypatch.setattr(web_app.strategy_learning,'latest_hypotheses',lambda *_a,**_k:{'h':{'hypothesis_id':'h'}})
    assert c.get('/api/analysis/summary').get_json()['analysis']['summary']['completed_trades']==1
    assert c.get('/api/analysis/trades').get_json()['trades'][0]['trade_id']=='abc'
    assert c.get('/api/analysis/trade/abc').get_json()['trade']['symbol']=='BTC'
    assert c.get('/api/analysis/reviews').get_json()['reviews'][0]['status']=='complete'
    assert c.get('/api/analysis/learning').get_json()['latest']['h']['hypothesis_id']=='h'


def test_review_now_is_background_only(tmp_path,monkeypatch):
    c,ctx=_auth_client(monkeypatch,tmp_path)
    called=[]
    monkeypatch.setattr(web_app,'_queue_manual_strategy_review',lambda context: called.append(context) or True)
    res=c.post('/api/analysis/review-now',json={})
    assert res.status_code==202
    assert called==[ctx]


def test_analysis_summary_does_not_embed_trade_rows(tmp_path,monkeypatch):
    c,_ctx=_auth_client(monkeypatch,tmp_path)
    cache={'analysis':{'summary':{'completed_trades':1},'trades':[{'trade_id':'large-row'}],'dimensions':[],'tf_combinations':[],'coverage':{}},'stale':False}
    monkeypatch.setattr(web_app.trade_learning_cache,'get_cached',lambda _u:cache)
    data=c.get('/api/analysis/summary').get_json()
    assert 'trades' not in data['analysis']
