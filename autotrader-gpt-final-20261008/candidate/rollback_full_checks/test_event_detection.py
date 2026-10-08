import unittest
from copy import deepcopy
from core_unified_events import detect_events

def market(closes, start=0):
    return dict(symbol='X',account='a',now_ms=(start+len(closes))*60000,
        frames={'1m':[dict(open_ms=(start+i)*60000,close_ms=(start+i+1)*60000,close=p) for i,p in enumerate(closes)],
                '5m':[dict(atr14=2)]})

class DetectionTests(unittest.TestCase):
    def test_independent_range_both_directions(self):
        for last,side in [(100.3,'long'),(99.7,'short')]:
            events,_=detect_events(market([100]*20+[last]),{},None)
            self.assertTrue(any(e['kind']=='range_break' and e['direction']==side for e in events))
    def test_repeat_and_continued_breakout_do_not_spam(self):
        s=market([100]*20+[100.3]);events,m=detect_events(s,{},None)
        self.assertEqual(detect_events(s,m,None)[0],[])
        self.assertFalse(any(e['kind']=='range_break' for e in detect_events(market([100]*19+[100.3,100.5],1),m,None)[0]))
    def test_material_move_uses_success_basis(self):
        m=dict(last_success_price='100',last_success_atr5='2',basis_version='b')
        self.assertTrue(any(e['kind']=='material_move' for e in detect_events(market([100]*20+[101]),m,None)[0]))
    def test_thesis_cross_and_giveback(self):
        m=dict(invalidation_price=99,basis_version='a')
        pos=dict(side='long',initial_entry=100,initial_r=2,mfe_price=102,position_id='p')
        kinds={e['kind'] for e in detect_events(market([100]*20+[98]),m,pos)[0]}
        self.assertTrue({'invalidation_cross','profit_giveback'}<=kinds)
    def test_unknown_atr_does_not_invent_event(self):
        s=market([100]*20+[101]);s['frames']['5m'][0]['atr14']=0
        self.assertEqual(detect_events(s,{},None)[0],[])
    def test_conflicting_repeated_and_future_bars_rejected(self):
        s=market([100]*21);_,m=detect_events(s,{},None)
        s['frames']['1m'][-1]['close']=101
        with self.assertRaises(ValueError):detect_events(s,m,None)
        s=market([100]*21);s['now_ms']-=1
        with self.assertRaises(ValueError):detect_events(s,{},None)
    def test_direction_requires_three_closes(self):
        ev,_=detect_events(market([100]*19+[100.3,100.6]),{},None)
        self.assertTrue(any(e['kind']=='direction_change' for e in ev))
