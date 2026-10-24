"""v32-3 offline arbitration tests: one successful risk action per fast tick."""
import logging
from types import SimpleNamespace
import pytest
import trader
from state import TraderState
import core_management_priority

S='XRP/USDT:USDT'

def env(tmp_path):
 c=SimpleNamespace(user_dir=str(tmp_path),EXECUTION_MODE='LIVE',
                   CORE_NEGATIVE_GUARD_ENABLED=True,
                   logger=logging.getLogger('v32-3'))
 p=dict(side='short',entry_price=1.34,contracts=6.,
        position_id='x',entry_timestamp_ms=1780000000000)
 class Client:
  def fetch_position(self):return dict(p)
  def fetch_multi_ohlcv(self,*a):return {'1m':object(),'5m':object()}
 return c,p,Client(),TraderState()

def profit_mock(monkeypatch,*,reduced=False,floor=False,recovery='READY',early=False,events=None):
 events=events if events is not None else []
 monkeypatch.setattr(trader.core_entry_timing,'confirmed_frame',lambda f,*a,**k:f)
 monkeypatch.setattr(trader,'_observe_mfe_profit_shadow',lambda *a:{'mfe_r':.7})
 monkeypatch.setattr(trader,'_maybe_execute_mfe_profit_live',
                     lambda *a:events.append('reduce') or reduced)
 monkeypatch.setattr(core_management_priority,'classify',lambda *a:{
       'status':recovery,'sources':['reduce'] if recovery!='READY' else []})
 monkeypatch.setattr(trader,'_maybe_apply_core_profit_floor',
                     lambda *a:events.append('floor') or floor)
 monkeypatch.setattr(trader,'_maybe_review_early_profit',
                     lambda *a:events.append('early') or early)
 return events

@pytest.mark.parametrize('reduced,floor,early,expected,handled',[
 (True,True,False,['reduce','floor'],True),
 (True,False,False,['reduce','floor'],True),
 (False,True,False,['reduce','floor'],True),
 (False,False,True,['reduce','floor','early'],True),
 (False,False,False,['reduce','floor','early'],False),
])
def test_management_arbitration(monkeypatch,tmp_path,reduced,floor,early,expected,handled):
 cfg,pos,client,state=env(tmp_path)
 events=profit_mock(monkeypatch,reduced=reduced,floor=floor,early=early)
 assert trader._core_fast_profit_observation(cfg,state,client,S,pos,{'1m':object()}) is handled
 assert events==expected

@pytest.mark.parametrize('reduced',[True,False])
def test_pending_reconciliation_short_circuits_floor_and_gemini(monkeypatch,tmp_path,reduced):
 cfg,pos,client,state=env(tmp_path)
 events=profit_mock(monkeypatch,reduced=reduced,recovery='RECOVERY_PENDING')
 assert trader._core_fast_profit_observation(cfg,state,client,S,pos,{'1m':object()}) is True
 assert events==['reduce']

def test_state_unknown_short_circuits(monkeypatch,tmp_path):
 cfg,pos,client,state=env(tmp_path)
 events=profit_mock(monkeypatch,recovery='STATE_UNKNOWN')
 assert trader._core_fast_profit_observation(cfg,state,client,S,pos,{'1m':object()}) is True
 assert events==['reduce']

def test_reducer_exception_blocks_further_discretionary_order(monkeypatch,tmp_path):
 cfg,pos,client,state=env(tmp_path)
 events=profit_mock(monkeypatch)
 def raise_unknown(*a):raise RuntimeError('order response unknown')
 monkeypatch.setattr(trader,'_maybe_execute_mfe_profit_live',raise_unknown)
 assert trader._core_fast_profit_observation(cfg,state,client,S,pos,{'1m':object()}) is True
 assert events==[]

def test_market_observation_failure_keeps_existing_ai_gate(monkeypatch,tmp_path):
 cfg,pos,client,state=env(tmp_path)
 monkeypatch.setattr(trader,'_observe_mfe_profit_shadow',lambda *a: (_ for _ in ()).throw(ValueError('no 1m data')))
 monkeypatch.setattr(trader.core_entry_timing,'confirmed_frame',lambda f,*a,**k:f)
 assert trader._core_fast_profit_observation(cfg,state,client,S,pos,{'1m':object()}) is False

@pytest.mark.parametrize('result',[True,False])
def test_fast_tick_stops_after_discretionary_action_only(monkeypatch,tmp_path,result):
 cfg,pos,client,state=env(tmp_path)
 actions=[]
 monkeypatch.setattr(trader,'_maybe_emergency_close_near_stop',lambda *a:actions.append('emergency') or False)
 monkeypatch.setattr(trader,'_reconcile_pending_core_reduce',lambda *a:None)
 monkeypatch.setattr(trader,'_reconcile_pending_core_add',lambda *a:None)
 monkeypatch.setattr(trader,'_core_recovery_priority_pending',lambda *a,**k:False)
 monkeypatch.setattr(trader.core_manual_close,'get',lambda *a:None)
 monkeypatch.setattr(trader,'_core_fast_profit_observation',lambda *a:actions.append('profit') or result)
 monkeypatch.setattr(trader,'_position_ai_review_candidate',
                     lambda *a:actions.append('loss_ai') or False)
 assert trader._run_core_fast_tick(cfg,state,client,S) is result
 assert actions==(['emergency','profit'] if result else ['emergency','profit','loss_ai'])

def test_fast_tick_emergency_always_has_priority(monkeypatch,tmp_path):
 cfg,pos,client,state=env(tmp_path)
 monkeypatch.setattr(trader,'_maybe_emergency_close_near_stop',lambda *a:True)
 monkeypatch.setattr(trader,'_reconcile_pending_core_reduce',
                     lambda *a:pytest.fail('no recovery after emergency close'))
 monkeypatch.setattr(trader,'_core_fast_profit_observation',
                     lambda *a:pytest.fail('no MFE after emergency close'))
 assert trader._run_core_fast_tick(cfg,state,client,S) is True

def test_flat_race_skips_floor_and_ai(monkeypatch,tmp_path):
 cfg,pos,client,state=env(tmp_path)
 events=profit_mock(monkeypatch,reduced=True)
 client.fetch_position=lambda:None
 assert trader._core_fast_profit_observation(cfg,state,client,S,pos,{'1m':object()}) is True
 assert events==['reduce']


def test_post_reduce_floor_already_attempted_is_not_repeated(monkeypatch,tmp_path):
 cfg,pos,client,state=env(tmp_path)
 evidence={'mfe_r':.8}
 monkeypatch.setattr(trader.core_entry_timing,'confirmed_frame',lambda f,*a,**k:f)
 monkeypatch.setattr(trader,'_observe_mfe_profit_shadow',lambda *a:evidence)
 def reduced_once(*a):
  evidence['_core_floor_attempted_after_reduce']=True
  return True
 monkeypatch.setattr(trader,'_maybe_execute_mfe_profit_live',reduced_once)
 monkeypatch.setattr(core_management_priority,'classify',
                     lambda *a:{'status':'READY','sources':[]})
 monkeypatch.setattr(trader,'_maybe_apply_core_profit_floor',
                     lambda *a:pytest.fail('same-cycle floor must not amend twice'))
 monkeypatch.setattr(trader,'_maybe_review_early_profit',
                     lambda *a:pytest.fail('same-cycle early AI must not run'))
 assert trader._core_fast_profit_observation(cfg,state,client,S,pos,{'1m':object()}) is True
