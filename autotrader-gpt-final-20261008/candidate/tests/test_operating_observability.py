import datetime as dt
import json
from types import SimpleNamespace
import pytest

KST=dt.timezone(dt.timedelta(hours=9))
NOW=dt.datetime(2026,10,6,9,0,tzinfo=KST)


def write_rows(path, rows):
    path.write_text(''.join(json.dumps(row)+'\n' for row in rows),encoding='utf-8')


def test_kst_today_rolling_24h_and_monthly_projection_are_distinct(tmp_path):
    from operating_costs import build_operating_cost_summary
    path=tmp_path/'token_usage.jsonl'
    write_rows(path,[{'time':'2026-10-06T00:00:00Z','cost_usd':.1},
                     {'time':'2026-10-05T15:00:00Z','cost_usd':.05},
                     {'time':'2026-10-05T09:00:00','cost_usd':.2},
                     {'time':'2026-10-05T08:59:59','cost_usd':.4},
                     {'time':'2026-10-01T08:00:00','cost_usd':1},
                     {'time':'2026-10-06T09:00:01','cost_usd':9}])
    before=path.read_bytes()
    s=build_operating_cost_summary(str(tmp_path),now=NOW)
    assert s['today_cost_usd']==.15
    assert s['rolling_24h_cost_usd']==.35
    assert s['month_to_date_cost_usd']==1.75
    assert s['projected_monthly_ai_cost_usd']==pytest.approx(.35*730/24)
    assert s['timezone']=='Asia/Seoul'
    assert s['estimate_only'] is True
    assert path.read_bytes()==before


@pytest.mark.parametrize('bad_cost',['NaN','Infinity',-2,'bad',{},None])
def test_corrupt_or_negative_costs_do_not_poison_json(tmp_path,bad_cost):
    from operating_costs import build_operating_cost_summary
    path=tmp_path/'token_usage.jsonl'
    write_rows(path,[{'time':'2026-10-06T08:00:00','cost_usd':bad_cost},
                     {'time':'bad','cost_usd':9},['invalid'],
                     {'time':'2026-10-06T08:00:00','cost_usd':.2,'provider':'openai','purpose':'review'}])
    with path.open('a') as f:f.write('{partial\n')
    s=build_operating_cost_summary(str(tmp_path),now=NOW)
    assert s['rolling_24h_cost_usd']==.2
    assert s['by_provider_24h']['openai']['cost_usd']==.2
    json.dumps(s,allow_nan=False)


def test_server_estimate_matches_verified_hardware_and_list_price(tmp_path):
    from operating_costs import build_operating_cost_summary
    s=build_operating_cost_summary(str(tmp_path),now=NOW)
    server=s['server']
    assert server['estimate_available'] is True
    assert server['machine_type']=='e2-medium'
    assert server['disk_type']=='pd-standard' and server['disk_gb']==20
    assert server['external_ipv4']=='34.28.148.113'
    assert server['monthly_estimate_usd']==pytest.approx(.03350571*730+.005*730+.04*20)
    assert server['estimate_only'] is True
    assert s['usage_log_available'] is False
    assert not tmp_path.exists() or list(tmp_path.iterdir())==[]


def test_unverified_server_assumptions_do_not_fabricate_cost(tmp_path):
    from operating_costs import build_operating_cost_summary
    s=build_operating_cost_summary(str(tmp_path),now=NOW,assumptions={})
    assert s['server']['estimate_available'] is False
    assert s['projected_monthly_total_usd'] is None


def test_review_context_ignores_manual_ids_and_uses_latest_scheduled_kst_window():
    from analysis_ops_status import build_analysis_ops_status
    reviews=[{'review_window_id':'20261005-18','status':'complete'},
             {'review_window_id':'manual-20261006-09','status':'complete'},
             {'review_window_id':'20261006-00','status':'no_new_sample'},
             {'review_window_id':'20261006-03','status':'complete'},None]
    s=build_analysis_ops_status(reviews,now=NOW.astimezone(dt.timezone.utc))
    assert s['scheduler']['last_window_id']=='20261006-00'
    assert s['scheduler']['next_window_start']=='2026-10-06T12:00:00+09:00'
    assert s['scheduler']['last_paid_ai'] is False
    assert s['scheduler']['last_cost_usd']==0
    assert s['local_analysis']['paid_ai'] is False
    assert s['manual_review']['paid_ai'] is True
    assert s['local_analysis']['live_trading_effect'] is False
    assert s['manual_review']['live_trading_effect'] is False


