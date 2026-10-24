"""Baseline reset history: atomic archive, auth, idempotency, no fake PnL."""
import datetime
import json
import logging
from types import SimpleNamespace
import pytest
import baseline_history as bh
import pnl_store
import web_app


UTC = datetime.timezone.utc

def _old(tmp_path, *, equity=1000.0, when=None):
    pnl_store.save_baseline(str(tmp_path),equity,now=when or
                            datetime.datetime(2026,10,12,10,tzinfo=UTC))


def _summary(profit=142.75,pct=14.275):
    return {'complete':True,'cashflow_adjusted_profit':profit,
            'cashflow_adjusted_return_pct':pct,
            'net_capital_flow_usdt':25.,
            'capital_in_usdt':40.,'capital_out_usdt':15.}


def test_previous_existing_baseline_shows_without_inventing_history(tmp_path):
    _old(tmp_path)
    before=(tmp_path/'pnl_state.json').read_bytes()
    hist=bh.load_history(str(tmp_path))
    assert len(hist)==1 and hist[0]['source']=='imported_existing_baseline'
    assert hist[0]['trade_contribution_usdt'] is None
    assert hist[0]['started_at_date']=='2026-10-12'
    assert (tmp_path/'pnl_state.json').read_bytes()==before


def test_one_reset_archives_prior_and_starts_new_period_atomically(tmp_path):
    _old(tmp_path)
    now=datetime.datetime(2026,10,13,9,50,52,tzinfo=UTC)
    result=bh.reset_with_history(str(tmp_path),1167.75,old_summary=_summary(),
                                 event_id='reset-001',now=now)
    assert not result['already_applied']
    history=bh.load_history(str(tmp_path))
    assert len(history)==2
    old,new=history
    assert old['status']=='closed'
    assert old['ended_at_kst'].startswith('2026-10-13T18:50:52')
    assert old['start_equity_usdt']==1000
    assert old['end_equity_usdt']==1167.75
    assert old['trade_contribution_usdt']==142.75
    assert old['trade_return_pct']==14.275
    assert old['net_capital_flow_usdt']==25
    assert old['capital_in_usdt']==40
    assert old['capital_out_usdt']==15
    assert new['status']=='current' and new['start_equity_usdt']==1167.75
    assert pnl_store.load_baseline(str(tmp_path))==1167.75
    assert pnl_store.load_baseline_metadata(str(tmp_path))['baseline_set_at_ms']==int(now.timestamp()*1000)


def test_multiple_resets_preserve_every_period(tmp_path):
    _old(tmp_path)
    for i in range(4):
        bh.reset_with_history(str(tmp_path),1000+i*10,
                              old_summary=_summary(i*8,0.8*i),
                              event_id=f'reset-{i}')
    rows=bh.load_history(str(tmp_path))
    assert len(rows)==5
    assert sum(e['status']=='current' for e in rows)==1
    assert len({e['id'] for e in rows})==5
    assert rows[-2]['trade_contribution_usdt']==24
    assert rows[-1]['trade_contribution_usdt'] is None


def test_unknown_cashflows_are_recorded_as_unverified_not_zero(tmp_path):
    _old(tmp_path)
    bh.reset_with_history(str(tmp_path),875.,event_id='unknown',
                          old_summary={'complete':False,
                            'cashflow_adjusted_profit':-100,
                            'cashflow_adjusted_return_pct':-10.})
    row=bh.load_history(str(tmp_path))[0]
    assert row['accounting_status']=='cashflow_unverified'
    assert row['trade_contribution_usdt'] is None
    assert row['trade_return_pct'] is None


def test_retry_idempotency_keeps_original_bytes(tmp_path):
    _old(tmp_path)
    bh.reset_with_history(str(tmp_path),1100.,event_id='once')
    before=(tmp_path/'pnl_state.json').read_bytes()
    second=bh.reset_with_history(str(tmp_path),1300.,event_id='once')
    assert second['already_applied']
    assert (tmp_path/'pnl_state.json').read_bytes()==before


