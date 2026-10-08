import unittest
from copy import deepcopy
from threading import Event
from core_unified_runtime import OwnerRegistry, Runtime
from core_unified_review import ReviewBroker
from test_market import snapshot


class Clock:
    def __init__(self): self.mono,self.ms = 0,3600000
    def monotonic(self): return self.mono
    def utc_ms(self): return self.ms
    def advance(self,seconds): self.mono+=seconds; self.ms+=seconds*1000


class Market:
    """Read-only cached inputs, not a synchronous exchange fetch."""
    def __init__(self,clock):
        self.clock,self.paused,self.risks = clock,False,[]
        self.reads = 0
        self.cached = None
    def controls(self): return {'paused':self.paused,'generation':1}
    def symbols(self): return ['X']
    def snapshot(self,symbol):
        self.reads += 1
        s = deepcopy(self.cached) if self.cached is not None else snapshot()
        s.update(now_ms=self.clock.ms,symbol='X')
        return s
    def risk_tick(self): self.risks.append(self.clock.mono)
    def context_tick(self): pass


class Forbidden:
    def __getattr__(self,name):
        raise AssertionError('SHADOW accessed production dependency: '+name)


class RuntimeTests(unittest.TestCase):
    def test_exactly_one_owner_and_wrong_release_is_rejected(self):
        r = OwnerRegistry()
        self.assertEqual(r.owner('X'),'legacy')
        self.assertTrue(r.claim('X','unified'))
        self.assertFalse(r.claim('X','legacy'))
        self.assertFalse(r.release('X','legacy'))
        self.assertEqual(r.owner('X'),'unified')
        self.assertTrue(r.release('X','unified'))
        self.assertEqual(r.owner('X'),'legacy')

    def test_ai_stall_does_not_block_risk_and_shadow_has_no_writes(self):
        entered,release = Event(),Event()
        clock = Clock()
        def gemini(c,s):
            entered.set()
            if not release.wait(2): raise TimeoutError()
            return dict(action=c['side'],confidence=.8)
        broker = ReviewBroker(gemini,lambda c,s:dict(decision='approve_now',confidence=.8),clock.utc_ms)
        market = Market(clock)
        runtime = Runtime(market,broker,Forbidden(),Forbidden(),clock,mode='SHADOW')
        try:
            runtime.tick()
            self.assertTrue(entered.wait(1))
            clock.advance(20)
            runtime.tick()
            clock.advance(10)
            runtime.tick()
            self.assertEqual(market.risks,[0,30])
            self.assertEqual(market.reads,1)
            market.paused = True
            runtime.tick()
            release.set()
        finally:
            release.set()
            runtime.stop()
        self.assertEqual(broker.drain(),[])

    def test_no_source_work_in_off_mode(self):
        r = Runtime(Forbidden(),Forbidden(),Forbidden(),Forbidden(),Clock())
        self.assertEqual(r.tick()['mode'],'OFF')

    def test_wall_clock_rollback_invalidates_candidates(self):
        clock,market = Clock(),None
        market = Market(clock)
        broker = ReviewBroker(lambda c,s:dict(action='hold'),lambda c,s:{},clock.utc_ms)
        r = Runtime(market,broker,Forbidden(),Forbidden(),clock,mode='SHADOW')
        try:
            r.tick()
            clock.ms -= 60000
            self.assertEqual(r.tick()['reason'],'clock_regression')
        finally: r.stop()

    def test_unwired_live_mode_is_explicitly_blocked(self):
        # Never let a bare kernel imply safe production routing or live readiness.
        with self.assertRaises(ValueError):
            Runtime(Forbidden(),Forbidden(),Forbidden(),Forbidden(),Clock(),mode='LIVE')

    def test_clock_rollback_never_stops_monotonic_risk_tick(self):
        clock = Clock()
        market = Market(clock)
        broker = ReviewBroker(lambda c,s:dict(action='hold'),lambda c,s:{},clock.utc_ms)
        r = Runtime(market,broker,Forbidden(),Forbidden(),clock,mode='SHADOW')
        try:
            r.tick()
            clock.mono,clock.ms = 30,3500000
            r.tick()
            clock.mono,clock.ms = 60,3530000
            r.tick()
            self.assertEqual(market.risks,[0,30,60])
        finally: r.stop()

    def test_late_cache_refresh_is_not_lost_for_whole_minute(self):
        entered = Event()
        clock = Clock()
        market = Market(clock)
        old = snapshot()
        for rows in old['frames'].values():
            for row in rows:
                row['open_ms'] -= 300000
                row['close_ms'] -= 300000
        market.cached = old
        def gemini(c,s): entered.set(); return dict(action='hold')
        broker = ReviewBroker(gemini,lambda c,s:{},clock.utc_ms)
        r = Runtime(market,broker,Forbidden(),Forbidden(),clock,mode='SHADOW')
        try:
            r.tick()
            self.assertFalse(entered.is_set())
            market.cached = snapshot()
            clock.advance(2)
            r.tick()
            self.assertTrue(entered.wait(1))
        finally: r.stop()


if __name__ == '__main__': unittest.main()
