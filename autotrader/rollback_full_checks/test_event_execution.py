import unittest
from decimal import Decimal as D
from unittest.mock import patch
from types import SimpleNamespace as NS
import test_live as fixture
from core_unified_event_memory import EventMemory
from core_unified_live import LiveController
from core_unified_policy import entry_qty,approval_valid
from test_event_review import decision

class EventExecutionTests(unittest.TestCase):
    def test_entry_quantities_and_fee_risk_cap(self):
        for fraction in (.25,.5,.75,1):
            f=fixture.ControllerTests();f.setUp()
            try:
                f.cfg.CORE_EVENT_AI_ENABLED=True;f.cfg.GPT_ENTRY_GATE_ENABLED=False
                g=decision(fraction=fraction)
                c=f.candidate;c.update(decision_source='gemini_event',purpose='ENTRY',review_mode='gemini',
                    decision_position_id=None,event_ids=[],entry_fraction=fraction,event_decision=g)
                f.approval.update(review_mode='gemini',entry_fraction=fraction)
                m=EventMemory(f.store);m.reserve(c,[],999000,False);m.finish(c['id'],g,999100)
                # Real guard, with only external pause/baseline boundaries isolated.
                f.c.entry_guard=LiveController.entry_guard.__get__(f.c)
                modules={'core_kill_switch':NS(is_active=lambda d:False),
                    'symbol_entry_control':NS(is_paused=lambda d,s:False),
                    'core_manual_close':NS(get=lambda d,s:None,block_reason=lambda *a,**k:None)}
                with patch.dict('sys.modules',modules),patch('core_unified_accounting.allow_new_entry',return_value=True):
                    outcome=f.run_entry()
                self.assertEqual(outcome['status'],'complete',outcome)
                state=f.store.state('X');target=D(state['target_qty'])
                self.assertEqual(D(state['filled_qty']),entry_qty(c,target,1,1))
                self.assertLessEqual(D(state['entry_risk_used']),D(state['risk_budget']))
                if fraction==1:self.assertEqual(D(state['filled_qty']),target)
                self.assertTrue(f.c.port.protection('X')['confirmed'])
            finally:f.tearDown()
    def test_fraction_tampering_rejected(self):
        f=fixture.ControllerTests();f.setUp();self.addCleanup(f.tearDown)
        f.candidate.update(decision_source='gemini_event',purpose='ENTRY',entry_fraction=1)
        f.approval['entry_fraction']=.25
        self.assertFalse(approval_valid(f.candidate,f.approval,1000000,100)[0])
    def test_unpersisted_decision_is_rejected(self):
        f=fixture.ControllerTests();f.setUp();self.addCleanup(f.tearDown)
        f.cfg.CORE_EVENT_AI_ENABLED=True
        self.assertEqual(f.c.event_decision_guard(dict(f.candidate,decision_source='gemini_event')),'event_decision_unverified')
