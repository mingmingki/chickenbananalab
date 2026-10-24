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

def test_daily_completion_endpoint_executes_and_returns_coverage_and_cohort(tmp_path,monkeypatch):
    import json
    c,ctx=_auth_client(monkeypatch,tmp_path)
    monkeypatch.setattr(web_app.config,"PROJECT_DIR",str(tmp_path))
    (tmp_path/"DEPLOYMENT_IDENTITY.json").write_text(json.dumps({"release":str(tmp_path),"created_at":"2026-10-06T12:20:48+09:00"}))
    monkeypatch.setattr(web_app.trade_learning_cache,"get_cached",lambda _u:{"analysis":{}})
    monkeypatch.setattr(web_app.learning_state,"load_active_state",lambda _u:({},True))
    monkeypatch.setattr(web_app.learning_shadow,"summarize_pattern_evidence",lambda _u:{})
    monkeypatch.setattr(web_app.learning_control,"get",lambda _u:{"live_enabled":False})
    monkeypatch.setattr(web_app.entry_counterfactual_shadow,"summary",lambda *_a,**_k:{"sample_count":0})
    monkeypatch.setattr(web_app.entry_counterfactual_shadow,"_read_latest",lambda _u:{
        "post1":{"entry_id":"post1","entry_time":"2026-10-06T12:30:00+09:00","resolved":False,
                 "late_entry_signature":False,"immediate_adverse":False,"clean_follow_through":False}
    })
    monkeypatch.setattr(web_app.exit_reentry_shadow,"recent",lambda *_a,**_k:[])
    monkeypatch.setattr(web_app.exit_reentry_shadow,"summarize_three_way",lambda _r:{"sample_count":0})
    coverage={"canonical_completed_trades":10,"feature_complete":2,"partially_enriched":8,
              "feature_missing_reasons":{"candle_finality":8},"by_strategy_group":{"core":{"total":10,"complete":2,"partial":8,"missing_reasons":{"candle_finality":8}}}}
    monkeypatch.setattr(web_app.trade_learning_cache,"get_cached",lambda _u:{"analysis":{"coverage":coverage}})
    def forbidden_recompute(_u):
        raise AssertionError("GET must use cached coverage")
    monkeypatch.setattr(web_app.trade_pattern_analysis,"analyze",forbidden_recompute)
    monkeypatch.setattr(web_app.trade_learning_lifecycle,"build_completed_lifecycles",lambda _u:[])
    monkeypatch.setattr(web_app.operating_costs,"build_operating_cost_summary",lambda _u:{"server":{"monthly_estimate_usd":28.91}})
    res=c.get("/api/analysis/daily-completion")
    assert res.status_code==200
    data=res.get_json()["daily_completion"]
    assert data["data_quality"]["feature_missing_reasons"]["candle_finality"]==8
    assert data["post_release_cohort"]["trade_count"]==0
    assert data["post_release_cohort"]["profit_factor"] is None
    assert data["post_release_entry"]["sample_count"]==1
