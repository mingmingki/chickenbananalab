import ast,unittest
from pathlib import Path
from types import SimpleNamespace as NS
from contextlib import nullcontext
from log_readability import format_line
class EventSettingsTests(unittest.TestCase):
    def test_actual_settings_handler_bool_and_omission(self):
        source=ast.parse(Path(__file__).resolve().parents[1].joinpath('web_app.py').read_text())
        node=next(n for n in source.body if isinstance(n,ast.FunctionDef) and n.name=='api_settings');node.decorator_list=[]
        saved={};cfg=NS(CORE_EVENT_AI_ENABLED=True,POSITION_SIZE_MODE='RISK',POSITION_FIXED_USDT=150,POSITION_PERCENT=10,
            CORE_SHORT_MAX_MARGIN_USDT=150,GPT_ENTRY_GATE_ENABLED=False,POSITION_AI_REVIEW_ENABLED=True,MAX_LEVERAGE=5,
            save_env=lambda v:saved.update(v))
        data=dict(poll_interval_seconds=300,leverage=5,stop_loss_pct=1.5,take_profit_pct=3,min_confidence=.6,min_hold_minutes=15)
        ns=dict(get_context=lambda n:NS(cfg=cfg,dir='/unused'),session={'username':'test'},request=NS(get_json=lambda **kw:data),
            account_order_lock=lambda d:nullcontext(),jsonify=lambda *a,**kw:a[0] if a else kw)
        exec(compile(ast.Module(body=[node],type_ignores=[]),'route','exec'),ns)
        self.assertEqual(ns['api_settings'](),{'ok':True});self.assertEqual(saved['CORE_EVENT_AI_ENABLED'],'true')
        data['core_event_ai_enabled']=False;self.assertEqual(ns['api_settings'](),{'ok':True})
        self.assertEqual(saved['CORE_EVENT_AI_ENABLED'],'false')
        for v in ('false',0,None):
            data['core_event_ai_enabled']=v;self.assertEqual(ns['api_settings']()[1],400)
    def test_event_log_explains_trigger(self):
        text,_=format_line('12:00:00 [INFO] [BTC/USDT:USDT] CORE {"reason":"ai_pending","trigger_kind":"range_break"}')
        self.assertIn('범위',text)
