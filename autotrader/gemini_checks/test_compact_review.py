"""Protect prompt cost, management context, and the saved review clock."""
import json
import tempfile
import unittest
from copy import deepcopy
from types import SimpleNamespace as NS

from core_unified_adapters import review_prompt
from core_unified_service import Coordinator
from test_market import snapshot


def context(prompt):
    return json.loads(prompt[prompt.index('{'):])


def full_snapshot():
    frames = {}
    for tf, width in [('1m',60000),('3m',180000),('5m',300000),
                      ('1h',3600000),('4h',14400000),('1d',86400000)]:
        frames[tf] = [dict(open_ms=i*width, close_ms=(i+1)*width,
            close=100+i, ema20=120, ema50=115, macd=i/100,
            macd_signal=.30, rsi14=32+i, atr14=1.29,
            volume=20 if i==29 else 10) for i in range(30)]
    return dict(symbol='BTC',now_ms=30*86400000,generation=2,frames=frames,
                position=None,account='PRIVATE_ACCOUNT')


class CompactPromptTests(unittest.TestCase):
    def test_six_timeframes_fit_budget_and_keep_trend_and_reversal_evidence(self):
        s=full_snapshot(); before=deepcopy(s)
        prompt=review_prompt({'purpose':'DECIDE','side':'undecided'},s,'gemini')
        self.assertLess(len(prompt),9000)
        data=context(prompt)
        self.assertEqual(s,before)  # Trading guards retain every original candle.
        self.assertNotIn('frames',data)
        self.assertNotIn('PRIVATE_ACCOUNT',prompt)
        self.assertEqual(set(data['market']),{'1m','3m','5m','1h','4h','1d'})
        m=data['market']['1m']
        self.assertEqual(m['close'],129)
        self.assertEqual(m['ema20'],120)
        self.assertEqual(m['ema50'],115)
        self.assertEqual(m['rsi'],61)
        self.assertEqual(m['rsi_change5'],5)
        self.assertEqual(m['macd_last3'],[.27,.28,.29])
        self.assertEqual(m['macd_signal'],.30)
        self.assertAlmostEqual(m['change20_pct'],17.272727,places=5)
        self.assertEqual(m['close_range20'],[110,129])
        self.assertEqual(m['atr_pct'],1)
        self.assertEqual(m['volume_ratio20'],2)
        self.assertEqual(m['closed_ms'],1800000)
        self.assertIsNone(data['position'])

    def test_management_context_is_bounded_and_uses_initial_entry_labels(self):
        for side,price,best,move,giveback in [('long',98,110,-2,6),('short',102,90,-2,6)]:
            with self.subTest(side=side):
                s=full_snapshot(); s['frames']['1m'][-1]['close']=price
                s['position']=dict(side=side,phase='ACTIVE',filled_qty='25',target_qty='100',
                    initial_entry='100',initial_r='2',stop_price='97' if side=='long' else '103',
                    mfe_price=str(best),reduce_stage=1,reduce_partial_qty='0',
                    protect_base_qty='50',protection_armed=True,opened_ms=s['now_ms']-600000,
                    executed_trades=[['PRIVATE_ORDER','PRIVATE_FILL']]*1000,
                    owned_stop_ids=['PRIVATE_STOP'],exchange_identity='PRIVATE_ID')
                prompt=review_prompt({'purpose':'DECIDE'},s,'gemini')
                self.assertLess(len(prompt),10000)
                p=context(prompt)['position']
                self.assertEqual(p['side'],side)
                self.assertEqual(p['filled_qty'],25)
                self.assertEqual(p['initial_entry'],100)
                self.assertEqual(p['stop_price'],97 if side=='long' else 103)
                self.assertEqual(p['held_minutes'],10)
                self.assertEqual(p['move_from_initial_pct'],move)
                self.assertEqual(p['giveback_r'],giveback)
                self.assertEqual(p['reduce_stage'],1)
                self.assertEqual(p['protect_base_qty'],50)
                self.assertNotIn('PRIVATE_',prompt)

    def test_small_coin_precision_zero_volume_and_missing_context_are_honest(self):
        s=full_snapshot()
        for r in s['frames']['1m']:
            r.update(close=.085751234,ema20=.085752345,ema50=.085701234,
                     macd=-.00000001234,volume=0)
        p=context(review_prompt({'purpose':'DECIDE'},s,'gemini'))
        self.assertIn('market',p)
        m=p['market']['1m']
        self.assertAlmostEqual(m['close'],.085751234,places=9)
        self.assertLess(m['macd_last3'][-1],0)
        self.assertIsNone(m['volume_ratio20'])
        s['position']={'side':'long','phase':'ADOPT_RESTRICTED','filled_qty':'2'}
        p=context(review_prompt({'purpose':'DECIDE'},s,'gemini'))['position']
        self.assertNotIn('move_from_initial_pct',p)
        self.assertNotIn('held_minutes',p)


class SettingsClockTests(unittest.TestCase):
    def test_identical_key_rechecks_keep_clock_but_real_setting_change_reviews_now(self):
        from config import UserConfig
        with tempfile.TemporaryDirectory() as d:
            cfg=UserConfig(d)
            cfg.save_env({'POLL_INTERVAL_SECONDS':'300','GEMINI_API_KEY':'test-gemini',
                          'OPENAI_API_KEY':'test-openai','OKX_API_KEY':'test-okx'})
            now=[3600000];mono=[0];offers=[]
            def market():
                s=snapshot();s['now_ms']=now[0]
                for rows in s['frames'].values():
                    for row in rows:
                        row['open_ms']+=now[0]-3600000;row['close_ms']+=now[0]-3600000
                return s
            feed=NS(snapshot=market)
            broker=NS(invalidate=lambda s:None,drain_events=lambda:[],drain=lambda:[],
                      offer=lambda c,s:offers.append(c) or True)
            controller=NS(status=lambda:dict(owner='unified',state=None),risk_tick=lambda s:None)
            c=Coordinator('X',feed,broker,controller,lambda:now[0],lambda:mono[0],
                lambda:(cfg.settings_revision,False),mode=lambda c:'gemini',review_interval=lambda:cfg.POLL_INTERVAL_SECONDS)
            c.tick();self.assertEqual(len(offers),1)
            for key in ('GEMINI_API_KEY','OPENAI_API_KEY','OKX_API_KEY'):
                mono[0]+=1;now[0]+=1000
                cfg.save_env({key:getattr(cfg,key)});c.tick()
            cfg.reload();c.tick()
            self.assertEqual(len(offers),1)
            mono[0]=300;now[0]=3900000;c.tick()
            self.assertEqual(len(offers),2)
            cfg.save_env({'MIN_CONFIDENCE':'0.75'});c.tick()
            self.assertEqual(len(offers),3)
            self.assertEqual(cfg.MIN_CONFIDENCE,.75)


if __name__=='__main__':unittest.main()
