"""v33-11 Candidate C exact fill reconciliation and immutable sidecar."""
import json
from datetime import datetime,timezone,timedelta
from types import SimpleNamespace
import pytest
import candidate_c_fill_reconciliation_v33_11 as f
import candidate_c_sidecar_quality_v33_11 as q

KST=timezone(timedelta(hours=9))
NOW=datetime(2026,10,15,15,10,tzinfo=KST)

def order():
    return dict(id='OID',status='closed',side='buy',filled=.83,
        info=dict(ordId='OID',clOrdId='CID',reduceOnly='true',
                  state='filled',side='buy'))

def fill(tid='F1',qty='.83',px='.08165',pnl='.6557',fee='-.03388475'):
    return dict(id=tid,order='OID',side='buy',amount=float(qty),price=float(px),
        info=dict(tradeId=tid,ordId='OID',side='buy',feeCcy='USDT',
                  fillSz=qty,fillPx=px,fillPnl=pnl,fee=fee))

def good(**kw):
    return f.verified_reduce('DOGE/USDT:USDT','short','.08244','.83','1000',
                             'OID','CID',order(),[fill()],**kw)

def test_exact_remote_order_fill_price_not_recorded_estimate():
    p=good()
    assert p is not None
    assert p['vwap']==pytest.approx(.08165)
    assert p['filled_contracts']==pytest.approx(.83)
    assert p['gross_pnl']==pytest.approx(.6557)
    assert p['net_ex_funding_and_entry_fees']==pytest.approx(.62181525)
    assert p['price_basis']=='verified_order_fills'

@pytest.mark.parametrize('mutation',[
    lambda o,v:o['info'].update(ordId='WRONG'),
    lambda o,v:o['info'].update(clOrdId='WRONG'),
    lambda o,v:o['info'].update(reduceOnly='false'),
    lambda o,v:o.update(status='open'),
    lambda o,v:o.update(filled=.6),
    lambda o,v:v[0]['info'].update(ordId='WRONG'),
    lambda o,v:v[0]['info'].update(feeCcy='BTC'),
    lambda o,v:v[0]['info'].update(fillPnl='0'),
    lambda o,v:v[0]['info'].pop('fee'),
    lambda o,v:v[0]['info'].update(side='sell'),
    lambda o,v:v.append(dict(v[0])),
])
def test_missing_or_inconsistent_exchange_proof_cannot_claim_verified(mutation):
    o=order();v=[fill()];mutation(o,v)
    assert f.verified_reduce('DOGE/USDT:USDT','short','.08244','.83','1000',
                             'OID','CID',o,v) is None

def test_sidecar_never_mutates_original_estimated_trade(tmp_path):
    row=dict(strategy_group='candidate_c',type='reduce',pnl_source='estimated',
             dry_run=False,symbol='DOGE/USDT:USDT',execution_id='CID',
             side='short',entry_price=.08244,close_price=.08163,
             amount=.83,time='2026-10-15T10:35:39',pnl=.6723,fee=.03388475)
    p=good()
    side=dict(schema_version=1,verified_reductions={'CID':dict(
        row_index=0,row_fingerprint=q._fingerprint(row),time=row['time'],
        symbol=row['symbol'],proof=p)})
    (tmp_path/'v33_11_candidate_verified_partial_evidence.json').write_text(json.dumps(side))
    original=json.dumps(row,sort_keys=True)
    report=q.analyze(tmp_path,[row],now=NOW)
    assert report['status']=='KNOWN'
    assert report['proven_in_window']==1
    assert report['remaining_estimated_in_window']==0
    assert report['proven_net_ex_funding_and_entry_usdt']==pytest.approx(.62181525)
    assert json.dumps(row,sort_keys=True)==original
    modified=dict(row,close_price=.08165)
    assert q.analyze(tmp_path,[modified],now=NOW)['status']=='INVALID'

def test_unknown_when_sidecar_missing(tmp_path):
    r=q.analyze(tmp_path,[],now=NOW)
    assert r['status']=='UNAVAILABLE'
    assert r['proven_total']==0


def test_verified_close_sidecar_validates_exact_journal_identity(tmp_path):
    row=dict(strategy_group='candidate_c',type='close',pnl_source='okx_realized',
        dry_run=False,symbol='DOGE/USDT:USDT',execution_id='CID',
        side='short',entry_price=.08244,close_price=.08163,
        amount=.83,time='2026-10-15T10:35:39',pnl=.6723,fee=.03388475)
    proof=good()
    file=dict(schema_version=1,verified_closes={'CID':dict(
        row_index=0,row_fingerprint=q._fingerprint(row),time=row['time'],
        symbol=row['symbol'],proof=proof)})
    (tmp_path/'v33_11_candidate_verified_close_evidence.json').write_text(json.dumps(file))
    result=q.analyze_closes(tmp_path,[row],now=NOW)
    assert result['status']=='KNOWN'
    assert result['proven_total']==1
    assert result['remaining_unproven_closes_in_window']==0
    modified=dict(row,close_price=.08165)
    assert q.analyze_closes(tmp_path,[modified],now=NOW)['status']=='INVALID'
