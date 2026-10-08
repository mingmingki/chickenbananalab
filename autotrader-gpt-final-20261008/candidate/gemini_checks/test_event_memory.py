import unittest,tempfile
from core_unified_store import Store
from core_unified_event_memory import EventMemory

class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=self.tmp.name+'/db';self.s=Store(self.path);self.addCleanup(lambda:self.s.close());self.m=EventMemory(self.s)
    def candidate(self,i,t):
        return dict(id=str(i),symbol='X',generation='g',signal_ms=t,expires_ms=t+60000,
            decision_position_id=None,snapshot_id='s',reference_price='100',atr5='2')
    def test_duplicate_restart_and_hold_context(self):
        c=self.candidate(1,100000);e=dict(id='e',kind='range_break',bar_ms=100000,directional=True)
        self.assertTrue(self.m.reserve(c,[e],100000,False)['allowed'])
        self.assertFalse(self.m.reserve(c,[e],100000,False)['allowed'])
        self.assertTrue(self.m.finish('1',dict(action='hold',thesis='keep'),100100))
        self.s.close();self.s=Store(self.path);self.m=EventMemory(self.s)
        self.assertEqual(self.m.context('X',None)['thesis'],'keep')
        self.assertFalse(self.m.reserve(c,[e],160000,False)['allowed'])
    def test_budget_counts_failed_calls_and_risk_reserve(self):
        for i in range(18):
            t=100000+i*60000;c=self.candidate(i,t)
            if i==12:self.assertFalse(self.m.reserve(c,[],t,False)['allowed'])
            self.assertTrue(self.m.reserve(c,[],t,i>=12)['allowed'])
            if i==17:self.m.fail(c['id'],'timeout',t+1)
            else:self.m.finish(c['id'],dict(action='hold'),t+1)
        self.assertFalse(self.m.reserve(self.candidate(19,t+60000),[],t+60000,True)['allowed'])
        self.assertTrue(self.m.reserve(self.candidate(20,t+3600000),[],t+3600000,False)['allowed'])
    def test_invalidation_and_lifecycle_filter(self):
        c=self.candidate(1,100000);c['decision_position_id']='old'
        self.m.reserve(c,[],100000,False);self.m.finish('1',dict(action='hold',thesis='old',invalidation_price=99),100010)
        self.assertNotIn('invalidation_price',self.m.context('X','new'))
        c=self.candidate(2,160000);self.m.reserve(c,[],160000,False)
        self.m.invalidate('X','new')
        self.assertFalse(self.m.finish('2',dict(action='long'),160001))