def test_archive_failure_does_not_reset_baseline(monkeypatch,tmp_path):
    _old(tmp_path)
    before=(tmp_path/'pnl_state.json').read_bytes()
    def failure(*_a,**_kw):raise OSError('simulated disk outage')
    monkeypatch.setattr(bh.process_lock,'save_json_atomic',failure)
    with pytest.raises(OSError):
        bh.reset_with_history(str(tmp_path),2100.,event_id='failure')
    assert (tmp_path/'pnl_state.json').read_bytes()==before


def test_corrupted_record_cannot_be_overwritten(tmp_path):
    path=tmp_path/'pnl_state.json'
    path.write_text('broken-json-!')
    with pytest.raises(RuntimeError):
        bh.reset_with_history(str(tmp_path),1200.,event_id='broken')
    assert path.read_text()=='broken-json-!'


def test_invalid_equity_and_event_id_rejected(tmp_path):
    _old(tmp_path)
    for amount in (0,-1,float('nan'),float('inf')):
        with pytest.raises(ValueError):
            bh.reset_with_history(str(tmp_path),amount)
    with pytest.raises(ValueError):
        bh.reset_with_history(str(tmp_path),2000,event_id='../escape')


def _client(monkeypatch,tmp_path,equity=1100.0):
    _old(tmp_path)
    summary=_summary()
    state_data=dict(live_equity=equity,equity=equity,baseline_equity=1000.,
                    capital_flow_summary=summary,live_total_profit=142.75)
    class State:
        def snapshot(self): return dict(state_data)
        def update(self,**kwargs):state_data.update(kwargs)
    ctx=SimpleNamespace(dir=str(tmp_path),
                        state=State(),cfg=SimpleNamespace(logger=logging.getLogger('baseline-v22')))
    monkeypatch.setattr(web_app,'get_context',lambda user:ctx)
    client=web_app.app.test_client()
    with client.session_transaction() as sess:
        sess['username']='chickenbananalab'
        sess['authenticated']=True
    return client,ctx


def test_reset_endpoint_archives_and_get_endpoint_lists_history(monkeypatch,tmp_path):
    client,ctx=_client(monkeypatch,tmp_path)
    monkeypatch.setattr(web_app.capital_flow,'cached_summary',lambda *a,**k:_summary())
    monkeypatch.setattr(web_app.capital_flow,'reset_context',lambda *a,**k:{})
    monkeypatch.setattr(web_app.capital_flow,'compute_summary',lambda **k:{'complete':True,
        'cashflow_adjusted_profit':0,'cashflow_adjusted_return_pct':0,
        'net_capital_flow_usdt':0})
    response=client.post('/api/reset_baseline',json={'reset_event_id':'btn-001'})
    assert response.status_code==200 and response.json['ok']
    assert pnl_store.load_baseline(str(tmp_path))==1100
    history=client.get('/api/baseline_history')
    assert history.status_code==200 and history.json['ok']
    rows=history.json['history']
    assert len(rows)==2
    assert rows[0]['status']=='current'
    assert rows[1]['trade_contribution_usdt']==142.75
    assert rows[1]['net_capital_flow_usdt']==25
    assert client.post('/api/reset_baseline',json={'reset_event_id':'btn-001'}).json['already_applied']
    assert len(bh.load_history(str(tmp_path)))==2


def test_history_requires_authentication():
    anon=web_app.app.test_client()
    assert anon.get('/api/baseline_history').status_code in (302,401)


def test_dashboard_contains_accessible_history_next_to_reset():
    from pathlib import Path
    html=(Path(__file__).parents[1]/'templates/dashboard.html').read_text()
    assert 'onclick="resetBaseline(this)">기준 초기화</button>' in html
    assert 'onclick="toggleBaselineHistory(this)"' in html
    assert 'id="baseline-history-rows"' in html
    assert 'trade_contribution_usdt' in html
    assert 'reset_event_id: resetEventId' in html
