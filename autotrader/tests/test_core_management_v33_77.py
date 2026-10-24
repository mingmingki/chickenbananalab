"""v33-77: closed multi-timeframe evidence and independent exit approval."""
import datetime as dt
import logging
from types import SimpleNamespace
import pytest
import trader
from state import TraderState

NOW=1792513200.0

def rows(direction,tf,now=NOW):
    seconds={'5m':300,'15m':900,'1h':3600,'4h':14400}[tf]
    return [dict(ts=dt.datetime.fromtimestamp(now-seconds*(3-i)-10,dt.timezone.utc).isoformat(),
                 close=100+direction*(i+1),ema_20=100+direction*i,
                 ema_50=99 if direction>0 else 101,macd=direction*(i+1),
                 rsi_14=60 if direction>0 else 40,atr_14=2.) for i in range(3)]

def context(side='long',directions=None):
    import core_management_context_v33_77 as ctx
    directions=directions or {'5m':-1,'15m':1,'1h':1,'4h':1}
    return ctx.build(side,{tf:rows(d,tf) for tf,d in directions.items()},now=NOW)

def test_upper_trend_supports_pullback_without_order_authority():
    x=context()
    assert x['regime']=='pullback_candidate'
    assert x['complete'] is True
    assert x['order_authority'] is False
    assert x['frames']['1h']['state']=='aligned'

def test_short_pullback_is_symmetric():
    assert context('short',{'5m':1,'15m':-1,'1h':-1,'4h':-1})['regime']=='pullback_candidate'

def test_reversal_requires_both_upper_frames():
    assert context(directions={'5m':-1,'15m':-1,'1h':-1,'4h':1})['regime']=='trend_invalidated'
    assert context(directions={'5m':-1,'15m':-1,'1h':1,'4h':1})['regime']=='range_or_conflict'

def test_missing_15m_is_unknown_not_confirmed_recovery():
    import core_management_context_v33_77 as ctx
    x=ctx.build('long',{'5m':rows(-1,'5m'),'1h':rows(1,'1h'),'4h':rows(1,'4h')},now=NOW)
    assert x['regime']=='unknown' and not x['complete']

@pytest.mark.parametrize('bad', [float('nan'),float('inf'),True,None])
def test_invalid_htf_numbers_are_unknown(bad):
    import core_management_context_v33_77 as ctx
    frames={tf:rows(1,tf) for tf in ['5m','15m','1h','4h']}
    frames['1h'][-1]['ema_20']=bad
    assert ctx.build('long',frames,now=NOW)['regime']=='unknown'

def test_stale_future_or_noncontiguous_bars_are_unknown():
    import core_management_context_v33_77 as ctx
    for kind in ['stale','future','gap']:
        frames={tf:rows(1,tf) for tf in ['5m','15m','1h','4h']}
        if kind=='stale':frames['1h']=rows(1,'1h',NOW-10000)
        if kind=='future':frames['1h']=rows(1,'1h',NOW+4000)
        if kind=='gap':frames['1h'][1]['ts']=frames['1h'][0]['ts']
        assert ctx.build('long',frames,now=NOW)['regime']=='unknown'

def test_context_render_is_idempotent():
    import core_management_context_v33_77 as ctx
    one=ctx.with_context('summary',context())
    assert ctx.with_context(one,context())==one
    assert '0.35R' in one and 'HOLD' in one

