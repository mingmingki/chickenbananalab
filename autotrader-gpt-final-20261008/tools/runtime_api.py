import os,json,time,sys,hashlib
from pathlib import Path
import requests
from flask import Flask
root=Path('/opt/autotrader')
app=Flask('runtime-audit');app.secret_key=(root/'flask_secret.key').read_text().strip()
cookie=app.session_interface.get_signing_serializer(app).dumps({'username':'chickenbananalab','authenticated':True})
s=requests.Session();s.cookies.set('session',cookie)
out={'observed_at':time.time(),'release':str(root.resolve()),'trader_sha256':hashlib.sha256((root/'trader.py').read_bytes()).hexdigest()}
for name,path in [('core','/api/state'),('candidate_c','/api/candidate_c_state'),('entry_events','/api/shadow')]:
 response=s.get('http://127.0.0.1:8080'+path,timeout=45)
 response.raise_for_status();d=response.json()
 if name=='core':
  out[name]={k:d.get(k) for k in ('running','last_error')}
  out[name]['settings']={k:v for k,v in d.get('settings',{}).items() if k in ('core_unified_mode','execution_mode','gpt_entry_gate_enabled','core_gpt_entry_timeout_bypass','core_paid_shadow_enabled','enabled_symbols','leverage')}
  out[name]['symbols']={sym:{k:r.get(k) for k in ('enabled','last_action','last_update','position','live_position','live_protection','last_entry_attempt','unified_status')} for sym,r in d.get('symbols',{}).items()}
 elif name=='candidate_c':
  out[name]={k:d.get(k) for k in ('status','configured_mode','engine_effective_mode','runtime','live_activation_blockers','configured_vs_effective_mismatch','gpt_entry_gate_configured_vs_effective_mismatch')}
  out[name]['symbols']={sym:{k:r.get(k) for k in ('runtime','running','mode','last_cycle_at','status','position','live_position','protection','last_closed_candle_ts')} for sym,r in d.get('symbol_states',{}).items()}
 else:
  out[name]={'events':d.get('events',[])[:10],'recent':[{k:r.get(k) for k in ('time','decision_id','symbol','gemini_action','gpt_decision','gate_result','order_success')} for r in d.get('recent',[])[:10]]}
filename=sys.argv[1] if len(sys.argv)>1 else '/tmp/gpt-recovery-runtime.json'
Path(filename).write_text(json.dumps(out,ensure_ascii=False,indent=2));os.chmod(filename,0o600)
print(json.dumps(out,ensure_ascii=False))
