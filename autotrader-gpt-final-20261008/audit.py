import os,sys,json,datetime,hashlib
from pathlib import Path
os.environ['AUTOTRADER_PROJECT_DIR']='/opt/autotrader'
sys.path.insert(0,'/opt/autotrader')
import config,accounts
from operating_costs import build_operating_cost_summary as summary
for row in accounts.list_accounts():
 if not row['approved']: continue
 cfg=config.UserConfig(accounts.user_dir(row['username']))
 print(json.dumps({'account':row['username'],'approved':row['approved'],'core_mode':cfg.CORE_UNIFIED_MODE,'execution_mode':cfg.EXECUTION_MODE,'gate_enabled':cfg.GPT_ENTRY_GATE_ENABLED,'telegram_existing':bool(cfg.TELEGRAM_BOT_TOKEN and cfg.TELEGRAM_CHAT_ID),'core_symbols':getattr(cfg,'ENABLED_SYMBOLS',None),'candidate_c_mode':getattr(cfg,'CANDIDATE_C_MODE',None),'openai_key_present':bool(cfg.OPENAI_API_KEY)},ensure_ascii=False))
 files=sorted(Path(cfg.user_dir).glob('*'))
 print('RUNTIME_FILES',json.dumps([p.name for p in files if p.is_file() and not p.name.startswith('.') and 'key' not in p.name and 'secret' not in p.name]))
 for f in files:
  if f.name.endswith('.json') and any(s in f.name for s in ('runtime','ownership','active','session')):
   try:
    d=json.loads(f.read_text());print('STATE',f.name,json.dumps({k:v for k,v in d.items() if k in ('running','mode','status','enabled','symbols','owners','updated_at','last_closed_bar_ts','core_mode')},ensure_ascii=False)[:3500])
   except Exception: pass
 try: print('COST',json.dumps(summary(cfg.user_dir),ensure_ascii=False))
 except Exception as e: print('COST_ERROR',type(e).__name__)
