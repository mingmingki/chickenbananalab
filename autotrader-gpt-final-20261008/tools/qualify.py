import os,ast,json,hashlib,py_compile,importlib
from pathlib import Path
root=Path(__file__).parent;c=root/'candidate';base=Path('/Users/bagmingi/chickenbanana-work/chickenbananalab/autotrader-live-recovery-20261008/production')
out={}
for name in ('_core_ai_call_gate','_handle_position_ai_review','_position_ai_review_candidate','_position_ai_review_cooldown_blocked_for_trigger'):
 def body(path):return ast.dump(next(n for n in ast.parse(path.read_text()).body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name==name),include_attributes=False)
 out[name]={'unchanged_from_live_baseline':body(base/'trader.py')==body(c/'trader.py')};assert out[name]['unchanged_from_live_baseline']
for module in ('config','trader','web_app','okx_client','openai_analyzer','gemini_analyzer','core_entry_orders','core_entry_events','core_entry_policy','telegram_notify','usage_log','operating_costs','candidate_c_runtime'):
 importlib.import_module(module)
import candidate_c_runtime
parity=candidate_c_runtime.backtest_live_parity_evidence();assert parity['verified'],parity
out['candidate_c_parity']=parity
compiled=0
for path in c.rglob('*.py'):
 if '__pycache__' not in path.parts:py_compile.compile(str(path),doraise=True);compiled+=1
out['compiled_python_files']=compiled
out['core_strategy_sources']={name:hashlib.sha256((c/name).read_bytes()).hexdigest() for name in ('core_entry_timing.py','market_context.py','core_unified_policy.py','adaptive_exit_engine.py','risk_manager.py')}
for name,h in out['core_strategy_sources'].items():assert h==hashlib.sha256((base/name).read_bytes()).hexdigest()
out['core_strategy_hashes_unchanged']=True
(root/'evidence/qualification.json').write_text(json.dumps(out,indent=2)+'\n')
print(json.dumps({'imports':'passed','compiled_python_files':compiled,'candidate_c_live_backtest_parity':parity['verified'],'core_strategy_hashes_unchanged':True,'mandatory_AI_timing_functions_unchanged':True}))
