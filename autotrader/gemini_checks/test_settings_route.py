import math
import ast
from pathlib import Path
from types import SimpleNamespace
from contextlib import nullcontext
import tempfile
import unittest

class SettingsRouteTests(unittest.TestCase):
    def test_off_on_and_invalid_values_use_saved_config(self):
        from config import UserConfig
        source=ast.parse(Path(__file__).resolve().parents[1].joinpath('web_app.py').read_text())
        node=next(n for n in source.body if isinstance(n,ast.FunctionDef) and n.name=='api_settings')
        node.decorator_list=[]
        with tempfile.TemporaryDirectory() as d:
            cfg=UserConfig(d)
            data=dict(poll_interval_seconds=300,leverage=5,stop_loss_pct=1.5,take_profit_pct=3,
                min_confidence=.6,min_hold_minutes=15,position_size_mode='RISK',
                gpt_entry_gate_enabled=False,position_ai_review_enabled=True)
            ns=dict(math=math,get_context=lambda name:SimpleNamespace(cfg=cfg,dir=d),session={'username':'test'},
                request=SimpleNamespace(get_json=lambda **kw:data),
                account_order_lock=lambda path:nullcontext(),jsonify=lambda *a,**kw:a[0] if a else kw)
            exec(compile(ast.Module(body=[n for n in source.body if isinstance(n,ast.FunctionDef) and n.name in ('_core_order_mode_updates','_core_exit_settings_updates')]+[node],type_ignores=[]),'<settings-test>','exec'),ns)
            self.assertEqual(ns['api_settings'](),{'ok':True})
            self.assertFalse(UserConfig(d).GPT_ENTRY_GATE_ENABLED)
            self.assertTrue(UserConfig(d).POSITION_AI_REVIEW_ENABLED)
            data['gpt_entry_gate_enabled']=True
            self.assertEqual(ns['api_settings'](),{'ok':True})
            self.assertTrue(UserConfig(d).GPT_ENTRY_GATE_ENABLED)
            data['gpt_entry_gate_enabled']='false'
            self.assertEqual(ns['api_settings']()[1],400)
            self.assertTrue(UserConfig(d).GPT_ENTRY_GATE_ENABLED)