def exercise(monkeypatch,tmp_path,verdict,action='CLOSE_ALL'):
    cfg=SimpleNamespace(user_dir=str(tmp_path),logger=logging.getLogger('v33-77'),
        CORE_AI_STRATEGY_AUTHORITY=True,CORE_GEMINI_MANAGEMENT_ONLY=True,
        CORE_MANAGEMENT_GPT_VERIFICATION=True,MIN_CONFIDENCE=.6,
        POSITION_AI_REVIEW_COOLDOWN_MINUTES=15,POSITION_AI_LIVE_EXECUTE=True,
        EXECUTION_MODE='LIVE',RISK_ADAPTIVE_PARTIAL_ENABLED=True)
    pos=dict(side='long',contracts=5.,entry_price=100.,mark_price=95.,
             unrealized_pnl=-25.,position_id='p',entry_timestamp_ms=1)
    protection=dict(algo_id='oco',sz=5.,sl_price=90.,tp_price=120.)
    review=dict(assessment='invalidated',confidence=.9,reasoning='confirmed reversal',
                management_action=action,risk_level='high',suggested_reduce_fraction=.25)
    monkeypatch.setattr(trader.core_ai_context,'reuse',lambda *a,**k:review)
    monkeypatch.setattr(trader,'_closed_indicator_tail',lambda raw,tf,n=3:rows(-1,tf))
    monkeypatch.setattr(trader,'_position_ai_close_guard',
                        lambda *a,**k:dict(allow_close_all=True,loss_r=.5,reason='breakdown',breakdown=True))
    calls=[]; closes=[]
    def verify(*a,**kw):
        calls.append(kw);return verdict
    monkeypatch.setattr(trader.openai_analyzer,'verify_position_management',verify)
    monkeypatch.setattr(trader,'_execute_close',lambda *a,**k:closes.append(k) or True)
    monkeypatch.setattr(trader.gemini_analyzer,'analyze_held_position',
        lambda *a,**k:pytest.fail('fresh Gemini review must be reused'))
    trader._handle_position_ai_review(cfg,TraderState(),
        SimpleNamespace(fetch_current_protection=lambda *_:protection),
        'BTC/USDT:USDT',['5m','15m','1h','4h'],'summary',pos,{'action':'hold'}, {})
    return calls,closes

@pytest.mark.parametrize('verdict',[None,{'action':None,'error_reason':'timeout'},
    {'action':'HOLD','confidence':.9,'reasoning':'pullback'},
    {'action':'CLOSE_ALL','confidence':.1,'reasoning':'uncertain'},
    {'action':'ADD_POSITION','confidence':.9,'reasoning':'not permitted'}])
def test_unapproved_exit_keeps_position_and_protection(monkeypatch,tmp_path,verdict):
    calls,closes=exercise(monkeypatch,tmp_path,verdict)
    assert len(calls)==1
    assert closes==[]
    assert calls[0]['allowed_actions']==('HOLD','CLOSE_ALL')
    assert calls[0]['max_retries']==0

def test_approved_exit_uses_one_gpt_and_zero_extra_gemini(monkeypatch,tmp_path):
    calls,closes=exercise(monkeypatch,tmp_path,{'action':'CLOSE_ALL','confidence':.9,'reasoning':'invalidated'})
    assert len(calls)==1 and len(closes)==1

def test_hold_never_pays_for_exit_approval(monkeypatch,tmp_path):
    calls,closes=exercise(monkeypatch,tmp_path,None,action='HOLD')
    assert calls==[] and closes==[]

def test_partial_verdict_cannot_expand_or_replace_proposal():
    import core_management_context_v33_77 as ctx
    proposal=dict(action='REDUCE_50',confidence=.8,reasoning='risk',reduce_fraction=.2,risk_level='medium')
    verdict=dict(action='REDUCE_50',confidence=.9,reasoning='reduce',reduce_fraction=.4,risk_level='high')
    assert ctx.verified_proposal(proposal,verdict,.6)['reduce_fraction']==.2
    verdict['reduce_fraction']=.1
    assert ctx.verified_proposal(proposal,verdict,.6)['reduce_fraction']==.1
    verdict['reduce_fraction']=float('nan')
    assert ctx.verified_proposal(proposal,verdict,.6)['action']=='HOLD'

