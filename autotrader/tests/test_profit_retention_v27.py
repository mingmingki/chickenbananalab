"""Catch lost early review and AI-authority bypass of approved profit guards."""
import datetime as dt
import logging
import contextlib
import json
import time
from types import SimpleNamespace

import pandas as pd
import pytest
import trader
import mfe_profit_shadow as mfe
import reduce_v2_state as reduce
from state import TraderState

S='ETH/USDT:USDT'

def pos(side='long',contracts=8.):
 return dict(position_id='p',entry_timestamp_ms=1791900000000,side=side,entry_price=100.,contracts=contracts)

def cfg(tmp_path,**kw):
 return SimpleNamespace(user_dir=str(tmp_path),logger=logging.getLogger('profit-v27'),
  EXECUTION_MODE='LIVE',CORE_AI_STRATEGY_AUTHORITY=True,CORE_GEMINI_MANAGEMENT_ONLY=True,
  POSITION_AI_REVIEW_ENABLED=True,POSITION_AI_LIVE_EXECUTE=True,GEMINI_API_KEY='offline',
  POSITION_AI_REVIEW_COOLDOWN_MINUTES=15,MIN_CONFIDENCE=.6,**kw)

def floor_observation(position, **values):
 return dict(position_identity=str(position['position_id']), side=position['side'],
  entry_price=position['entry_price'], entry_timestamp_ms=position['entry_timestamp_ms'], **values)

def obs(tmp_path,side='long',peak=.25,current=.10,bar='b1',timestamp=1791900000000):
 p=dict(pos(side),entry_timestamp_ms=timestamp)
 sign=1 if side=='long' else -1
 return mfe.observe(str(tmp_path),S,p,sl_price=100.-sign*10.,price=100.+sign*10.*current,
  favorable_price=100.+sign*10.*peak,bar_time=bar)

@pytest.mark.parametrize('side',['long','short'])
def test_early_review_triggers_at_quarter_r_and_point_fifteen_giveback(tmp_path,side):
 out=obs(tmp_path,side)
 assert out.get('early_review_candidate') is not None
 assert out['live_candidate'] is None  # Early event cannot issue a deterministic reduce.

@pytest.mark.parametrize('peak,current',[(.249,.09),(.25,.101)])
def test_early_review_requires_both_thresholds(tmp_path,peak,current):
 assert obs(tmp_path,peak=peak,current=current).get('early_review_candidate') is None

def test_early_review_retries_next_bar_until_valid_review_then_survives_restart(tmp_path):
 first=obs(tmp_path)
 assert first.get('early_review_candidate') is not None
 assert obs(tmp_path).get('early_review_candidate') is None
 assert obs(tmp_path,bar='b2').get('early_review_candidate') is not None
 assert mfe.mark_early_reviewed(str(tmp_path),S,position_identity=first['position_identity'])
 assert obs(tmp_path,bar='b3').get('early_review_candidate') is None
 assert obs(tmp_path,bar='b4',timestamp=1791900001000).get('early_review_candidate') is not None

@pytest.mark.parametrize('side,mark,stop,want',[('long',103.,90.,100.3),('short',97.,110.,99.7)])
def test_ai_authority_floor_covers_roundtrip_fee_spread_and_slippage(tmp_path,monkeypatch,side,mark,stop,want):
 p=pos(side);c=cfg(tmp_path,SPREAD_BPS=5,SLIPPAGE_BPS=5)
 class Client:
  def fetch_position(self):return dict(p)
  def fetch_current_protection(self,*_):return dict(algo_id='oco',sz=8.,sl_price=self.stop,tp_price=120. if side=='long' else 80.,side='sell' if side=='long' else 'buy',state='live')
  def fetch_last_price(self):return mark
  def amend_protective_stop(self,algo,new_sl_price):self.stop=new_sl_price;return {'ok':True}
  def fetch_protection_order_by_algo_id(self,*_):return self.fetch_current_protection()
 client=Client();client.stop=stop
 monkeypatch.setattr(trader.cc_ownership,'account_order_lock',lambda *_:contextlib.nullcontext())
 result=trader._maybe_apply_core_profit_floor(c,client,S,p,floor_observation(p,mfe_r=.75,current_r=.3,initial_r=10.))
 assert result is True
 assert client.stop==pytest.approx(want)

