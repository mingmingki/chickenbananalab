"""Offline regression: sparse OKX immediate ack, scoped safety latch recovery."""
from types import SimpleNamespace
import core_entry_orders
import core_kill_switch


def _fake_order(*, filled=None, remaining=None, status='open', cid='cgtest123'):
    return {'id':'ord-1','clientOrderId':cid,'symbol':'BTC/USDT:USDT','side':'buy',
            'status':status,'filled':filled,'remaining':remaining,'average':81706.6,
            'timestamp':1791488870160}


def test_sparse_accepted_order_readback_resolves_without_reorder(monkeypatch,tmp_path):
    payload={'side':'long','quantity_coin':.0122,'contracts':1.22,'sl_price':80081.9,'tp_price':84984.8}
    receipt={'decision_id':'manual-btc','client_order_id':'cgtest123','symbol':'BTC/USDT:USDT','payload':payload}
    calls={'creates':0,'reads':0,'updates':[]}
    monkeypatch.setattr(core_entry_orders.events,'reserve_order',lambda *a:(True,receipt))
    monkeypatch.setattr(core_entry_orders.events,'update_order',lambda *a,**k:calls['updates'].append((a,k)))
    monkeypatch.setattr(core_entry_orders.time,'sleep',lambda *_:None)
    def create(*a,**k):
        calls['creates']+=1
        return _fake_order(filled=None,remaining=None,status='open')
    def read(cid):
        assert cid=='cgtest123'
        calls['reads']+=1
        return _fake_order(filled=1.22,remaining=0,status='closed')
    client=SimpleNamespace(contract_size=lambda:.01,create_position_with_sl_tp=create,
                           fetch_order_status_by_client_id=read)
    cfg=SimpleNamespace(user_dir=str(tmp_path),LEVERAGE=5)
    result=core_entry_orders.submit_once(cfg,client,'BTC/USDT:USDT','manual-btc',
                                          'long',.0122,80081.9,84984.8,lambda *_:None)
    assert result['status']=='FILLED_UNJOURNALED'
    assert calls['creates']==1 and calls['reads']==1


def test_ambiguous_ack_remains_pending_no_duplicate(monkeypatch,tmp_path):
    payload={'side':'long','quantity_coin':.0122,'contracts':1.22,'sl_price':80081.9,'tp_price':84984.8}
    receipt={'decision_id':'manual-btc','client_order_id':'cgtest123','symbol':'BTC/USDT:USDT','payload':payload}
    calls={'creates':0,'reads':0}
    monkeypatch.setattr(core_entry_orders.events,'reserve_order',lambda *a:(True,receipt))
    monkeypatch.setattr(core_entry_orders.events,'update_order',lambda *a,**k:None)
    monkeypatch.setattr(core_entry_orders.time,'sleep',lambda *_:None)
    def create(*a,**k):
        calls['creates']+=1
        return _fake_order(filled=None,remaining=None,status='open')
    def read(cid):
        calls['reads']+=1
        return None
    client=SimpleNamespace(contract_size=lambda:.01,create_position_with_sl_tp=create,
                           fetch_order_status_by_client_id=read)
    cfg=SimpleNamespace(user_dir=str(tmp_path),LEVERAGE=5)
    result=core_entry_orders.submit_once(cfg,client,'BTC/USDT:USDT','manual-btc',
                                          'long',.0122,80081.9,84984.8,lambda *_:None)
    assert result['status']=='ORDER_PENDING'
    assert calls['creates']==1 and calls['reads']==2


def test_reconciled_latch_clears_only_exact_reason(tmp_path):
    u=str(tmp_path)
    sym='BTC/USDT:USDT'
    cid='cgtest123'
    assert not core_kill_switch.clear_reconciled_entry_stop(u,sym,cid)
    core_kill_switch.activate(u,f'{sym} unresolved entry {cid}')
    assert not core_kill_switch.clear_reconciled_entry_stop(u,sym,'cgother')
    assert core_kill_switch.is_active(u)
    assert core_kill_switch.clear_reconciled_entry_stop(u,sym,cid)
    assert not core_kill_switch.is_active(u)
    core_kill_switch.activate(u,f'{sym} protection failed:oco_missing')
    assert not core_kill_switch.clear_reconciled_entry_stop(u,sym,cid)
    assert core_kill_switch.is_active(u)