def test_fast_negative_review_receives_15m_without_touching_orders(monkeypatch,tmp_path):
    cfg=SimpleNamespace(user_dir=str(tmp_path),logger=logging.getLogger('fast-v33-77'),
        EXECUTION_MODE='LIVE',CORE_NEGATIVE_GUARD_ENABLED=True,
        CORE_AI_STRATEGY_AUTHORITY=True,CORE_GEMINI_MANAGEMENT_ONLY=True,
        CORE_MANAGEMENT_GPT_VERIFICATION=True)
    pos=dict(side='long',contracts=2.,entry_price=100.)
    class Client:
        def fetch_position(self):return pos
        def fetch_current_protection(self,*a):return dict(sz=2.,sl_price=90.)
        def fetch_last_price(self):return 96.
        def fetch_multi_ohlcv(self,tfs):return {tf:object() for tf in tfs}
    for name in ['_reconcile_pending_core_reduce','_reconcile_pending_core_add']:
        monkeypatch.setattr(trader,name,lambda *a,**k:None)
    for name in ['_maybe_emergency_close_near_stop','_core_recovery_priority_pending','_core_fast_profit_observation']:
        monkeypatch.setattr(trader,name,lambda *a,**k:False)
    monkeypatch.setattr(trader.core_manual_close,'get',lambda *a:None)
    monkeypatch.setattr(trader,'_position_ai_review_candidate',lambda *a:True)
    monkeypatch.setattr(trader,'_ensure_reduce_position',lambda *a:dict(baseline_known=True,reduce_stage=0))
    monkeypatch.setattr(trader,'_negative_guard_numeric_diagnostics',
                        lambda *a,**k:dict(numeric_conditions_met=True,closed_5m_ts='new'))
    monkeypatch.setattr(trader.indicators,'add_indicators',lambda x:x)
    monkeypatch.setattr(trader.indicators,'summarize_multi_timeframe_compact',lambda *a:'summary')
    def capture(*a):
        assert all(tf in a[-1] for tf in ['5m','15m','1h','4h'])
        return True
    monkeypatch.setattr(trader,'_maybe_negative_guard_review',capture)
    assert trader._run_core_fast_tick(cfg,TraderState(),Client(),'BTC/USDT:USDT') is True

def test_config_enables_verification_without_risk_increase(tmp_path):
    import config
    (tmp_path/'.env').write_text('LEVERAGE=5\nMAX_LEVERAGE=5\nPOSITION_FIXED_USDT=200\nRISK_PER_TRADE_PCT=0.5\nMAX_DAILY_LOSS_PCT=5\n')
    c=config.UserConfig(str(tmp_path))
    assert c.CORE_MANAGEMENT_GPT_VERIFICATION is True
    assert (c.LEVERAGE,c.MAX_LEVERAGE,c.POSITION_FIXED_USDT,c.RISK_PER_TRADE_PCT,c.MAX_DAILY_LOSS_PCT)==(5,5,200.,.5,5.)

def test_fresh_identical_context_preserves_gemini_reuse(tmp_path):
    import core_ai_context as reuse
    import core_management_context_v33_77 as ctx
    import time
    c=SimpleNamespace(CORE_AI_STRATEGY_AUTHORITY=True,CORE_GEMINI_MANAGEMENT_ONLY=True,MIN_CONFIDENCE=.6)
    review=dict(assessment='weakening',confidence=.8,reasoning='pullback',management_action='HOLD',risk_level='medium')
    pos=dict(side='long',contracts=2.,entry_price=100.,mark_price=99.)
    prot=dict(sl_price=90.,tp_price=120.,sz=2.)
    text=ctx.with_context('summary',context())
    decision=dict(position_review=review)
    reuse.bind(decision,c,'BTC',['5m','15m','1h','4h'],text,pos,prot,time.time())
    assert reuse.reuse(decision,c,'BTC',['5m','15m','1h','4h'],ctx.with_context(text,context()),pos,prot)==review

@pytest.mark.parametrize('reason',['signal_close','reversal_close','position_ai_close_all'])
def test_close_execution_boundary_rejects_legacy_unverified_approval(monkeypatch,tmp_path,reason):
    cfg=SimpleNamespace(user_dir=str(tmp_path),logger=logging.getLogger('boundary-v33-77'),
        EXECUTION_MODE='LIVE',CORE_AI_STRATEGY_AUTHORITY=True,
        CORE_MANAGEMENT_GPT_VERIFICATION=True,MIN_CONFIDENCE=.6,AI_LIVE_CLOSE=True)
    pos=dict(side='long',contracts=2.,entry_price=100.,position_id='p',entry_timestamp_ms=1)
    monkeypatch.setattr(trader.core_unified_service,'routed_close',
        lambda *a:pytest.fail('unverified discretionary close crossed execution boundary'))
    assert trader._execute_close(cfg,TraderState(),object(),'BTC/USDT:USDT',pos,reason) is False