def auth_client(monkeypatch,tmp_path):
    import web_app
    ctx=SimpleNamespace(dir=str(tmp_path),username='tester')
    monkeypatch.setattr(web_app,'get_context',lambda _u:ctx)
    c=web_app.app.test_client()
    with c.session_transaction() as s:s.update(authenticated=True,username='tester')
    return c,web_app


def test_operating_cost_api_requires_login_and_is_get_only(monkeypatch,tmp_path):
    import web_app
    assert web_app.app.test_client().get('/api/operating-costs').status_code in (302,401)
    c,_=auth_client(monkeypatch,tmp_path)
    assert c.post('/api/operating-costs',json={}).status_code==405


def test_get_apis_read_logs_without_paid_review_or_trading_mutations(monkeypatch,tmp_path):
    c,w=auth_client(monkeypatch,tmp_path)
    def forbidden(*a,**k):raise AssertionError('mutation/paid review forbidden')
    monkeypatch.setattr(w.ai_strategy_review,'generate_review',forbidden)
    monkeypatch.setattr(w.trade_learning_cache,'run_analysis',forbidden)
    monkeypatch.setattr(w,'_queue_manual_strategy_review',forbidden)
    write_rows(tmp_path/'token_usage.jsonl',[{'time':dt.datetime.now(KST).isoformat(),'cost_usd':.04}])
    write_rows(tmp_path/'ai_review_reports.jsonl',[{'review_window_id':'20261006-00','status':'no_new_sample'}])
    before={p.name:p.read_bytes() for p in tmp_path.iterdir()}
    for _ in range(2):
        res=c.get('/api/operating-costs')
        assert res.status_code==200
        assert res.get_json()['summary']['today_cost_usd']==.04
        r=c.get('/api/analysis/reviews').get_json()
        assert r['analysis_ops']['scheduler']['last_paid_ai'] is False
        assert r['reviews'][0]['status']=='no_new_sample'
    assert {p.name:p.read_bytes() for p in tmp_path.iterdir()}==before


def test_missing_usage_is_unknown_not_known_zero_ai_cost(tmp_path):
    from operating_costs import build_operating_cost_summary
    s=build_operating_cost_summary(str(tmp_path),now=NOW)
    assert s['usage_log_available'] is False
    for key in ('today_cost_usd','rolling_24h_cost_usd','month_to_date_cost_usd','projected_monthly_ai_cost_usd'):
        assert s[key] is None
    assert s['server']['estimate_available'] is True


def test_scheduled_outcome_is_independent_of_visible_manual_history_limit(monkeypatch,tmp_path):
    c,_=auth_client(monkeypatch,tmp_path)
    rows=[{'review_window_id':'20261006-00','status':'no_new_sample'}]
    rows += [{'review_window_id':'manual-'+str(i),'status':'complete'} for i in range(31)]
    write_rows(tmp_path/'ai_review_reports.jsonl',rows)
    r=c.get('/api/analysis/reviews?limit=1').get_json()
    assert len(r['reviews'])==1 and r['reviews'][0]['review_window_id']=='manual-30'
    assert r['analysis_ops']['scheduler']['last_window_id']=='20261006-00'
    assert r['analysis_ops']['scheduler']['last_paid_ai'] is False


def test_corrupt_finite_costs_cannot_overflow_response_json(tmp_path):
    from operating_costs import build_operating_cost_summary
    write_rows(tmp_path/'token_usage.jsonl',[{'time':'2026-10-06T08:00:00','cost_usd':1e308}]*2)
    s=build_operating_cost_summary(str(tmp_path),now=NOW)
    json.dumps(s,allow_nan=False)
    assert s['invalid_usage_rows']==2
