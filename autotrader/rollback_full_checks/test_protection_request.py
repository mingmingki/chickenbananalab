"""Exercise the production wrapper without credentials or order submission."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


class ProtectionRequestTests(unittest.TestCase):
    def setUp(self):
        import os
        path=Path(os.environ.get('PROTECTION_WRAPPER_SOURCE',str(Path(__file__).resolve().parents[1]/'okx_client.py')))
        tree=ast.parse(path.read_text())
        cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='OkxClient')
        method=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='attach_protection')
        scope={}
        exec(compile(ast.Module(body=[method],type_ignores=[]),str(path),'exec'),scope)
        self.attach=scope['attach_protection']
        self.exchange=Mock()
        self.client=SimpleNamespace(symbol='PI/USDT:USDT',exchange=self.exchange,_log=lambda:Mock())

    def test_core_oco_uses_algo_identity_and_reduce_only_net_side(self):
        self.attach(self.client,'long',3903,.084,.08783,algo_client_order_id='coreProtection1')
        symbol,kind,side,qty,price,params=self.exchange.create_order.call_args.args
        self.assertEqual((symbol,kind,side,qty),('PI/USDT:USDT','oco','sell',3903))
        self.assertEqual(params['algoClOrdId'],'coreProtection1')
        self.assertEqual(params['posSide'],'net')
        self.assertIs(params['reduceOnly'],True)
        self.assertNotIn('clientOrderId',params)

    def test_existing_candidate_client_id_path_is_unchanged(self):
        self.attach(self.client,'short',2,101,None,client_order_id='existingCandidateId')
        params=self.exchange.create_order.call_args.args[-1]
        self.assertEqual(params['clientOrderId'],'existingCandidateId')
        self.assertNotIn('algoClOrdId',params)
        self.assertNotIn('takeProfitPrice',params)

    def test_ambiguous_ids_do_not_submit(self):
        with self.assertRaises(ValueError):
            self.attach(self.client,'long',1,99,102,client_order_id='x',algo_client_order_id='y')
        self.exchange.create_order.assert_not_called()
