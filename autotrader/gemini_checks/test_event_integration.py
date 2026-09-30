"""Actual coordinator, asynchronous broker, durable memory, controller and fills; offline I/O only."""
import unittest,time
from copy import deepcopy
from types import SimpleNamespace as NS
from unittest.mock import patch
from decimal import Decimal as D
import test_live as fixture
from test_market import snapshot
from test_event_review import decision
from core_unified_review import ReviewBroker
from core_unified_service import Coordinator
from core_unified_event_memory import EventMemory
from core_unified_live import LiveController

class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.ControllerTests();self.f.setUp();self.addCleanup(self.f.tearDown)
        f=self.f;self.now=3600000;self.mono=0;self.price=100;self.next_action='long';self.fraction=.25;self.logs=[]
        f.cfg.CORE_EVENT_AI_ENABLED=True;f.cfg.GPT_ENTRY_GATE_ENABLED=False;f.cfg.MIN_HOLD_MINUTES=15
        f.cfg.POLL_INTERVAL_SECONDS=300;f.cfg.POSITION_AI_REVIEW_ENABLED=True;f.cfg.POSITION_AI_LIVE_EXECUTE=True
        f.c.clock=lambda:self.now;f.c.port.clock=lambda:self.now;f.c.manager.clock=lambda:self.now;f.c.handoff.clock=lambda:self.now
        f.client.fetch_last_price=lambda:self.price
        f.ex.fetch_ticker=lambda s:dict(bid=self.price,ask=self.price,timestamp=self.now)
        f.risk.sl_tp_prices=lambda cfg,side,p:(p-2 if side=='long' else p+2,p+4 if side=='long' else p-4)
        f.c.entry_guard=LiveController.entry_guard.__get__(f.c)
        f.client.cancel_protection=lambda ids:f.ex.stops.clear()
        def attach(side,qty,stop,tp,*,algo_client_order_id):
            f.ex.stops.append(dict(algoId='stop'+str(self.now),algoClOrdId=algo_client_order_id,instId='X-USDT-SWAP',
                side='sell' if side=='long' else 'buy',posSide='net',state='live',reduceOnly='true',slTriggerPx=str(stop),sz=str(qty)))
        f.client.attach_protection=attach
        def create(symbol,kind,side,qty,price,params):
            f.ex.calls.append(params);cid=params['clOrdId'];oid='order'+cid
            f.ex.orders[cid]=dict(id=oid,clientOrderId=cid,symbol='X',status='closed',filled=qty,remaining=0)
            f.ex.trades[oid]=[dict(id='trade'+cid,order=oid,symbol='X',amount=qty,price=self.price,timestamp=self.now)]
            old=f.ex.pos;oldqty=old['contracts'] if old else 0
            avg=(oldqty*old['entryPrice']+qty*self.price)/(oldqty+qty) if old else self.price
            f.ex.pos=dict(symbol='X',contracts=oldqty+qty,side='long' if side=='buy' else 'short',entryPrice=avg,markPrice=self.price,
                info=old['info'] if old else dict(posId='pos'+str(self.now),cTime=str(self.now),posSide='net'))
        f.ex.create_order=create
        def reduce(pos,qty,*,client_order_id):
            oid='reduce'+client_order_id
            f.ex.orders[client_order_id]=dict(id=oid,clientOrderId=client_order_id,symbol='X',status='closed',filled=qty,remaining=0)
            f.ex.trades[oid]=[dict(id='trade'+oid,order=oid,symbol='X',amount=qty,price=self.price,timestamp=self.now)]
            remaining=f.ex.pos['contracts']-qty
            if remaining:f.ex.pos['contracts']=remaining
            else:f.ex.pos=None
        f.client.reduce_position=reduce
        self.modules={'risk_manager':f.risk,'core_kill_switch':NS(is_active=lambda d:False),
            'symbol_entry_control':NS(is_paused=lambda d,s:False),
            'core_manual_close':NS(get=lambda d,s:None,block_reason=lambda *a,**k:None)}
        self.enterContext(patch.dict('sys.modules',self.modules))
        self.enterContext(patch('core_unified_accounting.allow_new_entry',return_value=True))
        f.c.ready()
        self.memory=EventMemory(f.store)
        self.b=ReviewBroker(lambda c,s:decision(self.next_action,self.fraction),lambda c,s:self.fail('GPT'),lambda:self.now,require_gpt=lambda:False)
        self.addCleanup(self.b.close)
        self.c=Coordinator('X',NS(snapshot=self.market),self.b,f.c,lambda:self.now,lambda:self.mono,lambda:('g',False),
            mode=lambda c:'gemini',review_interval=lambda:300,event_mode=lambda:True,event_memory=self.memory,
            notify=lambda **e:self.logs.append(e))
    def market(self):
        s=snapshot();s.update(symbol='X',now_ms=self.now)
        for rows in s['frames'].values():
            for r in rows:
                r['open_ms']+=self.now-3600000;r['close_ms']+=self.now-3600000;r.update(close=100,ema50=99)
        s['frames']['1m'][-1]['close']=self.price
        s['frames']['1h']=deepcopy(s['frames']['5m']);s['frames']['4h']=deepcopy(s['frames']['5m']);s['frames']['1d']=deepcopy(s['frames']['5m'])
        return s
    def step(self,action,price,seconds=60,fraction=None):
        self.now+=seconds*1000;self.mono+=seconds;self.next_action=action;self.price=price
        if fraction is not None:self.fraction=fraction
        if self.f.ex.pos:self.f.ex.pos['markPrice']=price
        self.c.tick()
        deadline=time.monotonic()+2
        while self.b.busy('X') and time.monotonic()<deadline:time.sleep(.005)
        self.c.tick()
    def test_event_entry_hold_close_and_post_exit_fresh_reentry(self):
        self.step('long',100,seconds=0)
        st=self.f.store.state('X');self.assertEqual(D(st['filled_qty']),23,self.logs)
        self.assertEqual(st['entry_thesis'],'상승 지속')
        self.step('hold',100.7)
        self.assertEqual(D(self.f.store.state('X')['filled_qty']),23)
        self.step('close',100,seconds=300)
        self.assertEqual(self.f.store.state('X')['phase'],'FLAT',self.logs)
        # Independent new downward event after exit; full risk-sized target.
        self.step('short',99,seconds=60,fraction=1)
        st=self.f.store.state('X');self.assertEqual(st['side'],'short',self.logs)
        self.assertEqual(D(st['filled_qty']),D(st['target_qty']),self.logs)
        self.assertTrue(self.f.c.port.protection('X')['confirmed'])
        self.assertEqual(len(self.f.ex.calls),2)
    def test_early_reduce_is_allowed_and_recorded(self):
        self.step('long',100,seconds=0)
        self.step('reduce',100.7,seconds=60)
        self.assertLess(D(self.f.store.state('X')['filled_qty']),23,self.logs)
        self.assertTrue(any(r['kind']=='REDUCE' for r in self.f.store.accounting_records()))
        self.assertTrue(self.f.c.port.protection('X')['confirmed'])
    def test_shadow_event_reviews_cannot_change_exchange(self):
        from core_unified_service import ShadowController
        self.c.controller=ShadowController()
        self.step('long',100,seconds=0)
        self.assertIsNotNone(self.c.controller.last_review)
        self.assertEqual(self.f.ex.calls,[])
        self.assertIsNone(self.f.store.state('X'))
    def test_full_initial_position_cannot_add(self):
        self.step('long',100,seconds=0,fraction=1)
        qty=D(self.f.store.state('X')['filled_qty'])
        self.step('add',100.7,seconds=60)
        self.assertEqual(D(self.f.store.state('X')['filled_qty']),qty)
        self.assertEqual(len(self.f.ex.calls),1)
    def test_repeat_tick_does_not_call_or_order_again(self):
        self.step('long',100,seconds=0)
        for _ in range(10):self.c.tick()
        self.assertEqual(self.memory.counts('X',self.now)['request_count_1h'],1)
        self.assertEqual(len(self.f.ex.calls),1)