@pytest.mark.parametrize('side,mark,stop',[('long',100.05,90.),('short',99.95,110.)])
def test_floor_never_crosses_live_market_or_loosens_existing_stop(tmp_path,monkeypatch,side,mark,stop):
 p=pos(side);c=cfg(tmp_path)
 client=SimpleNamespace(fetch_position=lambda:p,fetch_current_protection=lambda *_:dict(algo_id='oco',sz=8.,sl_price=stop,tp_price=120. if side=='long' else 80.,side='sell' if side=='long' else 'buy',state='live'),fetch_last_price=lambda:mark,
  amend_protective_stop=lambda *a,**k:pytest.fail('unsafe amendment'))
 monkeypatch.setattr(trader.cc_ownership,'account_order_lock',lambda *_:contextlib.nullcontext())
 assert not trader._maybe_apply_core_profit_floor(c,client,S,p,floor_observation(p,mfe_r=.75,current_r=.3,initial_r=10.))

@pytest.mark.parametrize('side',['long','short'])
def test_fast_tick_runs_early_gemini_even_inside_ordinary_review_cooldown(tmp_path,monkeypatch,side):
 c=cfg(tmp_path);p=pos(side);state=TraderState();state.update_symbol(S,position_ai_review_block_until=dt.datetime.now()+dt.timedelta(minutes=15))
 observation=obs(tmp_path,side)
 raw={'1m':pd.DataFrame([dict(timestamp=pd.Timestamp.now(tz='UTC')-pd.Timedelta(minutes=2),close=101.)])}
 monkeypatch.setattr(trader,'_observe_mfe_profit_shadow',lambda *_:observation)
 def review(*args,**kw):
  assert kw['review_path']=='profit_retention'
  assert args[6]['entry_timestamp_ms']==1791900000000
  mfe.mark_early_reviewed(str(tmp_path),S,position_identity=observation['position_identity'])
 monkeypatch.setattr(trader,'_handle_position_ai_review',review)
 client=SimpleNamespace(fetch_position=lambda:p)
 assert trader._core_fast_profit_observation(c,state,client,S,p,raw) is True
 assert mfe.get_state(str(tmp_path),S).get('early_reviewed') is True

@pytest.mark.parametrize('side',['long','short'])
@pytest.mark.parametrize('previous,stage,fill_fraction',[(0.,0,1.),(0.,0,.5),(.30,3,1.),(.48,4,1.)])
def test_authority_mfe_uses_real_fixed_initial_quantity_reducer(tmp_path,monkeypatch,side,previous,stage,fill_fraction):
 initial=8.;before=initial*(1-previous);requested=min(initial*.25,initial*(.5-previous))
 c=cfg(tmp_path);p=pos(side,contracts=before);state=TraderState()
 opened=dict(type='open',symbol=S,side=side,price=100.,entry_price=100.,amount=8.,dry_run=False,time='2026-10-13T00:00:00',sl_price=90. if side=='long' else 110.)
 reduce.ensure_position(str(tmp_path),S,p,initial_contracts=8.)
 reduce.update_fields(str(tmp_path),S,actual_reduced_contracts=initial*previous,cumulative_reduced_ratio=previous,reduce_stage=stage)
 observation=obs(tmp_path,side,peak=.6,current=.3,bar=(dt.datetime.now(dt.timezone.utc)-dt.timedelta(minutes=1)).isoformat())
 reductions=[]
 class Client:
  quantity=before
  filled=0.
  def fetch_position(self):return dict(p,contracts=self.quantity)
  def fetch_last_price(self):return 103. if side=='long' else 97.
  def contract_size(self):return 1.
  def fetch_current_protection(self,*_):return dict(algo_id='oco',sz=self.quantity,sl_price=90. if side=='long' else 110.,tp_price=120. if side=='long' else 80.,side='sell' if side=='long' else 'buy',state='live')
  def reduce_position(self,position,contracts,*,client_order_id):
   want=requested if not reductions else requested*(1-fill_fraction)
   assert contracts==pytest.approx(want)
   self.filled=contracts*(fill_fraction if not reductions else 1.)
   self.quantity-=self.filled;reductions.append(contracts);return dict(id='order')
  def fetch_order_status_by_client_id(self,*_):return dict(id='order',status='closed',filled=self.filled,average=103. if side=='long' else 97.)
 client=Client()
 monkeypatch.setattr(trader.trade_log,'last_unclosed_open',lambda *_:opened)
 monkeypatch.setattr(trader.risk_manager,'quantize_coin_amount_to_market',lambda _c,_s,amount:amount)
 monkeypatch.setattr(trader,'_closed_indicator_tail',lambda *a,**k:[])
 monkeypatch.setattr(trader,'_restore_core_reduce_protection',lambda *a:True)
 monkeypatch.setattr(trader.order_safety,'verify_protection',lambda *a,**k:{'ok':True})
 monkeypatch.setattr(trader,'_notify_telegram',lambda *_:None)
 import core_reduce_fill_accounting
 monkeypatch.setattr(core_reduce_fill_accounting,'resolve_reduce_fill',lambda *_:dict(gross_pnl=6.,fee=.2,source='offline',net_pnl=5.8))
 monkeypatch.setattr(trader,'_maybe_apply_core_profit_floor',lambda *_:False)
 assert trader._maybe_execute_mfe_profit_live(c,state,client,S,p,{},observation) is True
 assert reductions==pytest.approx([requested])
 if fill_fraction<1:
  assert not mfe.get_state(str(tmp_path),S)['policies'][mfe.LIVE_POLICY_NAME].get('live_resolved')
  fresh=obs(tmp_path,side,peak=.6,current=.3,bar=(dt.datetime.now(dt.timezone.utc)-dt.timedelta(minutes=1,seconds=1)).isoformat())
  assert trader._maybe_execute_mfe_profit_live(c,state,client,S,client.fetch_position(),{},fresh)
  assert reductions==pytest.approx([requested,requested*(1-fill_fraction)])
 assert reduce.get(str(tmp_path),S)['cumulative_reduced_ratio']==pytest.approx(previous+requested/initial)
 assert mfe.get_state(str(tmp_path),S)['policies'][mfe.LIVE_POLICY_NAME]['live_resolved'] is True
 assert trader._maybe_execute_mfe_profit_live(c,state,client,S,client.fetch_position(),{},obs(tmp_path,side,peak=.6,current=.3,bar='next')) is False

