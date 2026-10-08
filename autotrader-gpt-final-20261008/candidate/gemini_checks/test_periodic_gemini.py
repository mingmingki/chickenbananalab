import unittest
from unittest.mock import Mock, patch
from core_unified_service import Coordinator
from core_unified_review import ReviewBroker
from core_unified_adapters import review_prompt, GEMINI_REVIEW_SCHEMA
from test_market import snapshot
from test_live import ControllerTests


class PeriodicReviewTests(unittest.TestCase):
    def test_all_symbols_review_every_300_seconds_without_indicator_candidate(self):
        for symbol in ('BTC','ETH','XRP','PI'):
            with self.subTest(symbol=symbol):
                now=[3600000]; mono=[0]
                def market():
                    s=snapshot(); s.update(symbol=symbol,now_ms=now[0])
                    for tf, rows in s['frames'].items():
                        for row in rows:
                            row['open_ms']+=now[0]-3600000; row['close_ms']+=now[0]-3600000
                            row.update(close=99,ema20=100,volume=1,macd=0)
                    return s
                feed=Mock(); feed.snapshot.side_effect=market
                broker=Mock(); broker.offer.return_value=True
                broker.drain.return_value=[]; broker.drain_events.return_value=[]
                controller=Mock(); controller.status.return_value={'owner':'unified','state':None}
                c=Coordinator(symbol,feed,broker,controller,lambda:now[0],lambda:mono[0],
                    lambda:(1,False),mode=lambda c:'gemini',review_interval=lambda:300)
                c.tick()
                self.assertEqual(broker.offer.call_count,1)
                self.assertEqual(broker.offer.call_args.args[0]['purpose'],'DECIDE')
                mono[0]=299; c.tick(); self.assertEqual(broker.offer.call_count,1)
                mono[0]=300; now[0]+=300000; c.tick()
                self.assertEqual(broker.offer.call_count,2)

    def test_decision_prompt_allows_independent_direction_and_management(self):
        c={'purpose':'DECIDE','side':'undecided'}; s=snapshot()
        prompt=review_prompt(c,s,'gemini')
        self.assertIn('primary decision',prompt)
        self.assertTrue({'long','short','hold','close','reduce','add'} <=
                        set(GEMINI_REVIEW_SCHEMA['properties']['action']['enum']))

    def review(self,action,position=None,gpt=False,confidence=.65):
        g=Mock(return_value=dict(action=action,confidence=confidence,reasoning='판단 근거'))
        p=Mock(return_value=dict(decision='approve_now',confidence=.8))
        b=ReviewBroker(g,p,lambda:2000,require_gpt=lambda:gpt)
        c=dict(id='c',symbol='X',side='undecided',purpose='DECIDE',signal_ms=1000,
            expires_ms=61000,generation=1,snapshot_id='s',min_confidence=.6,
            review_mode='dual' if gpt else 'gemini',decision_source='gemini_periodic')
        self.assertTrue(b.offer(c,dict(position=position)))
        b.close(); return b.drain(),b.drain_events(),p

    def test_gemini_chooses_direction_with_saved_confidence_and_no_gpt(self):
        for action in ('long','short'):
            rows,events,gpt=self.review(action)
            self.assertEqual(rows[0]['resolved_candidate']['side'],action)
            self.assertEqual(rows[0]['purpose'],'ENTRY'); gpt.assert_not_called()
        rows,events,gpt=self.review('hold')
        self.assertEqual(rows,[]); self.assertEqual(events[0]['reason'],'gemini_decision')
        gpt.assert_not_called()
        self.assertEqual(self.review('long',confidence=.59)[0],[])

    def test_optional_gpt_only_for_entry_or_reverse(self):
        rows,events,gpt=self.review('short',gpt=True)
        gpt.assert_called_once(); self.assertEqual(rows[0]['review_mode'],'dual')
        for action in ('close','reduce','add','hold','long'):
            rows,events,gpt=self.review(action,{'side':'long'},gpt=True)
            gpt.assert_not_called()
            if rows: self.assertEqual(rows[0]['review_mode'],'gemini')
        rows,events,gpt=self.review('short',{'side':'long'},gpt=True)
        self.assertEqual(rows[0]['purpose'],'REVERSE'); gpt.assert_called_once()


