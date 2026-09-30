import unittest
from core_unified_live import trend_flags

class LiveTests(unittest.TestCase):
    def test_directional_weakness(self):
        rows=[dict(close=99,ema20=100,macd=m) for m in [3,2,1]]
        snap={'frames':{'5m':rows,'1h':rows}}
        self.assertTrue(trend_flags(snap,'long')['weak5'])
        self.assertFalse(trend_flags(snap,'short')['weak5'])

    def test_missing_frames_fail_closed(self):
        with self.assertRaises((KeyError,ValueError)):
            trend_flags({'frames':{}},'long')

from types import SimpleNamespace
from threading import Event, RLock
from decimal import Decimal as D
from unittest.mock import patch
from core_unified_live import LiveController
from core_unified_store import Store
from core_unified_policy import POLICY_VERSION

class Exchange:
    def __init__(self): self.pos=None; self.orders={}; self.trades={}; self.stops=[]; self.calls=[]
    def fetch_positions(self,symbols): return [self.pos] if self.pos else []
    def fetch_open_orders(self,symbol): return []
    def fetch_ticker(self,symbol): return dict(bid=100,ask=100,timestamp=1000000)
    def private_get_account_config(self): return {'code':'0','data':[{'posMode':'net_mode'}]}
    def market(self,symbol): return {'id':'X-USDT-SWAP'}
    def price_to_precision(self,symbol,price): return str(price)
    def create_order(self,symbol,kind,side,qty,price,params):
        self.calls.append(params)
        identifier=params['clOrdId']; order_id='order'+identifier
        self.orders[identifier]=dict(id=order_id,clientOrderId=identifier,symbol=symbol,status='closed',filled=qty,remaining=0)
        self.trades[order_id]=[dict(id='trade'+identifier,order=order_id,symbol=symbol,amount=qty,price=100)]
        self.pos=dict(symbol=symbol,contracts=qty,side='long' if side=='buy' else 'short',entryPrice=100,markPrice=100,
                      info={'posId':'real-position','cTime':'1000000','posSide':'net'})
    def fetch_order(self,identifier,symbol,params): return self.orders.get(params['clOrdId'])
    def fetch_my_trades(self,symbol,since,limit,params): return self.trades[params['ordId']]

class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.store=Store(':memory:'); self.ex=Exchange(); self.lock=RLock()
        self.client=SimpleNamespace(symbol='X',exchange=self.ex,fetch_pending_protection_orders=lambda:self.ex.stops,
            instrument_metadata=lambda:dict(contract_size=1,lot_step=1,tick_size='.01',min_contracts=1,max_contracts=1000),
            ensure_leverage=lambda:None,fetch_last_price=lambda:100,fetch_usdt_equity=lambda:1000,attach_protection=self.attach)
        self.cfg=SimpleNamespace(CORE_UNIFIED_MODE='LIVE',EXECUTION_MODE='LIVE',user_dir='/unused',ENABLED_SYMBOLS=['X'],CANDIDATE_C_SYMBOLS=[])
        self.c=LiveController(self.cfg,None,self.client,'X',SimpleNamespace(allow_new_entry=lambda equity:True),
                              self.store,lambda:1000000,Event())
        self.c.port.lock_factory=lambda:self.lock
        self.c.port._record=lambda filename,symbol:None
        self.c.entry_guard=lambda candidate:None
        self.snapshot={'generation':1,'frames':{'1m':[dict(close=99,ema20=100,macd=0),dict(close=101,ema20=100,macd=1)]}}
        self.candidate=dict(id='candidate1',version=POLICY_VERSION,symbol='X',side='long',signal_ms=999000,
            expires_ms=1059000,snapshot_id='snapshot1',generation=1,reference_price=100,atr5=10)
        self.approval=dict(candidate_id='candidate1',snapshot_id='snapshot1',generation=1,gemini_action='long',
            gemini_confidence=.8,gpt_decision='approve_now',gpt_confidence=.8,completed_ms=999100)
        self.risk=SimpleNamespace(calculate_position_size=lambda cfg,equity,price:100,sl_tp_prices=lambda cfg,side,price:(98,104))
    def tearDown(self): self.store.close()
    def attach(self,side,qty,stop,tp,client_order_id):
        self.ex.stops.append(dict(algoId='stop1',algoClOrdId=client_order_id,instId='X-USDT-SWAP',
            side='sell',posSide='net',state='live',reduceOnly='true',slTriggerPx=str(stop),sz=str(qty)))
    def run_entry(self):
        with patch.dict('sys.modules',{'risk_manager':self.risk}), patch('core_unified_live.validate_snapshot',return_value=(True,'ok')):
            return self.c.entry(self.candidate,self.approval,self.snapshot)
    def test_entry25_bound_actual_identity_and_protected(self):
        self.assertEqual(self.run_entry()['status'],'complete')
        state=self.store.state('X')
        self.assertEqual(float(state['filled_qty']),25)
        self.assertEqual(float(state['target_qty']),100)
        self.assertEqual(float(state['entry_risk_used']),52.5)
        self.assertEqual(state['exchange_identity'],'real-position:1000000:long')
        self.assertNotEqual(state['position_id'],state['exchange_identity'])
        self.assertEqual(state['owned_stop_ids'],['stop1'])
        self.assertEqual(len(self.ex.calls),1)
    def test_held_legacy_not_claimed(self):
        self.ex.pos=dict(symbol='X',contracts=4)
        self.assertFalse(self.c.ready())
        self.assertEqual(self.store.owner_record('X')['owner'],'legacy')
        self.assertEqual(self.ex.calls,[])
    def test_off_is_inert(self):
        self.cfg.CORE_UNIFIED_MODE='OFF'
        self.assertEqual(self.run_entry()['status'],'blocked')
        self.c.close()
        self.assertEqual(self.ex.calls,[])
    def test_duplicate_does_not_resubmit(self):
        self.run_entry()
        before=self.store.state('X')
        self.run_entry()
        self.assertEqual(self.store.state('X'),before)
        self.assertEqual(len(self.ex.calls),1)

    def test_global_off_is_inert(self):
        self.cfg.EXECUTION_MODE='OFF'
        self.assertEqual(self.run_entry()['status'],'blocked')
        self.assertEqual(self.ex.calls,[])

    def test_external_stop_exact_fill_proof_releases_flat(self):
        self.assertEqual(self.run_entry()['status'],'complete')
        self.ex.pos=None
        self.ex.private_get_trade_order_algo=lambda params:dict(code='0',data=[dict(algoId='stop1',state='effective',ordId='exit1')])
        self.ex.fetch_order=lambda identifier,symbol,**kw:dict(id='exit1',symbol='X',side='sell',status='closed',filled=25,remaining=0)
        self.ex.fetch_my_trades=lambda *a,**kw:[dict(id='exittrade',order='exit1',symbol='X',amount=25,price=98)]
        self.client.cancel_protection=lambda ids:self.ex.stops.clear()
        with self.lock:
            self.assertEqual(self.c._external_flat()['status'],'complete')
        self.assertEqual(self.store.state('X')['phase'],'FLAT')
        self.assertEqual(len(self.store.state('X')['external_close_evidence']),1)

    def test_external_unknown_cannot_release_flat(self):
        self.run_entry(); self.ex.pos=None
        self.ex.private_get_trade_order_algo=lambda params:dict(code='0',data=[dict(algoId='stop1',state='effective')])
        with self.lock:
            self.assertEqual(self.c._external_flat()['status'],'reconciling')
        self.assertEqual(float(self.store.state('X')['filled_qty']),25)