@pytest.mark.parametrize('bad', [float('nan'),float('inf'),True])
def test_mfe_gate_rejects_nonfinite_or_bool_geometry(bad):
 approval=dict(path='mfe_profit_live',mfe_candidate=dict(policy=mfe.LIVE_POLICY_NAME,entry_price=100.,initial_r=bad,mfe_r=.6))
 assert trader._mfe_profit_live_gate(pos(),approval,last_price=103.,reduce_state={})['allowed'] is False

@pytest.mark.parametrize('failure',[None,'exception','invalid'])
def test_real_early_review_latches_only_valid_gemini_response(tmp_path,monkeypatch,failure):
 c=cfg(tmp_path);c.POSITION_AI_LIVE_EXECUTE=False;p=dict(pos(),mark_price=101.);state=TraderState();observation=obs(tmp_path)
 class Client:
  def fetch_position(self):return p
  def fetch_current_protection(self,*_):return dict(algo_id='oco',sz=8.,sl_price=90.,tp_price=120.)
 def provider(*a,**k):
  if failure=='exception':raise RuntimeError('offline timeout')
  return dict(assessment='thesis_intact',confidence=.1 if failure=='invalid' else .8,reasoning='offline hold',management_action='HOLD',risk_level='low')
 monkeypatch.setattr(trader.gemini_analyzer,'analyze_held_position',provider)
 monkeypatch.setattr(trader,'_closed_indicator_tail',lambda *a,**k:[])
 assert trader._maybe_review_early_profit(c,state,Client(),S,p,{},observation)
 assert bool(mfe.get_state(str(tmp_path),S).get('early_reviewed')) is (failure is None)

@pytest.mark.parametrize('side,entry,mark,stop,want',[
 ('long',100.03,103.,90.,100.14),('short',100.03,97.,110.,99.92)])