class PeriodicExecutionTests(ControllerTests):
    def periodic(self):
        self.cfg.GPT_ENTRY_GATE_ENABLED=False
        self.candidate.update(decision_source='gemini_periodic',review_mode='gemini',
            purpose='ENTRY',decision_position_id=None,min_confidence=.6)
        self.approval.update(review_mode='gemini',gemini_confidence=.65,
            gpt_decision='not_required',gpt_confidence=None)

    def test_gemini_entry_not_vetoed_by_ema_momentum_or_quarter_atr(self):
        self.periodic(); self.candidate.update(reference_price=90,atr5=1)
        self.snapshot['frames']['1m'][-1].update(close=98,macd=-1)
        self.assertEqual(self.run_entry()['status'],'complete')
        self.assertEqual(float(self.store.state('X')['filled_qty']),25)
        self.assertEqual(self.store.state('X')['decision_source'],'gemini_periodic')
        self.assertEqual(self.store.state('X')['opened_ms'],1000000)

    def test_changed_position_cannot_apply_stale_flat_decision(self):
        self.periodic(); self.assertEqual(self.run_entry()['status'],'complete')
        self.c.risk_tick=Mock(return_value={'status':'idle'})
        self.assertEqual(self.c.management(self.candidate,self.approval,self.snapshot)['reason'],
                         'decision_position_changed')

    def test_close_obeys_minimum_hold_and_position_checkbox(self):
        self.periodic(); self.run_entry(); state=self.store.state('X')
        self.candidate.update(purpose='CLOSE',decision_action='close',decision_position_id=state['position_id'])
        self.approval['gemini_action']='close'
        self.c.risk_tick=Mock(return_value={'status':'idle'})
        self.cfg.MIN_HOLD_MINUTES=15
        self.cfg.POSITION_AI_REVIEW_ENABLED=False
        self.assertEqual(self.c.management(self.candidate,self.approval,self.snapshot)['reason'],'position_ai_disabled')
        self.cfg.POSITION_AI_REVIEW_ENABLED=True
        self.assertEqual(self.c.management(self.candidate,self.approval,self.snapshot)['reason'],'minimum_hold')
        self.cfg.MIN_HOLD_MINUTES=0
        with patch.object(self.c,'_event',return_value={'status':'complete'}) as action:
            self.assertEqual(self.c.management(self.candidate,self.approval,self.snapshot)['status'],'complete')
            self.assertEqual(action.call_args.args[0]['kind'],'REVERSE_APPROVED')
            self.assertIsNone(self.c.pending_reverse)

    def test_periodic_probe_keeps_stop_protection_without_ema_forced_exit(self):
        self.periodic(); self.run_entry()
        s=snapshot(); s['frames']['1m'][-1].update(close=98,ema20=100,macd=-1)
        s['frames']['1m'][-2]['macd']=0
        s['frames']['1h']=s['frames']['5m']
        with patch('core_unified_live.validate_snapshot',return_value=(True,'ok')):
            for bar in (3600000,3660000):
                s['frames']['1m'][-1]['close_ms']=bar
                self.assertEqual(self.c.risk_tick(s)['status'],'idle')
        self.assertEqual(self.store.state('X')['invalid_bars'],[])
        self.assertEqual(float(self.store.state('X')['filled_qty']),25)
        self.assertTrue(self.c.port.protection('X')['confirmed'])

    def test_periodic_reduce_is_capped_and_add_still_requires_profit(self):
        self.periodic(); self.run_entry(); state=self.store.state('X')
        self.cfg.MIN_HOLD_MINUTES=0
        self.c.risk_tick=Mock(return_value={'status':'idle'})
        self.c.risk_remaining=lambda price:200
        self.candidate.update(purpose='ADD',decision_action='add',decision_position_id=state['position_id'])
        self.approval['gemini_action']='add'
        # Unprofitable add remains ineligible even with Gemini approval.
        self.assertEqual(self.c.management(self.candidate,self.approval,self.snapshot)['status'],'idle')
        self.assertEqual(len(self.ex.calls),1)
        self.store.patch_metadata('X',protection_armed=True,protect_base_qty=24,reduce_stage=0)
        self.candidate.update(purpose='REDUCE',decision_action='reduce',signal_ms=1000000)
        self.approval.update(gemini_action='reduce',completed_ms=1000000)
        with patch.object(self.c,'_execute',return_value={'status':'complete'}) as execute:
            self.c.management(self.candidate,self.approval,self.snapshot)
            self.assertEqual(float(execute.call_args.args[0]['qty']),6)
        self.store.patch_metadata('X',reduce_stage=2)
        self.assertEqual(self.c.management(self.candidate,self.approval,self.snapshot)['status'],'idle')

if __name__=='__main__': unittest.main()
