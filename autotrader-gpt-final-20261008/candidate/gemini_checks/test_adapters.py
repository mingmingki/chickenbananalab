import json
import sys
import time
import unittest
from copy import deepcopy
from datetime import timedelta
from threading import Event
from types import SimpleNamespace as NS
from unittest.mock import patch

from core_unified_adapters import MarketFeed, normalize_frame, make_broker, review_prompt


def indicators(df):
    for key, value in [('ema_20',100),('ema_50',100),('macd',1),('macd_signal',.5),('rsi_14',55),('atr_14',2)]:
        df[key] = value
    return df


def closed(t,tf,now):
    widths = {'1m':60,'3m':180,'5m':300,'1h':3600,'4h':14400,'1d':86400}
    return t+timedelta(seconds=widths[tf]+3) <= now


class AdapterTests(unittest.TestCase):
    def frame(self):
        import pandas as pd
        return pd.DataFrame([[i*60000,100,102,98,101,5] for i in range(100)],
                            columns=['timestamp','open','high','low','close','volume'])

    def norm(self,df,now=6003000):
        return normalize_frame(df,'1m',now,indicator_fn=indicators,closed_fn=closed)

    def test_future_rows_never_become_confirmed(self):
        rows = self.norm(self.frame(), 5703000)
        self.assertEqual(rows[-1]['close_ms'],5700000)
        rows2 = self.norm(self.frame(), 5763000)
        self.assertEqual(rows2[-1]['close_ms'],5760000)

    def test_duplicates_conflicts_gaps_and_nonfinite(self):
        import pandas as pd
        df = self.frame()
        self.assertEqual(self.norm(pd.concat([df,df.tail(1)])),self.norm(df))
        other = df.tail(1).copy(); other['close']=102
        with self.assertRaisesRegex(ValueError,'conflicting_duplicate'):
            self.norm(pd.concat([df,other]))
        with self.assertRaisesRegex(ValueError,'gap'):
            self.norm(df.drop(80))
        df.loc[80,'volume'] = float('nan')
        with self.assertRaises(ValueError): self.norm(df)

    def test_canonical_finality_and_native_indicators_when_available(self):
        try:
            import indicators as native
            import candle_finality
        except ImportError:
            self.skipTest('production ta/candle modules not installed locally')
        rows=normalize_frame(self.frame(),'1m',6003000)
        self.assertEqual(rows[-1]['close_ms'],6000000)
        self.assertTrue(rows[-1]['atr14']>0)

    def test_construct_does_not_create_client(self):
        factory = lambda *args: self.fail('unexpected client')
        feed = MarketFeed(NS(user_dir='test'), 'BTC', lambda:6003000, client_factory=factory)
        self.assertIsNone(feed.snapshot())
        feed.close()

    def test_collector_owns_client_and_snapshot_is_cached(self):
        import threading
        from core_unified_market import WIDTH
        clock=[8640000000+3000]
        calls=[]; ready=Event(); closed_client=Event()
        owner=[]
        class Exchange:
            def fetch_ohlcv(self,symbol,timeframe,limit):
                calls.append((threading.get_ident(),timeframe))
                if timeframe=='1d': ready.set()
                return []
            def close(self):
                owner.append(threading.get_ident()); closed_client.set()
        exchange=Exchange()
        def factory(*args):
            owner.append(threading.get_ident()); return NS(exchange=exchange)
        def normalizer(df,tf,now):
            end=((now-3000)//WIDTH[tf])*WIDTH[tf]
            return [dict(open_ms=end-(30-i)*WIDTH[tf],close_ms=end-(29-i)*WIDTH[tf],
                         close=100,ema20=100,macd=1,volume=5,atr14=2) for i in range(30)]
        feed=MarketFeed(NS(user_dir='test'),'BTC',lambda:clock[0],client_factory=factory,normalizer=normalizer)
        try:
            feed.start(Event()); self.assertTrue(ready.wait(1))
            for _ in range(100):
                snap=feed.snapshot()
                if snap: break
                Event().wait(.005)
            self.assertIsNotNone(snap)
            self.assertEqual(exchange.timeout,3000)
            self.assertEqual(exchange.maxRetriesOnFailure,0)
            self.assertEqual(len(calls),6)
            self.assertNotEqual(calls[0][0],threading.get_ident())
            feed.generation=7
            self.assertEqual(feed.snapshot()['generation'],7)
            count=len(calls)
            for _ in range(50): feed.snapshot()
            self.assertEqual(len(calls),count)
            clock[0]+=66000
            self.assertIsNone(feed.snapshot())
        finally: feed.close()
        self.assertTrue(closed_client.wait(1))
        self.assertEqual(owner[0],owner[-1])

    def test_sdk_options_cleanup_and_purpose(self):
        captured=[]
        def gem_factory(**kwargs):
            captured.append(('gemini',kwargs))
            return NS(models=NS(generate_content=lambda **kw: NS(text=json.dumps({'action':'long','confidence':.8}))),
                      close=lambda:captured.append('gemini_closed'))
        def gpt_factory(**kwargs):
            captured.append(('gpt',kwargs))
            return NS(chat=NS(completions=NS(create=lambda **kw:NS(choices=[NS(message=NS(content=json.dumps({'decision':'approve_now','confidence':.8})))]))),close=lambda:captured.append('gpt_closed'))
        types = NS(HttpOptions=lambda **kw:NS(**kw), HttpRetryOptions=lambda **kw:NS(**kw),
                   GenerateContentConfig=lambda **kw:NS(**kw), ThinkingConfig=lambda **kw:NS(**kw))
        cfg = NS(GEMINI_API_KEY='fake', OPENAI_API_KEY='fake',GEMINI_MODEL='gemini-test',OPENAI_MODEL='gpt-test')
        c=dict(id='a',side='long',symbol='BTC',signal_ms=100,expires_ms=10000,generation=0,snapshot_id='s')
        s=dict(symbol='BTC',now_ms=100,generation=0,frames={})
        with patch.dict(sys.modules, {'google.genai':NS(types=types)}):
            broker=make_broker(cfg,lambda:100,gemini_factory=gem_factory,gpt_factory=gpt_factory)
            broker.gemini(c,s); broker.gpt(c,s); broker.close()
        self.assertEqual(captured[0][1]['http_options'].timeout,15000)
        self.assertEqual(captured[0][1]['http_options'].retry_options.attempts,1)
        self.assertEqual(captured[2][1]['timeout'],15)
        self.assertEqual(captured[2][1]['max_retries'],0)
        self.assertIn('gemini_closed',captured); self.assertIn('gpt_closed',captured)
        for purpose in ('ENTRY','ADD','REVERSE'):
            self.assertIn('"purpose": "'+purpose+'"',review_prompt(dict(c,purpose=purpose),s,'gemini'))

    def test_slow_ai_nonblocking_and_snapshot_isolation(self):
        from core_unified_review import ReviewBroker
        entered, release, done = Event(), Event(), Event()
        observed=[]
        def gem(c,s):
            entered.set(); release.wait(2); s['frames']['x']=2
            return dict(action='long',confidence=.8)
        def gpt(c,s):
            observed.append(s['frames']['x']); done.set()
            return dict(decision='approve_now',confidence=.8)
        broker=ReviewBroker(gem,gpt,lambda:100)
        c=dict(id='a',side='long',symbol='BTC',signal_ms=100,expires_ms=10000,generation=0,snapshot_id='s')
        s=dict(frames={'x':1})
        try:
            start=time.monotonic(); self.assertTrue(broker.offer(c,s)); self.assertLess(time.monotonic()-start,.2)
            self.assertTrue(entered.wait(1)); self.assertEqual(broker.drain(),[])
            release.set(); self.assertTrue(done.wait(1)); self.assertEqual(observed,[1])
        finally:
            release.set(); broker.close()

if __name__ == '__main__': unittest.main()
