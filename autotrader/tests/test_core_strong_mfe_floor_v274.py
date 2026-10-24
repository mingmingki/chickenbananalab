"""CORE-only strong-MFE floor behavior through real helpers and offline exchange boundaries."""
import datetime as dt
import logging
from types import SimpleNamespace
from decimal import Decimal, ROUND_HALF_UP
import ccxt
import pytest
import trader
import mfe_profit_shadow as mfe
import reduce_v2_state as reduce
from state import TraderState

S='XRP/USDT:USDT'

@pytest.fixture
def env(tmp_path,monkeypatch):
 cfg=SimpleNamespace(user_dir=str(tmp_path),EXECUTION_MODE='LIVE',CORE_AI_STRATEGY_AUTHORITY=True,
  CORE_GEMINI_MANAGEMENT_ONLY=True,logger=logging.getLogger('strong-floor'),SPREAD_BPS=0,SLIPPAGE_BPS=0)
 p=dict(position_id='p',entry_timestamp_ms=1791900000000,side='long',entry_price=100.,contracts=8.)
 opened=dict(time='2026-10-14T03:00:00',side='long',price=100.,entry_price=100.,sl_price=90.)
 observation=dict(position_identity='journal:'+S+'|long|'+opened['time'],side='long',entry_price=100.,
  entry_timestamp_ms=p['entry_timestamp_ms'],initial_r=10.,mfe_r=.60,current_r=.30,giveback_r=.30)
 class Client:
  position=dict(p);stop=90.;size=8.;tp=120.;mark=103.;amends=[];reject=False;failure=False;confirmed_bad=None
  exchange=SimpleNamespace(precisionMode=ccxt.TICK_SIZE,market=lambda _:dict(precision={'price':.01}),
    price_to_precision=lambda _s,x:str(Decimal(str(x)).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)))
  def fetch_position(self):return dict(self.position) if self.position else None
  def fetch_current_protection(self,side):
   if side != ('sell' if self.position['side']=='long' else 'buy'):raise AssertionError('wrong close side')
   return dict(algo_id='oco',sz=self.size,sl_price=self.stop,tp_price=self.tp,side=side,state='live')
  def fetch_last_price(self):return self.mark
  def amend_protective_stop(self,algo,*,new_sl_price):
   assert algo=='oco';self.amends.append(dict(algo_id=algo,sl=new_sl_price,size=self.size,tp=self.tp))
   if self.failure:raise RuntimeError('offline response unavailable')
   if self.reject:return {'ok':False}
   self.stop=new_sl_price;return {'ok':True}
  def fetch_protection_order_by_algo_id(self,algo):
   assert algo=='oco'
   o=self.fetch_current_protection('sell' if self.position['side']=='long' else 'buy')
   if self.confirmed_bad:o.update(self.confirmed_bad)
   return o
  def cancel_protection(self,*a,**kw):pytest.fail('existing protection must never be canceled')
  def attach_protection(self,*a,**kw):pytest.fail('floor may only amend existing owned stop')
  def reduce_position(self,*a,**kw):pytest.fail('floor must never create reduce/test/close orders')
 client=Client()
 monkeypatch.setattr(trader.trade_log,'last_unclosed_open',lambda *_:opened)
 return SimpleNamespace(cfg=cfg,p=p,opened=opened,obs=observation,c=client,user=str(tmp_path),state=TraderState())

def apply(e):return trader._maybe_apply_core_profit_floor(e.cfg,e.c,S,e.p,e.obs)

def short(e):
 e.p['side']='short';e.opened['side']='short';e.obs.update(side='short',position_identity='journal:'+S+'|short|'+e.opened['time'])
 e.c.position=dict(e.p);e.c.stop=110.;e.c.tp=80.;e.c.mark=97.

@pytest.mark.parametrize('side',['long','short'])
@pytest.mark.parametrize('peak,current,allowed,reason',[(.49,.20,False,None),(.50,.26,False,None),(.50,.25,True,'strong_mfe_giveback'),(.60,.30,True,'strong_mfe_giveback'),(.75,.75,True,'ordinary_075r')])
def test_live_thresholds_require_peak_and_actual_giveback(env,side,peak,current,allowed,reason,caplog):
 e=env
 if side=='short':short(e)
 e.obs.update(mfe_r=peak,current_r=current,giveback_r=peak-current)
 e.c.mark=100.+(1 if side=='long' else -1)*current*10.
 with caplog.at_level(logging.INFO):assert apply(e) is allowed
 assert len(e.c.amends)==int(allowed)
 if allowed:
  assert e.c.stop==pytest.approx(100.1 if side=='long' else 99.9)
  assert any('CORE_PROFIT_FLOOR_LOCKED' in r.message and 'reason='+reason in r.message for r in caplog.records)

