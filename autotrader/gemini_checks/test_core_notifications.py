import json,unittest
from core_unified_store import Store
from core_unified_notifications import Notifications
class NotificationTests(unittest.TestCase):
 def setUp(self):
  self.s=Store(':memory:');self.addCleanup(self.s.close);self.sent=[];self.now=100000
 def fill(self,i,kind='ENTRY'):
  r=dict(symbol='X',kind=kind,side='long',qty='2',price='100',contract_size='1',filled_ms=100000,trade_id=str(i),intent_id='i'+str(i))
  self.s.db.execute('INSERT INTO accounting VALUES(?,?,?)',(str(i),r['intent_id'],json.dumps(r)))
 def engine(self,send=None):return Notifications(self.s,'X',send or self.sent.append,lambda:self.now)
 def test_old_records_skipped_new_types_sent_once_after_restart(self):
  self.fill(0);n=self.engine();n.tick();self.assertEqual(self.sent,[])
  for i,kind in enumerate(['ENTRY','ADD','REDUCE','EXIT'],1):self.fill(i,kind)
  for _ in range(4):n.tick()
  self.assertEqual(len(self.sent),4)
  for label,msg in zip(['진입','추가','감축','청산'],self.sent):self.assertIn(label,msg)
  self.engine().tick();self.assertEqual(len(self.sent),4)
 def test_failure_retains_notification_and_backoff(self):
  def fail(text):raise TimeoutError('secret must not appear')
  n=self.engine(fail);self.fill(1);n.tick();self.assertEqual(self.sent,[])
  n=self.engine();n.tick();self.assertEqual(self.sent,[])
  self.now+=60000;n.tick();self.assertEqual(len(self.sent),1)
 def test_pending_execution_not_announced_until_complete(self):
  n=self.engine();self.fill(1)
  self.s.db.execute('INSERT INTO intents VALUES(?,?,?,?)',('i1','X','pending','{}'))
  n.tick();self.assertEqual(self.sent,[])
  self.s.db.execute("UPDATE intents SET status='complete'")
  n.tick();self.assertEqual(len(self.sent),1)