def test_receipt_binds_action_lifecycle_quantity_and_time():
    import core_management_context_v33_77 as ctx
    result=dict(action='REDUCE_50',confidence=.8,gpt_confidence=.9,gpt_verified=True,reduce_fraction=.2)
    receipt=ctx.make_receipt(result,'BTC','p:1:long',5.,NOW)
    args=dict(symbol='BTC',lifecycle_id='p:1:long',contracts=5.,action='REDUCE_50',reduce_fraction=.2,
              min_confidence=.6,now=NOW+1)
    assert ctx.valid_receipt(receipt,**args)
    for key,value in [('symbol','ETH'),('lifecycle_id','q:1:long'),('contracts',4.),
                       ('action','CLOSE_ALL'),('reduce_fraction',.3),('now',NOW+181),('now',NOW-1)]:
        assert not ctx.valid_receipt(receipt,**dict(args,**{key:value}))

def test_early_profit_review_fetches_missing_upper_context_once(monkeypatch,tmp_path):
    cfg=SimpleNamespace(user_dir=str(tmp_path),logger=logging.getLogger('early-v33-77'),
        EXECUTION_MODE='LIVE',CORE_AI_STRATEGY_AUTHORITY=True,CORE_GEMINI_MANAGEMENT_ONLY=True,
        CORE_MANAGEMENT_GPT_VERIFICATION=True)
    pos=dict(side='long',contracts=2.,entry_price=100.,position_id='p',entry_timestamp_ms=1)
    calls=[]
    class Client:
        def fetch_position(self):return pos
        def fetch_multi_ohlcv(self,tfs):
            calls.append(tfs);return {tf:object() for tf in tfs}
    monkeypatch.setattr(trader,'_position_ai_review_candidate',lambda *a:True)
    def capture(*a,**kw):
        assert all(tf in a[-1] for tf in ['5m','15m','1h','4h'])
        assert all(tf in a[4] for tf in ['5m','15m','1h','4h'])
    monkeypatch.setattr(trader,'_handle_position_ai_review',capture)
    observation={'early_review_candidate':dict(mfe_r=.3,giveback_r=.2)}
    assert trader._maybe_review_early_profit(cfg,TraderState(),Client(),'BTC',pos,{'1m':object(),'5m':object()},observation)
    assert calls==[['15m','1h','4h']]

@pytest.mark.parametrize('adaptive',[False,True])
def test_real_gpt_verifier_uses_variable_size_for_gemini_policy(monkeypatch,tmp_path,adaptive):
    import json
    import openai_analyzer as ai
    captured=[]
    class Client:
        max_retries=0
        def with_options(self,**kw):return self
        def create(self,**kw):
            captured.append(kw['messages'][0]['content'])
            return SimpleNamespace(usage=None,choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(
                dict(action='REDUCE_50',confidence=.9,reasoning='risk',reduce_fraction=.2,risk_level='medium'))))])
    client=Client();client.chat=SimpleNamespace(completions=client)
    monkeypatch.setattr(ai,'_get_client',lambda c:client)
    monkeypatch.setattr(ai,'_ensure_response_models_warmed_up',lambda:None)
    c=SimpleNamespace(user_dir=str(tmp_path),logger=logging.getLogger('prompt-v33-77'),
        OPENAI_API_KEY='unit-test-only',OPENAI_MODEL='fake',CORE_AI_STRATEGY_AUTHORITY=True,
        CORE_GEMINI_MANAGEMENT_ONLY=True,RISK_ADAPTIVE_PARTIAL_ENABLED=adaptive)
    pos=dict(side='long',contracts=2.,entry_price=100.,mark_price=96.,unrealized_pnl=-8.)
    result=ai.verify_position_management(c,'BTC',['5m','15m','1h','4h'],'facts',pos,
        {'action':'hold'},dict(assessment='weakening',confidence=.9,reasoning='risk',
                              risk_level='medium',suggested_reduce_fraction=.2),
        allowed_actions=('HOLD','REDUCE_50'),purpose='core_management_verification',max_retries=0)
    assert result['action']=='REDUCE_50' and result['reduce_fraction']==.2
    assert '0.05..0.50' in captured[0]

