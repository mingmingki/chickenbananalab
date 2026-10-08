import copy
import unittest
from core_unified_market import detect_probe, validate_snapshot
from core_unified_policy import POLICY


def snapshot(side='long'):
    end = 3600000
    sign = 1 if side == 'long' else -1
    def row(end, width, close, macd):
        return dict(open_ms=end-width, close_ms=end, close=close,
                    ema20=100, macd=macd, volume=10, atr14=2)
    minute = [row(end-(20-i)*60000,60000,100-sign,0) for i in range(20)]
    minute.append(row(end,60000,100+sign,sign))
    return dict(account='test',symbol='X',now_ms=end,generation=1,
                version=POLICY['version'],frames={
        '1m':minute,
        '3m':[row(end-180000,180000,100-sign,0),row(end,180000,100+sign,sign)],
        '5m':[row(end-300000,300000,100-sign,0),row(end,300000,100+sign,sign)]})


class MarketTests(unittest.TestCase):
    def test_both_directions_and_deterministic_identity(self):
        for side in ('long','short'):
            s = snapshot(side)
            c = detect_probe(s, POLICY)
            self.assertIsNotNone(c)
            self.assertEqual(c['side'],side)
            self.assertEqual(c['id'],detect_probe(copy.deepcopy(s),POLICY)['id'])

    def test_future_stale_gap_and_conflicting_duplicates_fail_closed(self):
        for mode in ('future','stale','gap','duplicate','nan','volume'):
            s = snapshot()
            if mode == 'future': s['frames']['1m'][-1]['close_ms'] += 60000
            if mode == 'stale': s['now_ms'] += 65000
            if mode == 'gap': del s['frames']['1m'][-2]
            if mode == 'duplicate': s['frames']['1m'].append(s['frames']['1m'][-1])
            if mode == 'nan': s['frames']['5m'][-1]['atr14'] = float('nan')
            if mode == 'volume': s['frames']['1m'][-1]['volume'] = 1
            with self.subTest(mode=mode):
                self.assertIsNone(detect_probe(s,POLICY))

    def test_partial_tf_signal_does_not_create_candidate(self):
        s = snapshot()
        s['frames']['3m'][-1]['macd'] = -1
        self.assertIsNone(detect_probe(s,POLICY))


if __name__ == '__main__': unittest.main()