class TerminalPartialTests(unittest.TestCase):
    def run_case(self,kind,initial,filled):
        import sys
        from pathlib import Path
        sys.path.insert(0,str(Path(__file__).parent))
        from core_unified_fakes import FakePort
        from core_unified_execution import ExecutionManager
        store=Store(':memory:'); self.addCleanup(store.close)
        store.save_state('X',dict(position_id='p',side='long',filled_qty=str(initial),phase='PROBE',reduce_stage=0),None)
        port=FakePort(store)
        if initial: port.pos=dict(position_id='p',side='long',contracts=str(initial))
        qty=4
        intent=dict(id='terminal',position_id='p',side='long',kind=kind,qty=str(qty),expected_revision=0,bar_ms=1000,stage=1)
        store.reserve('X',intent,0)
        port.pos=dict(position_id='p',side='long',contracts=str(initial+filled if kind=='ENTRY' else initial-filled)) if (initial+filled if kind=='ENTRY' else initial-filled) else None
        port.orders['terminal']=dict(client_id='terminal',terminal=True,filled=str(filled),remaining='0',
            trades=[dict(id='f',qty=str(filled),price='100')] if filled else [])
        output=ExecutionManager(store,port,lambda:1000).reconcile('X')
        self.assertEqual(output['status'],'complete')
        self.assertIsNone(store.pending('X')); self.assertEqual(port.submitted_ids,[])
        return store.state('X')
    def test_zero_terminal_entry_is_flat(self):
        self.assertEqual(self.run_case('ENTRY',0,0)['phase'],'FLAT')
    def test_terminal_partial_entry_releases_protected_exposure(self):
        state=self.run_case('ENTRY',0,2)
        self.assertEqual(state['filled_qty'],'2'); self.assertEqual(state['phase'],'PROBE')
    def test_terminal_partial_exit_retains_manageable_residual(self):
        state=self.run_case('EXIT',4,2)
        self.assertEqual(state['filled_qty'],'2'); self.assertNotIn(state['phase'],('FLAT','EXIT_PENDING'))
    def test_terminal_partial_reduce_consumes_stage_once(self):
        state=self.run_case('REDUCE',8,2)
        self.assertEqual(state['filled_qty'],'6'); self.assertEqual(state['reduce_stage'],0)
        self.assertEqual(D(state['reduce_partial_qty']),2)
    def test_zero_add_preserves_probe(self):
        self.assertEqual(self.run_case('ADD',4,0)['phase'],'PROBE')
    def test_full_exit_is_flat(self):
        self.assertEqual(self.run_case('EXIT',4,4)['phase'],'FLAT')

