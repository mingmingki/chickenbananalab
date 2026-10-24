"""v32-2: unresolved fill/OCO ownership outranks AI and profit management."""
from types import SimpleNamespace
import logging
import pytest
import trader
import core_management_priority as priority
from state import TraderState

S='ETH/USDT:USDT'

def cfg(tmp_path):
 return SimpleNamespace(user_dir=str(tmp_path),EXECUTION_MODE='LIVE',
                        CORE_NEGATIVE_GUARD_ENABLED=False,
                        logger=logging.getLogger('v32_2'))

def pending(monkeypatch,*,reduce=False,add=False,unknown=False):
 if unknown:
  def fail(*a):raise ValueError('corrupt lifecycle')
  monkeypatch.setattr(priority.reduce_v2_state,'get',fail)
 else:
  monkeypatch.setattr(priority.reduce_v2_state,'get',
                      lambda *a: {'pending_order':{'id':'r'}} if reduce else {})
 monkeypatch.setattr(priority.core_add_position_state,'get',
                     lambda *a: {'pending_order':{'id':'a'}} if add else {})

def mock_fast(monkeypatch):
 monkeypatch.setattr(trader,'_maybe_emergency_close_near_stop',lambda *a:False)
 monkeypatch.setattr(trader,'_reconcile_pending_core_reduce',lambda *a:None)
 monkeypatch.setattr(trader,'_reconcile_pending_core_add',lambda *a:None)
 return SimpleNamespace(fetch_position=lambda:None)

def test_only_ready_state_allows_discretionary_management(monkeypatch,tmp_path):
 pending(monkeypatch)
 result=priority.classify(str(tmp_path),S)
 assert result=={'status':'READY','sources':[]}
 p=TraderState()
 assert not trader._core_recovery_priority_pending(cfg(tmp_path),p,S,loop='fast',phase='add')

@pytest.mark.parametrize('where',['reduce','add','both','unknown'])
def test_fast_defers_profit_and_orders_until_reconciled(monkeypatch,tmp_path,where):
 pending(monkeypatch,reduce=where in ('reduce','both'),add=where in ('add','both'),
         unknown=where=='unknown')
 client=mock_fast(monkeypatch)
 steps=[]
 monkeypatch.setattr(trader,'_reconcile_pending_core_reduce',lambda *a:steps.append('reduce'))
 monkeypatch.setattr(trader,'_reconcile_pending_core_add',lambda *a:steps.append('add'))
 monkeypatch.setattr(trader,'_core_fast_profit_observation',
                     lambda *_:pytest.fail('profit/MFE cannot run while pending'))
 monkeypatch.setattr(trader,'_position_ai_review_candidate',
                     lambda *_:pytest.fail('Gemini cannot run while pending'))
 assert trader._run_core_fast_tick(cfg(tmp_path),TraderState(),client,S) is False
 assert steps == (['reduce'] if where in ('reduce','both','unknown') else ['reduce','add'])

def test_fast_emergency_remains_first_even_with_unresolved_reduce(monkeypatch,tmp_path):
 pending(monkeypatch,reduce=True)
 client=mock_fast(monkeypatch)
 monkeypatch.setattr(trader,'_maybe_emergency_close_near_stop',lambda *a:True)
 monkeypatch.setattr(trader,'_reconcile_pending_core_reduce',
                     lambda *_:pytest.fail('emergency retains priority'))
 assert trader._run_core_fast_tick(cfg(tmp_path),TraderState(),client,S) is True

@pytest.mark.parametrize('where',['reduce','add','both','unknown'])
def test_main_never_starts_ai_or_journal_when_order_unresolved(monkeypatch,tmp_path,where):
 pending(monkeypatch,reduce=where in ('reduce','both'),add=where in ('add','both'),
         unknown=where=='unknown')
 import core_entry_events
 monkeypatch.setattr(core_entry_events,'kick',lambda *_:None)
 monkeypatch.setattr(core_entry_events,'pending_order',lambda *_:False)
 monkeypatch.setattr(trader,'_reconcile_pending_core_entry',lambda *_:None)
 steps=[]
 monkeypatch.setattr(trader,'_reconcile_pending_core_reduce',lambda *_:steps.append('reduce'))
 monkeypatch.setattr(trader,'_reconcile_pending_core_add',lambda *_:steps.append('add'))
 monkeypatch.setattr(trader.core_manual_close,'get',
                     lambda *_:pytest.fail('manual reconciliation must wait'))
 client=SimpleNamespace(fetch_multi_ohlcv=lambda *_:pytest.fail('AI chart work must wait'))
 trader.run_cycle(cfg(tmp_path),TraderState(),client,S,None)
 assert steps == (['reduce'] if where in ('reduce','both','unknown') else ['reduce','add'])

def test_unexpected_state_read_errors_are_fail_closed(monkeypatch,tmp_path):
 pending(monkeypatch,unknown=True)
 assert priority.classify(str(tmp_path),S)['status']=='STATE_UNKNOWN'

def test_recovery_status_tracks_source_without_any_order(monkeypatch,tmp_path):
 pending(monkeypatch,add=True)
 s=TraderState();c=cfg(tmp_path)
 assert trader._core_recovery_priority_pending(c,s,S,loop='main',phase='add')
 state=s.snapshot()
 assert state['symbols'][S]['core_recovery_priority']['sources']==['add']
