from types import SimpleNamespace
from copy import deepcopy
import pytest
import candidate_c_exit_management as cem
from scripts.deploy_compact_v19 import migrate


def setup_case(*,failure=None,tighter=False):
    epoch=cem.PositionEpochState(symbol='SOL/USDT:USDT',side='short',entry_intent_id='owned',
        raw_entry_price=106.99,initial_stop_price=113.79003,current_stop_price=105 if tighter else 113.79003,
        target_price=94.1512,remaining_contracts=1.29,original_contracts=1.29,
        protective_algo_ids=['algo'],attach_algo_cl_ord_id='cl',exchange_position_id='pos',
        exchange_entry_timestamp_ms=123,tick_size=.01,high_water=104,profit_lock_active=tighter)
    store=cem.PositionEpochStore.in_memory();store.save('owned',epoch)
    ledger=SimpleNamespace(find_protected_entry=lambda:(SimpleNamespace(intent_id='owned'),False))
    class Client:
        def __init__(self):
            self.calls=[];self.pos=dict(side='short',contracts=1.29,entry_price=106.99,position_id='pos',entry_timestamp_ms=123)
            self.o=dict(algo_id='algo',algo_cl_ord_id='cl',sz=1.29,side='buy',sl_price=105 if tighter else 113.79,tp_price=94.15)
        def fetch_position(self):return self.pos.copy()
        def fetch_pending_protection_algo_ids(self):return ['algo']
        def fetch_protection_order_by_algo_id(self,a):return self.o.copy()
        def fetch_last_price(self):return 104 if tighter else (102 if failure=='crossed' else 105.5)
        def amend_protective_stop(self,a,**kw):
            self.calls.append((a,kw))
            if failure=='reject':return {'ok':False}
            self.o.update(sl_price=kw['new_sl_price'],tp_price=kw['new_tp_price'])
            if failure=='qty':self.pos['contracts']=1
            if failure=='timeout':raise TimeoutError()
            return {'ok':True}
    return epoch,store,ledger,Client()

@pytest.mark.parametrize('failure',[None,'timeout'])
def test_existing_oco_tightens_in_place_only_after_actual_verification(failure):
    epoch,store,ledger,client=setup_case(failure=failure);old=deepcopy(epoch.__dict__)
    result=migrate(None,client,ledger,store)
    assert (result['sl'],result['tp'])==(109.12,102.72)
    assert client.calls==[('algo',{'new_sl_price':109.12,'new_tp_price':102.72})]
    assert {k:v for k,v in epoch.__dict__.items() if k not in ('current_stop_price','target_price')}=={k:v for k,v in old.items() if k not in ('current_stop_price','target_price')}

@pytest.mark.parametrize('failure',['reject','qty','crossed'])
def test_failed_migration_does_not_claim_epoch_change_or_cancel(failure):
    epoch,store,ledger,client=setup_case(failure=failure);old=deepcopy(epoch.__dict__)
    with pytest.raises(AssertionError):migrate(None,client,ledger,store)
    assert epoch.__dict__==old
    if failure=='crossed':assert client.calls==[]
    if failure=='reject':assert client.o['sl_price']==113.79


def test_migration_preserves_profitable_tighter_stop():
    epoch,store,ledger,client=setup_case(tighter=True)
    result=migrate(None,client,ledger,store)
    assert result['sl']==105
    assert epoch.profit_lock_active


def test_preflight_plans_without_mutating_broker_or_epoch():
    epoch,store,ledger,client=setup_case();old=deepcopy(epoch.__dict__)
    result=migrate(None,client,ledger,store,apply=False)
    assert result['planned'] and result['sl']==109.12
    assert client.calls==[] and epoch.__dict__==old



def test_migration_journals_before_request_and_failure_keeps_pending():
    epoch,store,ledger,client=setup_case(failure='reject');rows=[]
    original=client.amend_protective_stop
    def amend(*a,**k):
        assert rows and rows[-1]['status']=='pending'
        assert rows[-1]['algo_id']=='algo'
        return original(*a,**k)
    client.amend_protective_stop=amend
    with pytest.raises(AssertionError):migrate(None,client,ledger,store,journal=rows.append)
    assert rows[-1]['status']=='pending'


def test_epoch_save_failure_never_journals_complete():
    epoch,store,ledger,client=setup_case();rows=[]
    def fail_save(*a):raise OSError('disk write')
    store.save=fail_save
    with pytest.raises(OSError):migrate(None,client,ledger,store,journal=rows.append)
    assert client.o['sl_price']==109.12
    assert rows[-1]['status']=='pending'



def test_baseline_failure_parser_uses_exact_node_ids():
    from scripts.deploy_compact_v19 import failed_nodeids
    output="FAILED tests/test_one.py::test_label - AssertionError\nFAILED tests/test_two.py::test_missing_node\n2 failed"
    assert failed_nodeids(output)=={'tests/test_one.py::test_label','tests/test_two.py::test_missing_node'}
