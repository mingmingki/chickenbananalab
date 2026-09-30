import json
from pathlib import Path
import tempfile
import unittest
from deployment_resume import consume

class ResumeTests(unittest.TestCase):
    def test_manifest_is_release_bound_short_lived_and_consumed_once(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'resume.json'
            v=dict(release=str(Path(d).resolve()),username='chickenbananalab',
                   core=True,candidate_c='live',created_at=100,expires_at=200)
            p.write_text(json.dumps(v))
            for now,release in [(99,d),(200,d),(150,d+'/other')]:
                with self.assertRaises(ValueError): consume(p,release,now)
            self.assertEqual(consume(p,d,150),v)
            self.assertIsNone(consume(p,d,150))
            self.assertTrue(p.with_name('resume.json.consumed').exists())

class RollbackResumeTests(unittest.TestCase):
    def test_explicit_rollback_manifest_accepts_only_matching_settings(self):
        import deployment_resume as module
        from types import SimpleNamespace
        self.assertTrue(hasattr(module,'settings_match'))
        cfg=SimpleNamespace(CORE_UNIFIED_MODE='ROLLBACK',EXECUTION_MODE='LIVE',GPT_ENTRY_GATE_ENABLED=True,HOLD_AUDIT_ENABLED=False,CORE_EVENT_AI_ENABLED=False)
        self.assertTrue(module.settings_match({'core_mode':'ROLLBACK'},cfg))
        self.assertFalse(module.settings_match({},cfg))
        cfg.CORE_EVENT_AI_ENABLED=True
        self.assertFalse(module.settings_match({'core_mode':'ROLLBACK'},cfg))
