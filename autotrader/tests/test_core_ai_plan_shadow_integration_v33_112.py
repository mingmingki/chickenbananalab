import copy
import datetime as dt
import logging
from types import SimpleNamespace

import pytest

import core_ai_plan_shadow_v33_112 as shadow
import trader
import web_app


SYM='BTC/USDT:USDT'
NOW=dt.datetime(2026,10,24,1,0,tzinfo=dt.timezone.utc)


def config(tmp_path):
    (tmp_path/shadow.MARKER).touch()
    return SimpleNamespace(user_dir=str(tmp_path),CORE_AI_STRATEGY_AUTHORITY=True,
                           GPT_PRIMARY_ONLY=True,OPENAI_MODEL='test',
                           RISK_PER_TRADE_PCT=1.,logger=logging.getLogger('test'))


def test_trader_observer_preserves_raw_ai_decision(tmp_path):
    cfg=config(tmp_path);d={'action':'long','confidence':.8,'reasoning':'trend'}
    original=copy.deepcopy(d)
    trader._observe_core_ai_plan_shadow(cfg,SYM,event='primary_decision',
                                       now=NOW,decision=d)
    row=shadow.read_snapshot(tmp_path)['latest'][0]
    assert row['decision']==original
    assert row['contract']['action']=='ENTER_LONG'
    assert d==original


def test_trader_observer_failure_does_not_escape_or_change_decision(tmp_path,monkeypatch):
    def broken(*a,**kw):raise OSError('disk unavailable')
    monkeypatch.setattr(shadow,'capture',broken)
    d={'action':'close','confidence':.8}
    trader._observe_core_ai_plan_shadow(config(tmp_path),SYM,event='primary_decision',now=NOW,decision=d)
    assert d=={'action':'close','confidence':.8}


def test_trader_hook_cannot_observe_candidate_c(tmp_path):
    trader._observe_core_ai_plan_shadow(config(tmp_path),'SOL/USDT:USDT',
                                       event='primary_decision',now=NOW,decision={'action':'short'})
    assert not (tmp_path/shadow.DB_NAME).exists()


def test_route_requires_login():
    web_app.app.config['TESTING']=True
    with web_app.app.test_client() as client:
        response=client.get('/api/ops/core-ai-plan-shadow-v33-112')
    assert response.status_code in (302,401)


def test_authenticated_route_reads_only_the_session_account(tmp_path,monkeypatch):
    cfg=config(tmp_path)
    shadow.capture(cfg,SYM,trader.config.CORE_SYMBOLS,event='prefilter_skip',now=NOW)
    requested=[]
    def context(name):
        requested.append(name)
        return SimpleNamespace(dir=str(tmp_path))
    monkeypatch.setattr(web_app,'get_context',context)
    web_app.app.config['TESTING']=True
    with web_app.app.test_client() as client:
        with client.session_transaction() as session:
            session['username']='research-test'
            session['authenticated']=True
        response=client.get('/api/ops/core-ai-plan-shadow-v33-112')
    assert response.status_code==200
    data=response.get_json()
    assert data['report']['observations']==1
    assert data['execution_permission'] is False
    assert requested==['research-test']
