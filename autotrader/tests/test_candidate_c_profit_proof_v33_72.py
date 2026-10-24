"""v33-72 exact fill ownership, no-double-count, reporting tests."""
from pathlib import Path
import copy
import json
import hashlib
import pytest
import candidate_c_profit_evidence_v33_72 as p


USER=Path('/opt/autotrader/users/chickenbananalab')
def source():
    proof,sha=p.bounded_report()
    r=[json.loads(x) for x in (USER/'trades_log.jsonl').read_text().splitlines() if x.strip()]
    return r,proof,sha

def test_integrity_and_25_original_reduce_events():
    journal,proof,sha=source()
    report=p.analyze(journal,proof)
    assert report['verified_reduce_events']==25
    assert report['gross_mismatch_count']==18
    assert report['cohorts']['full_record']['events']==97
    assert report['cohorts']['full_record']['event_profit_factor']<1
    assert report['recent_negative_edge_advisory'] is True

def test_never_double_counts_original_order_cash():
    rows,proof,_=source()
    a=p.analyze(rows,proof)
    assert abs(float(a['exact_vs_current_fallback_delta_usdt'])+2.5256096)<.0001
    assert float(a['verified_exchange_close_order_cash_usdt'])< -130
    assert a['caution'].startswith('Do not add')
    assert a['orders_or_strategy_modified'] is False

def test_unchanged_journal_identity_fails_closed():
    rows,proof,_=source()
    i=proof['cases'][0]['row_index']
    rows[i]=dict(rows[i],pnl=999)
    with pytest.raises(ValueError,match='rewritten'):
        p.analyze(rows,proof)

def test_previously_booked_net_value_fails_closed():
    rows,proof,_=source()
    i=proof['cases'][0]['row_index']
    rows[i]=dict(rows[i],okx_net_pnl=-123)
    with pytest.raises(ValueError,match='rewritten'):
        p.analyze(rows,proof)

def test_duplicate_proof_case_fails_closed():
    rows,proof,_=source()
    proof=copy.deepcopy(proof)
    proof['cases'][1]['row_index']=proof['cases'][0]['row_index']
    with pytest.raises(ValueError):
        p.analyze(rows,proof)

def test_appended_journal_events_do_not_invalidate_historical_proof():
    rows,proof,_=source()
    rows.append({'type':'open','strategy_group':'candidate_c','symbol':'DOGE/USDT:USDT',
                 'time':'2026-10-21T00:00:00','execution_id':'UNRELATED'})
    report=p.analyze(rows,proof)
    assert report['verified_reduce_events']==25

def test_unknown_numbers_and_invalid_sha_fail():
    with pytest.raises(ValueError):p.decimal(None)
    with pytest.raises(ValueError):p.decimal(float('inf'))

def test_tampered_proof_checksum_fails_closed(tmp_path,monkeypatch):
    report,checksum=p.bounded_report()
    pth=tmp_path/'audit.json'
    pth.write_text(json.dumps(report))
    (tmp_path/'sha256.txt').write_text('deadbeef  '+str(pth))
    monkeypatch.setattr(p,'REPORT',pth)
    monkeypatch.setattr(p,'SHA',tmp_path/'sha256.txt')
    with pytest.raises(ValueError,match='checksum'):
        p.bounded_report()

def test_event_pf_does_not_misrepresent_lifecycle():
    result=p.summarize_events([
      dict(net=p.decimal('-2')),dict(net=p.decimal('1')),
      dict(net=p.decimal('1'))])
    assert result['not_independent_trade_lifecycles'] is True
    assert result['net_usdt']==0
    assert result['event_profit_factor']==1

def test_secure_endpoint_rejects_guest_and_supports_user():
    import web_app
    with web_app.app.test_client() as client:
        unauth=client.get('/api/ops/candidate-c-profit-proof-v33-72')
        assert unauth.status_code==401
        with client.session_transaction() as session:
            session['username']='chickenbananalab'
            session['authenticated']=True
        response=client.get('/api/ops/candidate-c-profit-proof-v33-72')
        assert response.status_code==200
        payload=response.get_json()['report']
        assert payload['verified_reduce_events']==25
        assert 'evidence_rows' not in payload
        details=client.get('/api/ops/candidate-c-profit-proof-v33-72?details=1').get_json()['report']
        assert len(details['evidence_rows'])==25

def test_trading_adapter_unchanged():
    latest=Path('candidate_c_trader_adapter.py')
    old=Path('/opt/autotrader-releases/candidate_c_4h_cost_visibility_v33_71_20261020KST/candidate_c_trader_adapter.py')
    assert latest.read_bytes()==old.read_bytes()