@pytest.mark.parametrize('side',['long','short'])
@pytest.mark.parametrize('mark_delta',[.10,.09,0.,-.1])
def test_cost_nonpositive_never_amends_even_with_strong_peak(env,side,mark_delta):
 e=env
 if side=='short':short(e)
 e.c.mark=100.+(1 if side=='long' else -1)*mark_delta
 assert not apply(e);assert not e.c.amends

@pytest.mark.parametrize('side',['long','short'])
def test_existing_better_stop_never_loosens(env,side):
 e=env
 if side=='short':short(e)
 e.c.stop=101. if side=='long' else 99.
 assert not apply(e);assert not e.c.amends

@pytest.mark.parametrize('case',['missing_position','identity','side','ctime','obs_identity','obs_side','obs_ctime','obs_entry','contracts','protection_size','pending_reduce','pending_add','manual','kill'])
def test_lifecycle_and_owned_quantity_guards_defer_without_amend(env,monkeypatch,case):
 e=env
 if case=='missing_position':e.c.position=None
 if case=='identity':e.c.position['position_id']='another'
 if case=='side':e.c.position['side']='short'
 if case=='ctime':e.c.position['entry_timestamp_ms']+=1
 if case=='obs_identity':e.obs['position_identity']='journal:'+S+'|long|old-time'
 if case=='obs_side':e.obs['side']='short'
 if case=='obs_ctime':e.obs['entry_timestamp_ms']+=1
 if case=='obs_entry':e.obs['entry_price']=99.
 if case=='contracts':e.c.position['contracts']=7.5;e.c.size=7.5
 if case=='protection_size':e.c.size=7.5
 if case=='pending_reduce':reduce.ensure_position(e.user,S,e.p,initial_contracts=8.);reduce.update_fields(e.user,S,pending_order={'id':'unresolved'})
 if case=='pending_add':monkeypatch.setattr(trader.core_add_position_state,'get',lambda *_:{'pending_order':{'id':'add'}})
 if case=='manual':monkeypatch.setattr(trader.core_manual_close,'get',lambda *_:{'status':'running'})
 if case=='kill':monkeypatch.setattr(trader.core_kill_switch,'is_active',lambda *_:True)
 assert not apply(e);assert not e.c.amends

@pytest.mark.parametrize('side',['long','short'])
def test_tick_rounded_floor_must_still_be_inside_market(env,side):
 e=env
 if side=='short':short(e)
 e.p['entry_price']=100.03;e.c.position['entry_price']=100.03;e.obs['entry_price']=100.03
 # Unrounded cost floor is inside, but valid tick touches/breaches live market.
 e.c.mark=100.135 if side=='long' else 99.925
 assert not apply(e);assert not e.c.amends

@pytest.mark.parametrize('failure',['rejected','exception'])
def test_amend_failure_preserves_existing_oco_and_never_logs_success(env,failure,caplog):
 e=env;e.c.reject=failure=='rejected';e.c.failure=failure=='exception'
 with caplog.at_level(logging.INFO):assert not apply(e)
 assert e.c.stop==90. and e.c.size==8. and e.c.tp==120.
 assert not any('CORE_PROFIT_FLOOR_LOCKED' in r.message for r in caplog.records)

@pytest.mark.parametrize('bad',[{'sl_price':90.},{'sl_price':float('nan')},{'sz':7.},{'tp_price':119.},{'algo_id':'wrong'},{'state':'canceled'},{'side':'buy'}])
def test_amend_confirmation_must_verify_actual_stop_tp_size_and_owned_id(env,bad,caplog):
 e=env;e.c.confirmed_bad=bad
 with caplog.at_level(logging.INFO):assert not apply(e)
 assert len(e.c.amends)==1
 assert not any('CORE_PROFIT_FLOOR_LOCKED' in r.message for r in caplog.records)

@pytest.mark.parametrize('ratio',[0.,.25,.5])
def test_floor_independent_of_reduction_ratio(env,ratio):
 e=env;reduce.ensure_position(e.user,S,e.p,initial_contracts=8.)
 reduce.update_fields(e.user,S,cumulative_reduced_ratio=ratio,reduce_stage=2 if ratio==.5 else 0)
 assert apply(e);assert len(e.c.amends)==1