def test_floor_rounds_toward_cost_coverage_on_exchange_tick(tmp_path,monkeypatch,side,entry,mark,stop,want):
 from decimal import Decimal,ROUND_HALF_UP
 import ccxt
 p=dict(pos(side),entry_price=entry);c=cfg(tmp_path)
 class Client:
  def fetch_position(self):return p
  def fetch_current_protection(self,*_):return dict(algo_id='oco',sz=8.,sl_price=self.stop,tp_price=120. if side=='long' else 80.,side='sell' if side=='long' else 'buy',state='live')
  def fetch_last_price(self):return mark
  def amend_protective_stop(self,algo,new_sl_price):
   self.stop=float(Decimal(str(new_sl_price)).quantize(Decimal('.01'),rounding=ROUND_HALF_UP));return {'ok':True}
  def fetch_protection_order_by_algo_id(self,*_):return self.fetch_current_protection()
 client=Client();client.stop=stop
 client.exchange=SimpleNamespace(precisionMode=ccxt.TICK_SIZE,market=lambda _:dict(precision={'price':.01}),
  price_to_precision=lambda _s,x:str(Decimal(str(x)).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)))
 monkeypatch.setattr(trader.cc_ownership,'account_order_lock',lambda *_:contextlib.nullcontext())
 assert trader._maybe_apply_core_profit_floor(c,client,S,p,floor_observation(p,mfe_r=.75,current_r=.3,initial_r=10.))
 assert client.stop==pytest.approx(want)

@pytest.mark.parametrize('case',['pending_reduce','pending_add','stale_ctime','manual_close','kill'])
def test_floor_safety_blocks_never_amend(tmp_path,monkeypatch,case):
 p=pos();c=cfg(tmp_path)
 client=SimpleNamespace(fetch_position=lambda:p,fetch_current_protection=lambda *_:dict(algo_id='oco',sz=8.,sl_price=90.,tp_price=120.,side='sell',state='live'),fetch_last_price=lambda:103.,
  amend_protective_stop=lambda *a,**k:pytest.fail('guarded amendment'))
 observation=floor_observation(p,mfe_r=.75,current_r=.3,initial_r=10.)
 if case=='pending_reduce':reduce.ensure_position(str(tmp_path),S,p,initial_contracts=8.);reduce.update_fields(str(tmp_path),S,pending_order={'client_order_id':'pending'})
 if case=='pending_add':monkeypatch.setattr(trader.core_add_position_state,'get',lambda *_:{'pending_order':{'id':'pending'}})
 if case=='stale_ctime':observation['entry_timestamp_ms']=1791900001000
 if case=='manual_close':monkeypatch.setattr(trader.core_manual_close,'get',lambda *_:{'status':'started'})
 if case=='kill':monkeypatch.setattr(trader.core_kill_switch,'is_active',lambda *_:True)
 assert not trader._maybe_apply_core_profit_floor(c,client,S,p,observation)

@pytest.mark.parametrize('peak,allowed',[(.749,False),(.75,True)])
def test_ordinary_floor_waits_for_three_quarter_r_without_strong_giveback(tmp_path,monkeypatch,peak,allowed):
 p=pos();c=cfg(tmp_path);amended=[]
 class Client:
  stop=90.
  def fetch_position(self):return p
  def fetch_current_protection(self,*_):return dict(algo_id='oco',sz=8.,sl_price=self.stop,tp_price=120.,side='sell',state='live')
  def fetch_last_price(self):return 107.
  def amend_protective_stop(self,algo,new_sl_price):self.stop=new_sl_price;amended.append(new_sl_price);return {'ok':True}
  def fetch_protection_order_by_algo_id(self,*_):return self.fetch_current_protection()
 monkeypatch.setattr(trader.reduce_v2_state,'get',lambda *_:dict(cumulative_reduced_ratio=.25,reduce_stage=1))
 monkeypatch.setattr(trader.cc_ownership,'account_order_lock',lambda *_:contextlib.nullcontext())
 assert bool(trader._maybe_apply_core_profit_floor(c,Client(),S,p,floor_observation(p,mfe_r=peak,current_r=.7,initial_r=10.))) is allowed
 assert bool(amended) is allowed

@pytest.mark.parametrize('protected',[True,False])
def test_terminal_zero_fill_mfe_releases_pending_only_with_unchanged_protection(tmp_path,monkeypatch,protected):
 p=pos();c=cfg(tmp_path);state=TraderState();reduce.ensure_position(str(tmp_path),S,p,initial_contracts=8.)
 pending=dict(client_order_id='zero',lifecycle_id=reduce.position_identity(p),contracts=2.,before_contracts=8.,stage=1,bar_ts='b',
  path='mfe_profit_live',position=p,protection=dict(algo_id='oco',sl_price=90.,tp_price=120.,sz=8.))
 reduce.update_fields(str(tmp_path),S,pending_order=pending,mfe_reduce_target_contracts=2.)
 client=SimpleNamespace(fetch_order_status_by_client_id=lambda _:dict(status='canceled',filled=0.),fetch_position=lambda:p)
 monkeypatch.setattr(trader.order_safety,'verify_protection',lambda *a,**k:{'ok':protected})
 assert trader._reconcile_pending_core_reduce(c,state,client,S) is protected
 saved=reduce.get(str(tmp_path),S)
 assert (saved['pending_order'] is None) is protected
 assert saved['actual_reduced_contracts']==0. and saved['reduce_stage']==0
 assert saved['mfe_reduce_target_contracts']==2.


