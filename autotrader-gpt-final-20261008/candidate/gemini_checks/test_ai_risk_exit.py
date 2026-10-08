"""Real controller/executor/store with an offline exchange; no live orders."""
from decimal import Decimal as D
import tempfile
import unittest
from unittest.mock import patch
import test_live as live_fixture
from core_unified_state import advance
from core_unified_store import Store
from core_unified_policy import POLICY


class AIRiskExitTests(unittest.TestCase):
    def setUp(self):
        self.f=live_fixture.ControllerTests('test_entry25_bound_actual_identity_and_protected')
        self.f.setUp(); self.addCleanup(self.f.tearDown)
        f=self.f
        f.cfg.GPT_ENTRY_GATE_ENABLED=False; f.cfg.MIN_HOLD_MINUTES=15
        f.cfg.POSITION_AI_REVIEW_ENABLED=True; f.cfg.POSITION_AI_LIVE_EXECUTE=True
        f.candidate.update(decision_source='gemini_periodic',review_mode='gemini',purpose='ENTRY',
                           decision_position_id=None,min_confidence=.6)
        f.approval.update(review_mode='gemini',gpt_decision='not_required',gpt_confidence=None)
        self.assertEqual(f.run_entry()['status'],'complete')
        self.now=1900000;self.fraction=D(1);self.reductions=[]
        f.c.clock=lambda:self.now; f.c.port.clock=lambda:self.now; f.c.manager.clock=lambda:self.now
        # Falling price, no profit-protection activation; away from emergency SL.
        f.client.fetch_last_price=lambda:99.5
        f.ex.pos['markPrice']=99.5
        f.client.reduce_position=self.reduce
        f.client.cancel_protection=lambda ids:f.ex.stops.clear()
        rows=[dict(close=99.5,ema20=100,ema50=101,macd=v,close_ms=1200000) for v in (1,0,-1)]
        f.snapshot={'generation':1,'frames':{'1m':rows,'5m':rows,'1h':rows}}

    def reduce(self,position,qty,*,client_order_id):
        f=self.f;requested=D(str(qty));filled=requested*self.fraction
        self.assertEqual(position['position_id'],f.store.state('X')['position_id'])
        self.assertLessEqual(requested,D(str(f.ex.pos['contracts'])))
        oid='reduce-'+client_order_id
        f.ex.orders[client_order_id]=dict(id=oid,clientOrderId=client_order_id,symbol='X',
            status='closed' if self.fraction==1 else 'canceled',filled=float(filled),remaining=float(requested-filled))
        f.ex.trades[oid]=[dict(id='fill-'+oid,order=oid,symbol='X',amount=float(filled),price=99.5,timestamp=self.now)]
        remaining=D(str(f.ex.pos['contracts']))-filled
        if remaining: f.ex.pos['contracts']=float(remaining)
        else: f.ex.pos=None
        self.reductions.append(float(filled))

    def decide(self,action,*,confidence=.8):
        f=self.f;st=f.store.state('X');purpose={'close':'CLOSE','reduce':'REDUCE','add':'ADD','short':'REVERSE'}[action]
        candidate=dict(f.candidate,id='review-'+str(self.now),purpose=purpose,decision_action=action,
            side='short' if action=='short' else 'long',signal_ms=self.now,expires_ms=self.now+60000,
            decision_position_id=st['position_id'])
        approval=dict(f.approval,candidate_id=candidate['id'],gemini_action=action,
                      gemini_confidence=confidence,completed_ms=self.now)
        with patch('core_unified_live.validate_snapshot',return_value=(True,'ok')):
            return f.c.management(candidate,approval,f.snapshot)

    def test_close_after_minimum_hold_executes_and_cleans_owned_stop(self):
        self.assertEqual(self.decide('close')['status'],'complete')
        self.assertEqual(self.reductions,[25.0])
        self.assertEqual(self.f.store.state('X')['phase'],'FLAT')
        self.assertIsNone(self.f.ex.pos);self.assertEqual(self.f.ex.stops,[])

    def test_losing_position_reduces_before_profit_arm_and_keeps_protection(self):
        self.assertEqual(self.decide('reduce')['status'],'complete')
        self.assertEqual(self.reductions,[6.0])
        state=self.f.store.state('X')
        self.assertEqual(D(state['filled_qty']),19);self.assertEqual(state['reduce_stage'],1)
        self.assertEqual(D(state['protect_base_qty']),25)
        self.assertFalse(state['protection_armed'])
        self.assertTrue(self.f.c.port.protection('X')['confirmed'])

    def test_two_ai_reductions_share_fixed_baseline_and_stop_at_half(self):
        self.assertEqual(self.decide('reduce')['status'],'complete')
        self.now+=300000;self.assertEqual(self.decide('reduce')['status'],'complete')
        self.now+=300000;result=self.decide('reduce')
        self.assertEqual(result['reason'],'reduce_limit_reached')
        self.assertEqual(self.reductions,[6.0,6.0])
        self.assertEqual(D(self.f.store.state('X')['filled_qty']),13)

    def test_partial_fill_baseline_survives_store_restart(self):
        self.fraction=D('.5')
        self.assertEqual(self.decide('reduce')['status'],'complete')
        state=self.f.store.state('X');self.assertEqual(D(state['reduce_partial_qty']),3)
        with tempfile.TemporaryDirectory() as directory:
            path=directory+'/state.sqlite3'
            target=Store(path);self.f.store.db.backup(target.db);target.close()
            self.f.store.close();self.f.store=Store(path)
            self.f.c.store=self.f.store;self.f.c.manager.store=self.f.store
            self.f.c.handoff.store=self.f.store
            self.fraction=D(1);self.now+=300000
            self.assertEqual(self.decide('reduce')['status'],'complete')
            self.assertEqual(self.reductions,[3.0,3.0])
            self.assertEqual(self.f.store.state('X')['reduce_stage'],1)
            self.assertEqual(D(self.f.store.state('X')['protect_base_qty']),25)

    def test_profit_arm_after_ai_reduce_does_not_reset_reduction_baseline(self):
        self.assertEqual(self.decide('reduce')['status'],'complete')
        state=advance(self.f.store.state('X'),dict(kind='MARK',price=102),POLICY)['state']
        self.assertEqual(D(state['protect_base_qty']),25)
        event=dict(kind='WEAK_5M',confirmed=True,weak_1h=True,bar_ms=2100000,lot=1,minimum=1)
        intent=advance(state,event,POLICY)['intent']
        self.assertEqual(intent['qty'],6);self.assertEqual(intent['stage'],2)

    def test_all_discretionary_actions_obey_minimum_holding_time(self):
        self.now=1300000
        for action in ('close','reduce','add','short'):
            self.assertEqual(self.decide(action)['reason'],'minimum_hold')
        self.assertEqual(self.reductions,[])

    def test_disabled_or_low_confidence_risk_exit_does_not_trade(self):
        self.f.cfg.POSITION_AI_REVIEW_ENABLED=False
        self.assertEqual(self.decide('close')['reason'],'position_ai_disabled')
        self.f.cfg.POSITION_AI_REVIEW_ENABLED=True
        for action in ('close','reduce'):
            self.assertEqual(self.decide(action,confidence=.59)['reason'],'not_approved')
        self.assertEqual(self.reductions,[])

    def test_duplicate_decision_cannot_repeat_reduction(self):
        self.assertEqual(self.decide('reduce')['status'],'complete')
        self.decide('reduce')
        self.assertEqual(self.reductions,[6.0])

    def test_emergency_close_does_not_wait_for_minimum_hold(self):
        self.now=1300000
        self.assertEqual(self.f.c.manual_close('protection_missing')['status'],'complete')
        self.assertIsNone(self.f.ex.pos)

    def test_pre_stop_emergency_still_exits_during_minimum_hold(self):
        self.now=1300000
        self.f.ex.pos['markPrice']=98.1
        self.f.c.risk_tick(self.f.snapshot)
        self.now+=30000
        self.assertEqual(self.f.c.risk_tick(self.f.snapshot)['status'],'complete')
        self.assertIsNone(self.f.ex.pos)