class StopTickTests(unittest.TestCase):
    def test_directional_tick(self):
        from core_unified_live import quantized_stop
        self.assertEqual(quantized_stop('98.123','long','.01'),D('98.13'))
        self.assertEqual(quantized_stop('101.877','short','.01'),D('101.87'))

class EmergencyTests(ControllerTests):
    def test_known_emergency_flat_resolves_original_pending(self):
        self.run_entry(); state=self.store.state('X')
        intent=dict(id='add-unknown',kind='ADD',position_id=state['position_id'],side='long',qty='5',
                    expected_revision=state['revision'])
        self.store.reserve('X',intent,state['revision']); self.store.mark_terminal(intent['id'],0)
        self.store.reserve_action('X','emergency1',dict(kind='EMERGENCY_EXIT',original_id=intent['id'],created_ms=1000000))
        self.ex.orders['emergency1']=dict(id='emergency-order',clientOrderId='emergency1',symbol='X',status='closed',filled=25,remaining=0)
        self.ex.trades['emergency-order']=[dict(id='emergency-fill',order='emergency-order',symbol='X',amount=25,price=99,timestamp=1000000)]
        self.ex.pos=None; self.client.cancel_protection=lambda ids:self.ex.stops.clear()
        out=self.c.port.reconcile_emergency('X',intent)
        self.assertEqual(out['status'],'complete'); self.assertIsNone(self.store.pending('X'))
        self.assertEqual(self.store.state('X')['phase'],'FLAT')
        self.assertEqual(self.store.accounting_records()[-1]['trade_id'],'emergency-fill')
    def test_executable_quote_rejects_stale_and_uses_ask(self):
        self.ex.fetch_ticker=lambda symbol:dict(bid=99,ask=101,timestamp=1000000)
        self.assertEqual(self.c.port.executable_quote('long'),101)
        self.assertEqual(self.c.port.executable_quote('short'),99)
        self.ex.fetch_ticker=lambda symbol:dict(bid=99,ask=101,timestamp=990000)
        with self.assertRaises(ValueError): self.c.port.executable_quote('long')

    def test_terminal_emergency_partial_releases_manual_residual(self):
        self.run_entry(); state=self.store.state('X')
        intent=dict(id='add-unknown',kind='ADD',position_id=state['position_id'],side='long',qty='5',
                    expected_revision=state['revision'])
        self.store.reserve('X',intent,state['revision']); self.store.mark_terminal(intent['id'],0)
        self.store.reserve_action('X','emergency1',dict(kind='EMERGENCY_EXIT',original_id=intent['id'],created_ms=1000000))
        self.ex.orders['emergency1']=dict(id='emergency-order',clientOrderId='emergency1',symbol='X',status='canceled',filled=10,remaining=15)
        self.ex.trades['emergency-order']=[dict(id='emergency-fill',order='emergency-order',symbol='X',amount=10,price=99,timestamp=1000000)]
        self.ex.pos['contracts']=15
        out=self.c.port.reconcile_emergency('X',intent)
        self.assertEqual(out['status'],'complete'); self.assertIsNone(self.store.pending('X'))
        self.assertEqual(D(self.store.state('X')['filled_qty']),15)
        self.assertTrue(self.store.state('X')['emergency_required'])
        # Explicit manual request reaches execution with residual actual quantity.
        with patch.object(self.c.manager,'execute',return_value={'status':'complete'}) as execute:
            self.c.manual_close('operator')
            self.assertEqual(D(execute.call_args.args[1]['qty']),15)

class CumulativeReductionTests(unittest.TestCase):
    def test_partial_fills_only_finish_stage_at_full_quarter(self):
        from core_unified_state import new_position, advance, completed_state
        from core_unified_policy import POLICY
        state=new_position('p','long',100,2,40,40,80)
        state.update(protection_armed=True,protect_base_qty=40,phase='PROTECT_ARMED')
        event=dict(kind='WEAK_5M',confirmed=True,weak_1h=False,bar_ms=300000,lot=1,minimum=1)
        first=advance(state,event,POLICY)['intent']
        self.assertEqual(first['qty'],10)
        state['filled_qty']=36
        state=completed_state(state,dict(first,filled=4))
        self.assertEqual(state['reduce_stage'],0)
        self.assertIsNone(advance(state,event,POLICY)['intent'])
        second=advance(state,dict(event,bar_ms=600000),POLICY)['intent']
        self.assertEqual(second['qty'],6)
        state['filled_qty']=30
        state=completed_state(state,dict(second,filled=6))
        self.assertEqual(state['reduce_stage'],1)
        self.assertEqual(D(state['reduce_partial_qty']),0)
        self.assertIsNone(advance(state,dict(event,bar_ms=900000),POLICY)['intent'])
        third=advance(state,dict(event,bar_ms=900000,weak_1h=True),POLICY)['intent']
        self.assertEqual(third['qty'],10)
        self.assertEqual(third['stage'],2)
