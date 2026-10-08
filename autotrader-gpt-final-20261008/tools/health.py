import os,sys,json,time,hashlib,subprocess,re,sqlite3
from pathlib import Path
import requests
from flask import Flask
root=Path('/opt/autotrader');sys.path.insert(0,str(root));os.environ['AUTOTRADER_PROJECT_DIR']=str(root)
import config,accounts,core_unified_service,candidate_c_runtime
cfg=config.UserConfig(accounts.user_dir('chickenbananalab'))
app=Flask('runtime-health');app.secret_key=(root/'flask_secret.key').read_text().strip()
s=requests.Session();s.cookies.set('session',app.session_interface.get_signing_serializer(app).dumps({'username':'chickenbananalab','authenticated':True}))
out={'observed_at':time.time(),'release':str(root.resolve()),'trader_sha256':hashlib.sha256((root/'trader.py').read_bytes()).hexdigest(),'http':{}}
for key,path in [('state','/api/state'),('candidate_c','/api/candidate_c_state'),('events','/api/shadow'),('costs','/api/operating-costs'),('daily','/api/analysis/daily-completion'),('learning','/api/analysis/self-learning')]:
 r=s.get('http://127.0.0.1:8080'+path,timeout=45);out['http'][key]=r.status_code;r.raise_for_status();d=r.json()
 if key=='state':
  out['core']={'running':d.get('running'),'last_error':d.get('last_error'),'settings':{k:d.get('settings',{}).get(k) for k in ('core_unified_mode','gpt_entry_gate_enabled','core_gpt_entry_timeout_bypass','core_paid_shadow_enabled')},'symbols':{sym:{k:row.get(k) for k in ('last_action','position','live_position','live_protection','last_entry_attempt')} for sym,row in d.get('symbols',{}).items()}}
 elif key=='candidate_c':
  out[key]={k:d.get(k) for k in ('status','engine_effective_mode','runtime','live_activation_blockers','configured_vs_effective_mismatch')}
  out[key]['symbols']={sym:{k:row.get('runtime',{}).get(k) for k in ('status','running','heartbeat_at','last_cycle_at','last_closed_bar','gpt_gate_reason','actual_position','monitor','effective_settings')} for sym,row in d.get('symbol_states',{}).items()}
 elif key=='events':out[key]=d.get('events',[])[:100]
 elif key=='costs':out[key]=d.get('summary')
 else:out[key]=d
r=s.get('http://127.0.0.1:8080/',timeout=20);out['http']['dashboard']=r.status_code;out['dashboard_entry_table']=('CORE 최종 진입' in r.text or 'core-entry' in r.text)
for asset in ('static/core_entry_status.js','static/operating_observability.js'):
 r=s.get('http://127.0.0.1:8080/'+asset,timeout=10);r.raise_for_status();assert hashlib.sha256(r.content).hexdigest()==hashlib.sha256((root/asset).read_bytes()).hexdigest();out['http'][asset]=r.status_code
out['owners']={sym:core_unified_service.owner(cfg,sym) for sym in cfg.ENABLED_SYMBOLS};out['candidate_c_parity']=candidate_c_runtime.backtest_live_parity_evidence()['verified']
state=Path('/var/lib/autotrader/deployment-backups/core-gpt-recovery-20261008');backup=json.loads((state/'private-state.json').read_text())
# Report only equality/count, never other accounts' identities or secrets.
prior=json.loads(Path('/tmp/gpt-recovery-pre-exchange.private.json').read_text())['account_settings_hashes'];after={p.parent.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (root/'users').glob('*/.env')}
out['other_accounts_settings_unchanged']=all(after.get(k)==v for k,v in prior.items() if k!='chickenbananalab')
flags={'GPT_ENTRY_GATE_ENABLED','CORE_GPT_ENTRY_TIMEOUT_BYPASS','CORE_PAID_SHADOW_ENABLED'}
def unchangedlines(text):return [line for line in text.splitlines() if line.partition('=')[0].strip() not in flags or line.lstrip().startswith('#')]
out['other_user_settings_unchanged']=unchangedlines((state/'account.env').read_text())==unchangedlines((Path(cfg.user_dir)/'.env').read_text())
out['shared_paths_preserved']=all((root/p.name).is_symlink() and os.readlink(root/p.name)==os.readlink(p) for p in Path(backup['previous']).iterdir() if p.is_symlink())
def cmd(*args):return subprocess.check_output(args,text=True).strip()
out['service']={k:cmd('systemctl','show','autotrader.service','-p',k,'--value') for k in ('ActiveState','MainPID','NRestarts','WorkingDirectory','ExecStart')}
pid=out['service']['MainPID'];out['process']={'cwd':os.readlink('/proc/'+pid+'/cwd'),'exe':os.readlink('/proc/'+pid+'/exe'),'cmdline':Path('/proc/'+pid+'/cmdline').read_bytes().replace(b'\0',b' ').decode()}
logs=cmd('journalctl','-u','autotrader.service','--since','@'+str(int(backup['created_at'])),'--no-pager','-o','cat')
heartbeat=[];errors=[]
for line in logs.splitlines():
 if re.search(r'api[_ -]?key|authorization|Bearer|chat[_ -]?id|token|secret|password|passphrase|https?://',line,re.I):continue
 if any(tag in line for tag in ('CORE_ENTRY_TIMING','CORE_AI_BUDGET_CALL','CORE_AI_BUDGET_SKIP','CORE_ENTRY_EVENT_WAKE')):heartbeat.append(line[:1500])
 if 'Traceback' in line or '[ERROR]' in line or '[CRITICAL]' in line:errors.append(line[:1500])
out['core_closed_bar_heartbeat']=heartbeat[-40:];out['new_error_lines']=errors[-20:];out['new_error_count']=len(errors)
receipt=Path(cfg.user_dir)/'core_entry_events.sqlite3'
if receipt.exists():
 db=sqlite3.connect(receipt);out['outbox_counts']=dict(db.execute('select delivery,count(*) from events group by delivery'));out['order_receipt_counts']=dict(db.execute('select status,count(*) from entry_orders group by status'));db.close()
assert out['core']['running'] and out['core']['last_error'] is None
assert out['core']['settings']==dict(core_unified_mode='ROLLBACK',gpt_entry_gate_enabled=True,core_gpt_entry_timeout_bypass=True,core_paid_shadow_enabled=False)
assert out['candidate_c']['runtime']['running'] and not out['candidate_c']['live_activation_blockers']
assert out['service']['ActiveState']=='active' and out['service']['NRestarts']=='0'
assert out['other_accounts_settings_unchanged'] and out['other_user_settings_unchanged'] and out['shared_paths_preserved'] and out['candidate_c_parity']
dest=Path(sys.argv[1]);dest.write_text(json.dumps(out,ensure_ascii=False,indent=2));dest.chmod(0o600)
print(json.dumps({'observed_at':out['observed_at'],'release':out['release'],'service':out['service'],'core_running':out['core']['running'],'candidate_c':out['candidate_c']['runtime'],'C_symbols':{k:{x:r[x] for x in ('heartbeat_at','last_closed_bar','last_cycle_at')} for k,r in out['candidate_c']['symbols'].items()},'http':out['http'],'new_error_count':out['new_error_count'],'core_heartbeat_lines':len(heartbeat),'other_settings_preserved':out['other_user_settings_unchanged'] and out['other_accounts_settings_unchanged'],'shared_paths_preserved':out['shared_paths_preserved'],'candidate_c_parity':out['candidate_c_parity']}))
