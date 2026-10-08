import unittest
from threading import Event
from core_unified_review import ReviewBroker


def candidate(id='a',symbol='X',generation=1):
    return dict(id=id,symbol=symbol,side='long',generation=generation,
                signal_ms=1000,expires_ms=61000,snapshot_id='s')


class ReviewTests(unittest.TestCase):
    def test_offer_nonblocking_duplicate_and_pause_invalidation(self):
        entered, release = Event(), Event()
        def gemini(c,s):
            entered.set()
            if not release.wait(2): raise TimeoutError()
            return dict(action='long',confidence=.8)
        b = ReviewBroker(gemini,lambda c,s:dict(decision='approve_now',confidence=.8),lambda:2000)
        try:
            self.assertTrue(b.offer(candidate(),{}))
            self.assertTrue(entered.wait(1))
            self.assertFalse(b.offer(candidate(),{}))
            b.invalidate('X')
        finally:
            release.set()
            b.close()
        self.assertEqual(b.drain(),[])

    def test_valid_result_and_timeout_are_distinguished(self):
        b = ReviewBroker(lambda c,s:dict(action='long',confidence=.8),
                         lambda c,s:dict(decision='approve_now',confidence=.8),lambda:2000)
        self.assertTrue(b.offer(candidate(),{}))
        b.close()
        result = b.drain()
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]['gpt_decision'],'approve_now')
        self.assertEqual(result[0]['candidate_id'],'a')

    def test_failed_gemini_does_not_reach_gpt(self):
        def forbidden(c,s): raise AssertionError('GPT must not be called')
        b = ReviewBroker(lambda c,s:dict(action='hold',confidence=.8),forbidden,lambda:2000)
        b.offer(candidate(),{})
        b.close()
        self.assertEqual(b.drain(),[])

    def test_expired_and_api_error_drop_result(self):
        def fail(c,s): raise TimeoutError('offline')
        for gpt, clock in ((fail,lambda:2000),
                           (lambda c,s:dict(decision='approve_now',confidence=.8),lambda:62000)):
            b = ReviewBroker(lambda c,s:dict(action='long',confidence=.8),gpt,clock)
            b.offer(candidate(),{})
            b.close()
            self.assertEqual(b.drain(),[])


if __name__ == '__main__': unittest.main()
