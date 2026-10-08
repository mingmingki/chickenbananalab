from pathlib import Path
from types import SimpleNamespace

import learning_state
import web_app

HTML=Path(__file__).resolve().parents[1]/'templates'/'dashboard.html'


def _auth_client(monkeypatch,tmp_path):
    cfg=SimpleNamespace(user_dir=str(tmp_path),logger=SimpleNamespace(exception=lambda *a,**k:None))
    ctx=SimpleNamespace(cfg=cfg,dir=str(tmp_path),username='tester')
    monkeypatch.setattr(web_app,'get_context',lambda _u:ctx)
    c=web_app.app.test_client()
    with c.session_transaction() as s:
        s['authenticated']=True; s['username']='tester'
    return c


def test_self_learning_routes_require_login():
    c=web_app.app.test_client()
    assert c.get('/api/analysis/self-learning').status_code in (302,401)
    assert c.post('/api/analysis/self-learning/control',json={'live_enabled':True}).status_code in (302,401)


def test_control_api_requires_json_boolean_and_persists(monkeypatch,tmp_path):
    c=_auth_client(monkeypatch,tmp_path)
    assert c.post('/api/analysis/self-learning/control',data='live_enabled=true').status_code==415
    bad=c.post('/api/analysis/self-learning/control',json={'live_enabled':'true'})
    assert bad.status_code==400
    on=c.post('/api/analysis/self-learning/control',json={'live_enabled':True})
    assert on.status_code==200 and on.get_json()['control']['live_enabled'] is True
    status=c.get('/api/analysis/self-learning').get_json()
    assert status['control']['live_enabled'] is True


def test_self_learning_status_reports_state_counts_and_live_patterns(monkeypatch,tmp_path):
    c=_auth_client(monkeypatch,tmp_path)
    ev={'dimension':'symbol','value':'BTC/USDT:USDT','sample_count':80,'coverage':.95,
        'resolved_count':30,'shadow_benefit_net':10,'recent_benefit_net':4,
        'recent_direction':'negative','long_direction':'negative','outlier_share':.1,
        'checkpoint_streak':3,'deteriorating_checkpoints':0,'material_pf_reversal':False,
        'data_integrity_issue':False}
    learning_state.append_transition(str(tmp_path),'p1',None,'LIVE_BOUNDED','test',ev)
    data=c.get('/api/analysis/self-learning').get_json()
    assert data['ok'] is True
    assert data['state_counts']['LIVE_BOUNDED']==1
    assert data['live_patterns'][0]['pattern_id']=='p1'
    assert data['safe'] is True


def test_cross_site_control_is_rejected(monkeypatch,tmp_path):
    c=_auth_client(monkeypatch,tmp_path)
    r=c.post('/api/analysis/self-learning/control',json={'live_enabled':True},headers={'Sec-Fetch-Site':'cross-site'})
    assert r.status_code==403


def test_dashboard_has_explicit_self_learning_control_and_explanation():
    text=HTML.read_text()
    assert 'data-learning-tab="self-learning"' in text
    assert '자가학습' in text
    assert 'id="self-learning-live-toggle"' in text
    assert '실전반영 OFF' in text
    assert 'LIVE_BOUNDED' in text
    assert '/api/analysis/self-learning/control' in text
    assert '/api/analysis/self-learning' in text
