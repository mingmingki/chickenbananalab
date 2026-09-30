import unittest
from unittest.mock import Mock
from core_unified_store import Store
from core_unified_event_memory import EventMemory
from core_unified_service import Coordinator
from test_market import snapshot

class EventServiceTests(unittest.TestCase):
    def setUp(self):
        self.now=3600000;self.mono=0;self.s=Store(':memory:');self.addCleanup(self.s.close);self.memory=EventMemory(self.s)
        self.feed=Mock();self.feed.snapshot.side_effect=self.market
        self.b=Mock();self.b.busy.return_value=False;self.b.offer.return_value=True;self.b.drain.return_value=[];self.b.drain_events.return_value=[]
        self.ctrl=Mock();self.ctrl.status.return_value=dict(owner='unified',state=None);self.ctrl.pending_reverse=None
        self.paused=False
        self.c=Coordinator('X',self.feed,self.b,self.ctrl,lambda:self.now,lambda:self.mono,lambda:('g',self.paused),
            mode=lambda c:'gemini',review_interval=lambda:300,event_mode=lambda:True,event_memory=self.memory)
    def market(self):
        s=snapshot();s.update(now_ms=self.now,symbol='X')
        for rows in s['frames'].values():
            for r in rows:
                r['open_ms']+=self.now-3600000;r['close_ms']+=self.now-3600000;r['close']=100;r['atr14']=2
        if self.mono>=60:s['frames']['1m'][-1]['close']=103
        return s
    def test_event_before_five_minutes_and_hold_persisted(self):
        self.c.tick();c=self.b.offer.call_args.args[0]
        self.b.drain_events.return_value=[dict(reason='ai_review_complete',candidate_id=c['id'],generation='g',
            completed_ms=self.now,decision=dict(action='hold',thesis='wait'),snapshot_id=c['snapshot_id'])]
        self.c.tick();self.b.drain_events.return_value=[]
        self.now+=60000;self.mono=60;self.c.tick()
        self.assertEqual(self.b.offer.call_count,2)
        self.assertEqual(self.b.offer.call_args.args[0]['decision_source'],'gemini_event')
        self.assertEqual(self.memory.context('X',None)['thesis'],'wait')
    def test_paused_flat_no_call_but_risk_runs(self):
        self.paused=dict(reason='entry_paused');self.c.tick()
        self.b.offer.assert_not_called();self.ctrl.risk_tick.assert_called_once()
    def test_busy_does_not_reserve_another_call(self):
        self.b.busy.return_value=True;self.c.tick()
        self.b.offer.assert_not_called();self.assertEqual(self.memory.counts('X',self.now)['request_count_1h'],0)
    def test_consumed_waiting_event_cannot_block_fallback_forever(self):
        self.mono=60;self.now+=60000;self.c.tick();first=self.b.offer.call_args.args[0]
        self.memory.fail(first['id'],'timeout',self.now+1)
        self.memory.invalidate('X','g')
        self.c.event_scheduler.invalidate('g')
        # Same event after restart must not become a permanently blocked queue.
        self.c.tick()
        self.now+=300001;self.mono+=301
        self.c.tick()
        self.assertGreater(self.b.offer.call_count,1)
    def test_paused_held_still_reviews(self):
        self.paused=dict(reason='entry_paused')
        self.ctrl.status.return_value=dict(owner='unified',state=dict(position_id='p',side='long',phase='PROBE',filled_qty=1))
        self.c.tick();self.b.offer.assert_called_once()

    def test_data_outage_abandons_pending_request_and_recovers(self):
        self.c.tick()
        first=self.b.offer.call_args.args[0]
        self.feed.snapshot.side_effect=lambda:None
        self.c.tick()
        self.assertEqual(self.s.db.execute('SELECT status FROM ai_requests WHERE id=?',(first['id'],)).fetchone()[0],'abandoned')
        self.feed.snapshot.side_effect=self.market
        self.now+=60000;self.mono=60
        self.c.tick()
        self.assertEqual(self.b.offer.call_count,2)
