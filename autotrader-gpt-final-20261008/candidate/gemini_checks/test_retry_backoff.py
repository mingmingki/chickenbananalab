import unittest
from core_unified_store import Store
from core_unified_event_memory import EventMemory
class BackoffTests(unittest.TestCase):
 def test_failures_back_off_and_restart_preserves(self):
  s=Store(':memory:');self.addCleanup(s.close);m=EventMemory(s)
  def c(i,t):return dict(id=str(i),symbol='X',generation='g',signal_ms=t,expires_ms=t+60000,reference_price='100',atr5='2')
  t=100000
  for i,delay in enumerate([300000,600000,1200000,1800000]):
   self.assertTrue(m.reserve(c(i,t),[],t,False)['allowed'])
   m.fail(str(i),'ServerError',t+1000)
   m=EventMemory(s)
   denied=m.reserve(c('next',t+60000),[],t+60000,True)
   self.assertEqual(denied['reason'],'event_error_backoff')
   self.assertEqual(denied['next_allowed_ms'],t+1000+delay)
   t+=1000+delay
  self.assertTrue(m.reserve(c('ok',t),[],t,False)['allowed'])
  m.finish('ok',dict(action='hold'),t+1)
  self.assertTrue(m.reserve(c('after',t+60000),[],t+60000,False)['allowed'])