@pytest.mark.parametrize('symbol',['DOGE/USDT:USDT','SOL/USDT:USDT'])
def test_core_floor_cannot_amend_candidate_c(env,symbol):
 e=env
 assert not trader._maybe_apply_core_profit_floor(e.cfg,e.c,symbol,e.p,e.obs)
 assert not e.c.amends

@pytest.mark.parametrize('outcome',['success','max_cumulative_reduction','below_minimum_size','fill_unresolved'])
def test_fast_cycle_applies_floor_after_reduce_reconciliation_and_skips_are_independent(env,monkeypatch,outcome):
 e=env;reduce.ensure_position(e.user,S,e.p,initial_contracts=8.)
 p=dict(e.p,lifecycle_id=S+'|long|'+e.opened['time'])
 observation=mfe.observe(e.user,S,p,sl_price=90.,price=103.,favorable_price=106.,bar_time=(dt.datetime.now(dt.timezone.utc)-dt.timedelta(minutes=1)).isoformat())
 e.obs=observation
 if outcome=='max_cumulative_reduction':reduce.update_fields(e.user,S,cumulative_reduced_ratio=.5,actual_reduced_contracts=4.,reduce_stage=2)
 order_calls=[]
 def reducer(*a,**kw):
  assert not e.c.amends,'defer floor until reduction outcome is reconciled'
  order_calls.append(1)
  if outcome=='success':
   e.c.position['contracts']=6.;e.c.size=6.
   reduce.update_fields(e.user,S,cumulative_reduced_ratio=.25,actual_reduced_contracts=2.,reduce_stage=1)
   return True
  reduce.record_block(e.user,S,outcome)
  if outcome=='fill_unresolved':reduce.update_fields(e.user,S,pending_order={'id':'unknown'})
  return False
 monkeypatch.setattr(trader,'_execute_position_ai_reduce_50',reducer)
 monkeypatch.setattr(trader,'_observe_mfe_profit_shadow',lambda *_:observation)
 monkeypatch.setattr(trader.core_entry_timing,'confirmed_frame',lambda f,*a,**kw:f)
 monkeypatch.setattr(trader,'_maybe_review_early_profit',lambda *_:False)
 trader._core_fast_profit_observation(e.cfg,e.state,e.c,S,e.p,{'1m':object()})
 if outcome=='fill_unresolved':
  assert not e.c.amends
  assert not mfe.get_state(e.user,S)['policies'][mfe.LIVE_POLICY_NAME].get('live_resolved')
  assert mfe.observe(e.user,S,p,sl_price=90.,price=103.,favorable_price=106.,bar_time='next')['live_candidate']
 else:
  assert len(e.c.amends)==1
  assert e.c.amends[0]['size']==(6. if outcome=='success' else 8.)
  assert e.c.amends[0]['tp']==120.
  policy=mfe.get_state(e.user,S)['policies'][mfe.LIVE_POLICY_NAME]
  assert policy['live_resolved']
  if outcome in ('max_cumulative_reduction','below_minimum_size'):assert policy['live_status']=='skipped'
 assert len(order_calls)==(0 if outcome=='max_cumulative_reduction' else 1)


def test_persisted_terminal_skip_remains_resolved_but_floor_independent(env,monkeypatch):
 e=env;reduce.ensure_position(e.user,S,e.p,initial_contracts=8.);reduce.record_block(e.user,S,'below_minimum_size')
 p=dict(e.p,lifecycle_id=S+'|long|'+e.opened['time'])
 observation=mfe.observe(e.user,S,p,sl_price=90.,price=103.,favorable_price=106.,bar_time='old')
 monkeypatch.setattr(trader,'_execute_position_ai_reduce_50',lambda *a,**k:pytest.fail('persisted skip must not resubmit'))
 assert not trader._maybe_execute_mfe_profit_live(e.cfg,e.state,e.c,S,e.p,{},observation)
 assert mfe.get_state(e.user,S)['policies'][mfe.LIVE_POLICY_NAME]['live_reason']=='below_minimum_size'
 e.obs=observation
 assert apply(e)
 assert mfe.observe(e.user,S,p,sl_price=90.,price=103.,favorable_price=106.,bar_time='after')['live_candidate'] is None

def test_historical_peak_is_revalidated_with_fresh_price_even_if_saved_current_was_loss(env):
 e=env;e.obs.update(current_r=-.2,giveback_r=.8)
 assert apply(e)


def test_nonfinite_confirmed_size_is_not_success(env,caplog):
 e=env;e.c.confirmed_bad={'sz':float('nan')}
 with caplog.at_level(logging.INFO):assert not apply(e)
 assert not any('CORE_PROFIT_FLOOR_LOCKED' in r.message for r in caplog.records)