def test_partial_execution_boundary_rejects_gemini_only_payload(monkeypatch,tmp_path):
    import time
    cfg=SimpleNamespace(user_dir=str(tmp_path),logger=logging.getLogger('reduce-boundary-v33-77'),
        EXECUTION_MODE='LIVE',CORE_AI_STRATEGY_AUTHORITY=True,CORE_GEMINI_MANAGEMENT_ONLY=True,
        CORE_MANAGEMENT_GPT_VERIFICATION=True,RISK_ADAPTIVE_PARTIAL_ENABLED=True,MIN_CONFIDENCE=.6)
    pos=dict(side='long',contracts=5.,entry_price=100.,position_id='p',entry_timestamp_ms=1)
    sv=dict(baseline_known=True,cumulative_reduced_ratio=0.,reduce_stage=0)
    monkeypatch.setattr(trader.core_kill_switch,'is_active',lambda *a:False)
    monkeypatch.setattr(trader.core_manual_close,'get',lambda *a:None)
    monkeypatch.setattr(trader.core_add_position_state,'get',lambda *a:{})
    monkeypatch.setattr(trader,'_ensure_reduce_position',lambda *a,**kw:sv)
    approval=dict(lifecycle_id=trader.reduce_v2_state.position_identity(pos),started_at=time.time(),
        path='ai_strategy',gemini_confidence=.9,gemini_risk_level='medium',gemini_reduce_fraction=.2,
        authority='gemini_management',management_action='REDUCE_50',risk_level='medium',
        reduce_fraction=.2,code_fraction_policy=trader.core_management_policy.POLICY_HASH)
    def forbidden(*a):pytest.fail('unverified partial crossed execution boundary')
    client=SimpleNamespace(fetch_position=lambda:pos,fetch_multi_ohlcv=forbidden)
    assert trader._execute_position_ai_reduce_50_locked(cfg,TraderState(),client,'BTC',pos,'review',.9,
        'weakening',{'_core_reduce_approval':approval}) is False

@pytest.mark.parametrize('entry_reply',[
    {'decision':'approve_now','confidence':.9},
    {'decision':None,'error_reason':'timeout','timeout_confirmed':True}])
def test_reversal_entry_approval_or_timeout_never_substitutes_exit_approval(monkeypatch,tmp_path,entry_reply):
    import time
    cfg=SimpleNamespace(user_dir=str(tmp_path),logger=logging.getLogger('reversal-v33-77'),
        CORE_AI_STRATEGY_AUTHORITY=True,CORE_GEMINI_MANAGEMENT_ONLY=True,
        CORE_MANAGEMENT_GPT_VERIFICATION=True,MIN_CONFIDENCE=.6)
    pos=dict(side='long',contracts=5.,entry_price=100.,mark_price=95.,unrealized_pnl=-25.,
             position_id='p',entry_timestamp_ms=1)
    review=dict(assessment='invalidated',confidence=.9,reasoning='reversal',management_action='CLOSE_ALL',risk_level='high')
    decision=dict(action='short',_held_review=review,_held_review_snapshot={'created_at':time.time()})
    calls=[]
    monkeypatch.setattr(trader.core_unified_service,'owner',lambda *a:'legacy')
    monkeypatch.setattr(trader,'_set_core_entry_outcome',lambda *a,**k:None)
    monkeypatch.setattr(trader.openai_analyzer,'verify_position_management',lambda *a,**k:calls.append(k) or None)
    monkeypatch.setattr(trader,'_execute_close',lambda *a,**k:pytest.fail('no independent close approval'))
    monkeypatch.setattr(trader,'_execute_entry',lambda *a,**k:pytest.fail('existing position must remain'))
    client=SimpleNamespace(fetch_current_protection=lambda *a:dict(sl_price=90.,tp_price=120.,sz=5.))
    assert trader._execute_approved_entry_with_optional_reversal(
        cfg,TraderState(),client,'BTC','short',1.,95.,100.,80.,reversal_position=pos,
        decision=decision,decision_id='candidate',gpt_result=entry_reply,
        management_tf_list=['5m','15m','1h','4h'],management_summary='facts',management_raw_dfs={}) is False
    assert len(calls)==1 and calls[0]['allowed_actions']==('HOLD','CLOSE_ALL')

def test_upper_context_fetch_failure_is_unknown_without_fabricated_frames(tmp_path):
    cfg=SimpleNamespace(CORE_MANAGEMENT_GPT_VERIFICATION=True,logger=logging.getLogger('missing-v33-77'))
    def fail(*a):raise RuntimeError('market data unavailable')
    frames=trader._management_review_frames(cfg,SimpleNamespace(fetch_multi_ohlcv=fail),{'5m':object()})
    assert set(frames)=={'5m'}
    assert trader._management_structure_context('long',{})['complete'] is False
