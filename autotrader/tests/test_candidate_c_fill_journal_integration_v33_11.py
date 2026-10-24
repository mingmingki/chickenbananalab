"""One full live finalize-path test proving settlement recording uses exact fills."""
from types import SimpleNamespace
from unittest.mock import MagicMock
import candidate_c_hybrid_live_adapter as live
import candidate_c_fill_reconciliation_v33_11 as verified
from test_candidate_c_telegram_notifications import _managed_epoch

def test_candidate_reduce_journal_prefers_verified_exchange_fill(tmp_path,monkeypatch):
    sym='DOGE/USDT:USDT'
    intent=SimpleNamespace(kind=live.dec.INTENT_REDUCE,symbol=sym,reason_code='structural_derisk')
    parent=SimpleNamespace(intent_id='entry1',protective_algo_ids=['algo1'])
    action=SimpleNamespace(intent_id='reduce1',cl_ord_id='reduce1')
    epoch=_managed_epoch(sym)
    position=dict(side='long',contracts=2.,raw_entry_price=100.,
        position_id='position-1',entry_timestamp_ms=1234)
    client=MagicMock()
    client.fetch_position.return_value=dict(side='long',contracts=1.,entry_price=100.,
        position_id='position-1',entry_timestamp_ms=1234)
    client.amend_protective_stop.return_value={'ok':True}
    client.exchange.market.return_value={'precision':{'amount':0.1}}
    client.fetch_protection_order_by_algo_id.return_value={'sz':1.}
    cfg=SimpleNamespace(user_dir=str(tmp_path),logger=MagicMock())
    monkeypatch.setattr(live,'_resolve_candidate_c_reduce_pnl',
        lambda *a,**k:dict(gross_pnl=15.,fee=0.,net_pnl=None,
                           exit_price=105.,funding_fee=None,source='estimated'))
    proof=dict(order_id='OID',client_order_id='reduce1',trade_ids=['TRADE1'],
       filled_contracts=1.,vwap=104.,price_basis='verified_order_fills',
       gross_pnl=4.,fee=.2,net_ex_funding_and_entry_fees=3.8)
    monkeypatch.setattr(verified,'fetch_verified_reduce',lambda *a,**k:proof)
    recorded=[]
    monkeypatch.setattr(live.trade_log,'record_reduce',lambda *a,**kw:recorded.append((a,kw)))
    result=live._finalize_managed(cfg,client,intent,MagicMock(),MagicMock(),
         parent,epoch,position,2.,1.,action,{'order':{'id':'OID','average':105.}})
    assert result.get('executed') is True
    assert len(recorded)==1
    args,kwargs=recorded[0]
    assert args[5]==4.
    assert kwargs['fee']==.2
    assert kwargs['close_price']==104.
    assert kwargs['pnl_source']=='order_fills'
    assert kwargs['okx_net_pnl']==3.8
    assert kwargs['close_execution_evidence'] is proof
