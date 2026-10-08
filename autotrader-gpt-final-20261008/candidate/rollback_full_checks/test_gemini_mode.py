import unittest
from threading import Event
from core_unified_review import ReviewBroker
from core_unified_policy import POLICY_VERSION, approval_valid


def candidate(mode='gemini'):
    return dict(id='signal', symbol='BTC', side='long', signal_ms=1000,
                expires_ms=61000, generation=1, snapshot_id='snapshot',
                reference_price=100, atr5=2, version=POLICY_VERSION, review_mode=mode)


class GeminiModeTests(unittest.TestCase):
    def test_unchecked_uses_gemini_without_gpt_and_preserves_price_guard(self):
        called=[]
        def gpt(c,s):
            called.append(c['id'])
            return dict(decision='reject',confidence=.9)
        b=ReviewBroker(lambda c,s:dict(action='long',confidence=.8),gpt,lambda:2000)
        b.require_gpt=lambda:False
        try:
            b.offer(candidate(),{})
        finally:b.close()
        out=b.drain()
        self.assertEqual(called,[])
        self.assertEqual(len(out),1)
        self.assertEqual(out[0]['gpt_decision'],'not_required')
        self.assertTrue(approval_valid(candidate(),out[0],2000,100)[0])
        self.assertFalse(approval_valid(candidate(),out[0],2000,100.5)[0])
        self.assertFalse(approval_valid(candidate('dual'),out[0],2000,100)[0])

    def test_setting_changed_during_review_discards_old_approval(self):
        entered,release=Event(),Event(); mode=[False]
        def gemini(c,s):
            entered.set(); release.wait(2)
            return dict(action='long',confidence=.8)
        b=ReviewBroker(gemini,lambda c,s:dict(decision='approve_now',confidence=.8),lambda:2000)
        b.require_gpt=lambda:mode[0]
        try:
            b.offer(candidate(),{});self.assertTrue(entered.wait(1));mode[0]=True
        finally:release.set();b.close()
        self.assertEqual(b.drain(),[])

    def test_checked_still_requires_gpt_approval(self):
        b=ReviewBroker(lambda c,s:dict(action='long',confidence=.8),
                       lambda c,s:dict(decision='wait',confidence=.8),lambda:2000)
        b.require_gpt=lambda:True
        try:b.offer(candidate('dual'),{})
        finally:b.close()
        self.assertEqual(b.drain(),[])


if __name__=='__main__': unittest.main()

class SavedSettingsTests(unittest.TestCase):
    def test_toggle_survives_reload_and_hold_audit_stays_retired(self):
        import tempfile
        from config import UserConfig
        with tempfile.TemporaryDirectory() as d:
            cfg=UserConfig(d);r=cfg.settings_revision
            cfg.save_env({'GPT_ENTRY_GATE_ENABLED':'false','HOLD_AUDIT_ENABLED':'true',
                          'POSITION_AI_REVIEW_ENABLED':'true'})
            self.assertFalse(cfg.GPT_ENTRY_GATE_ENABLED)
            self.assertFalse(UserConfig(d).HOLD_AUDIT_ENABLED)
            self.assertTrue(UserConfig(d).POSITION_AI_REVIEW_ENABLED)
            self.assertGreater(cfg.settings_revision,r)
            cfg.save_env({'GPT_ENTRY_GATE_ENABLED':'true'})
            self.assertTrue(UserConfig(d).GPT_ENTRY_GATE_ENABLED)
            cfg.save_env({'GPT_ENTRY_GATE_ENABLED':'false'})
            self.assertFalse(UserConfig(d).GPT_ENTRY_GATE_ENABLED)
