import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT.parent / 'autotrader-rc-structure-risk-ai-20261005'
sys.path.insert(0, str(BASE))
# Import real dependencies before patching the archived entry functions.
import trader
spec = importlib.util.spec_from_file_location('entry_kernel', ROOT/'tools/load_entry_kernel.py')
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)
# Revised Adaptive engine and analyzers are real files, not AI/exchange calls.
for name in ('adaptive_exit_engine','openai_analyzer','telegram_notify','config','gpt_shadow_log'):
    spec = importlib.util.spec_from_file_location(name,ROOT/'patch'/f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    if hasattr(trader,name): setattr(trader,name,module)
import adaptive_exit_engine as ae
for name in ('AdaptiveExitContext','AdaptiveExitEngine','apply_ai_price_plan',
             'select_verified_ai_price_plan','build_ai_price_contract'):
    setattr(trader,name,getattr(ae,name))
sys.path.insert(0, str(ROOT/'patch'))
helper.load(trader, ROOT/'patch/trader.py')

# Use the changed order-query method only, preserving real baseline SDK adapters.
import ast
node=next(n for n in ast.parse((ROOT/'patch/okx_client.py').read_text()).body if isinstance(n,ast.ClassDef) and n.name=='OkxClient')
method=next(n for n in node.body if isinstance(n,ast.FunctionDef) and n.name=='fetch_order_status_by_client_id')
namespace=dict(trader.okx_client.__dict__)
exec(compile(ast.Module(body=[method],type_ignores=[]),str(ROOT/'patch/okx_client.py'),'exec'),namespace)
trader.okx_client.OkxClient.fetch_order_status_by_client_id=namespace['fetch_order_status_by_client_id']
