import unittest
from core_unified_live import near_stop_confirmation

class EmergencyThresholdTests(unittest.TestCase):
    def test_both_sides_require_second_observation_25_seconds_later(self):
        for side,stop,mark in [('long',90,91),('short',110,109)]:
            state={'stop_price':stop}
            position={'side':side,'entry_price':100,'mark_price':mark}
            self.assertEqual(near_stop_confirmation(state,position,100000),(100000,False))
            state['emergency_first_ms']=100000
            self.assertEqual(near_stop_confirmation(state,position,124999),(100000,False))
            self.assertEqual(near_stop_confirmation(state,position,125000),(100000,True))

    def test_recovery_and_stop_above_break_even_reset_observation(self):
        state={'stop_price':90,'emergency_first_ms':100000}
        pos={'side':'long','entry_price':100,'mark_price':95}
        self.assertEqual(near_stop_confirmation(state,pos,130000),(None,False))
        state['stop_price']=101
        self.assertEqual(near_stop_confirmation(state,pos,130000),(None,False))
