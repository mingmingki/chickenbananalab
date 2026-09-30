import unittest
import tempfile
from pathlib import Path
from core_unified_store import Store


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)/'state.sqlite3'
    def tearDown(self): self.temp.cleanup()

    def test_reservation_survives_restart(self):
        s = Store(self.path)
        self.assertTrue(s.save_state('X',dict(revision=0,filled_qty='0'),None))
        i = dict(id='one',kind='ENTRY',side='long',qty='2',expected_revision=0,expires_ms=60000)
        self.assertTrue(s.reserve('X',i,0))
        s.close()
        s = Store(self.path)
        try:
            self.assertEqual(s.pending('X')['id'],'one')
            self.assertFalse(s.reserve('X',i,0))
            self.assertTrue(s.apply_fill('one','t1','1','100'))
            self.assertFalse(s.apply_fill('one','t1','1','100'))
            self.assertEqual(s.state('X')['filled_qty'],'1')
            self.assertIsNotNone(s.pending('X'))
            self.assertTrue(s.apply_fill('one','t2','1','100',terminal=True))
            self.assertEqual(s.state('X')['filled_qty'],'2')
            # Terminal fill is insufficient: protection must be confirmed first.
            self.assertIsNotNone(s.pending('X'))
            s.complete('one',protection_confirmed=True)
            self.assertIsNone(s.pending('X'))
        finally: s.close()

    def test_stale_revision_and_parallel_reserve_are_blocked(self):
        s,t = Store(self.path),Store(self.path)
        try:
            s.save_state('X',dict(revision=0,filled_qty='0'),None)
            self.assertFalse(s.save_state('X',dict(revision=1,filled_qty='9'),9))
            i = dict(id='one',kind='ENTRY',side='long',qty='2',expected_revision=0)
            self.assertTrue(s.reserve('X',i,0))
            self.assertFalse(t.reserve('X',dict(i,id='two'),0))
            self.assertEqual(t.pending('X')['id'],'one')
        finally: s.close(); t.close()

    def test_overfill_and_reduction_cannot_increase_exposure(self):
        s = Store(self.path)
        try:
            s.save_state('X',dict(revision=0,filled_qty='4'),None)
            s.reserve('X',dict(id='r',kind='REDUCE',side='long',qty='1',
                              expected_revision=0,stage=1),0)
            with self.assertRaises(ValueError): s.apply_fill('r','bad','2','100')
            self.assertEqual(s.state('X')['filled_qty'],'4')
            s.apply_fill('r','good','1','100',terminal=True)
            self.assertEqual(s.state('X')['filled_qty'],'3')
            with self.assertRaises(ValueError): s.complete('r',protection_confirmed=False)
        finally: s.close()


if __name__ == '__main__': unittest.main()
