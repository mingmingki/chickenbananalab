import unittest
from copy import deepcopy
from core_unified_okx_contract import position_identity,owned_protection,normalize_order


class OkxContractTests(unittest.TestCase):
    def setUp(self):
        self.position=dict(position_id='42',entry_timestamp_ms=1000,side='long',contracts='4',pos_side='net')
        self.stop=dict(algoId='owned',instId='X-USDT-SWAP',side='sell',state='live',
                       reduceOnly='true',sz='4',slTriggerPx='98.5',posSide='net')

    def proof(self,orders):
        return owned_protection(self.position,orders,instrument_id='X-USDT-SWAP',
                                owned_ids=['owned'],stop_price='98.5')

    def test_reused_exchange_position_id_is_a_different_lifecycle(self):
        a=position_identity(self.position)
        b=position_identity(dict(self.position,entry_timestamp_ms=2000))
        self.assertIsNotNone(a)
        self.assertNotEqual(a,b)
        self.assertIsNone(position_identity(dict(self.position,entry_timestamp_ms=None)))

    def test_only_recorded_id_counts_even_with_identical_price_size(self):
        self.assertTrue(self.proof([self.stop])['confirmed'])
        other=dict(self.stop,algoId='foreign')
        self.assertFalse(self.proof([other])['confirmed'])
        self.assertTrue(self.proof([self.stop,other])['confirmed'])

    def test_undersize_or_weaker_stop_is_not_protected(self):
        for change in ({'sz':'3.99'},{'slTriggerPx':'98'},{'reduceOnly':'false'},
                       {'side':'buy'},{'state':'canceled'},{'instId':'Y-USDT-SWAP'}):
            with self.subTest(change=change):
                self.assertFalse(self.proof([dict(self.stop,**change)])['confirmed'])
        self.assertTrue(self.proof([dict(self.stop,slTriggerPx='99')])['confirmed'])

    def test_duplicate_protection_rows_cannot_double_count_quantity(self):
        self.assertFalse(self.proof([dict(self.stop,sz='2'),dict(self.stop,sz='2')])['confirmed'])

    def test_trade_identity_and_cumulative_quantity_must_match_order(self):
        order=dict(id='exchange-order',symbol='X',clientOrderId='client',status='closed',filled='2',remaining='0')
        trades=[dict(id='trade-1',order='exchange-order',symbol='X',amount='2',price='100')]
        result=normalize_order(order,trades,client_id='client',symbol='X')
        self.assertIsNotNone(result)
        self.assertTrue(result['terminal'])
        self.assertEqual(result['trades'][0]['qty'],'2')
        with self.assertRaises(ValueError):
            normalize_order(order,[dict(trades[0],order='foreign')],client_id='client',symbol='X')
        with self.assertRaises(ValueError):
            normalize_order(order,[dict(trades[0],amount='1')],client_id='client',symbol='X')
        with self.assertRaises(ValueError):
            normalize_order(dict(order,clientOrderId='foreign'),trades,client_id='client',symbol='X')

    def test_terminal_cancellation_keeps_actual_fill_not_requested_size(self):
        order=dict(id='order',symbol='X',clientOrderId='client',status='canceled',filled='1',remaining='3')
        t=dict(id='t',order='order',symbol='X',amount='1',price='100')
        result=normalize_order(order,[t],client_id='client',symbol='X')
        self.assertIsNotNone(result)
        self.assertTrue(result['terminal'])
        self.assertEqual(result['filled'],'1')
        # Remaining submitted quantity differs from remaining *working* quantity.
        self.assertEqual(result['remaining'],'0')

    def test_zero_fill_foreign_order_is_not_terminal_evidence(self):
        order=dict(id='foreign',symbol='Y',clientOrderId='client',status='canceled',filled='0',remaining='1')
        with self.assertRaises(ValueError):
            normalize_order(order,[],client_id='client',symbol='X')

    def test_contradictory_or_unknown_position_mode_is_not_protected(self):
        self.assertFalse(self.proof([dict(self.stop,posSide='short')])['confirmed'])
        self.assertFalse(self.proof([dict(self.stop,posSide=None)])['confirmed'])
        self.position.pop('pos_side')
        self.assertFalse(self.proof([self.stop])['confirmed'])


if __name__=='__main__': unittest.main()