def test_mfe_below_minimum_size_is_terminal_for_current_lifecycle(tmp_path,monkeypatch):
 p=pos();c=cfg(tmp_path);state=TraderState()
 reduce.ensure_position(str(tmp_path),S,p,initial_contracts=8.)
 observation=obs(tmp_path,peak=.6,current=.3,
  bar=(dt.datetime.now(dt.timezone.utc)-dt.timedelta(minutes=1)).isoformat())
 calls=[]
 class Client:
  def fetch_last_price(self):return 103.
 client=Client()
 def blocked(*args,**kwargs):
  calls.append(1)
  reduce.record_block(str(tmp_path),S,'below_minimum_size')
  return False
 monkeypatch.setattr(trader,'_execute_position_ai_reduce_50',blocked)
 assert trader._maybe_execute_mfe_profit_live(c,state,client,S,p,{},observation) is False
 assert len(calls)==1
 saved=mfe.get_state(str(tmp_path),S)
 policy=saved['policies'][mfe.LIVE_POLICY_NAME]
 assert policy['live_resolved'] is True
 assert policy['live_status']=='skipped'
 assert policy['live_reason']=='below_minimum_size'
 # Even if the same threshold remains true on a later confirmed bar, this
 # lifecycle must not emit another live candidate/order attempt.
 later=obs(tmp_path,peak=.6,current=.2,bar='later')
 assert later.get('live_candidate') is None
 assert trader._maybe_execute_mfe_profit_live(c,state,client,S,p,{},later) is False
 assert len(calls)==1


def test_mfe_transient_reduce_block_remains_retryable(tmp_path,monkeypatch):
 p=pos();c=cfg(tmp_path);state=TraderState()
 reduce.ensure_position(str(tmp_path),S,p,initial_contracts=8.)
 observation=obs(tmp_path,peak=.6,current=.3,
  bar=(dt.datetime.now(dt.timezone.utc)-dt.timedelta(minutes=1)).isoformat())
 class Client:
  def fetch_last_price(self):return 103.
 def blocked(*args,**kwargs):
  reduce.record_block(str(tmp_path),S,'fill_unresolved')
  return False
 monkeypatch.setattr(trader,'_execute_position_ai_reduce_50',blocked)
 assert trader._maybe_execute_mfe_profit_live(c,state,Client(),S,p,{},observation) is False
 policy=mfe.get_state(str(tmp_path),S)['policies'][mfe.LIVE_POLICY_NAME]
 assert not policy.get('live_resolved')
 assert obs(tmp_path,peak=.6,current=.2,bar='later').get('live_candidate') is not None


def test_persisted_below_minimum_size_resolves_before_price_gate_or_order(tmp_path,monkeypatch):
 p=pos();c=cfg(tmp_path);state=TraderState()
 reduce.ensure_position(str(tmp_path),S,p,initial_contracts=8.)
 reduce.record_block(str(tmp_path),S,'below_minimum_size')
 observation=obs(tmp_path,peak=.6,current=-.2,bar='persisted-restart')
 class Client:
  def fetch_last_price(self):
   pytest.fail('persisted terminal block must resolve before live price fetch')
 monkeypatch.setattr(trader,'_execute_position_ai_reduce_50',
  lambda *a,**k:pytest.fail('persisted terminal block must not reach reducer'))
 assert trader._maybe_execute_mfe_profit_live(c,state,Client(),S,p,{},observation) is False
 policy=mfe.get_state(str(tmp_path),S)['policies'][mfe.LIVE_POLICY_NAME]
 assert policy['live_resolved'] is True
 assert policy['live_status']=='skipped'
 assert policy['live_reason']=='below_minimum_size'
 assert obs(tmp_path,peak=.6,current=.2,bar='after-resolve').get('live_candidate') is None
