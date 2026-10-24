"""Qualified DOGE/SOL deployment and in-place owned OCO migration."""
import os,sys,json,time,hashlib,tempfile,shutil,subprocess,datetime,fcntl,sqlite3,copy,re
from pathlib import Path
EXPECTED=Path('/opt/autotrader-releases/ai_cost_v18_20261012T224703KST')
LINK=Path('/opt/autotrader')

def run(*args):return subprocess.check_output(args,text=True).strip()
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def failed_nodeids(output):return set(re.findall(r'^FAILED (\S+)',output,re.M))
def atomic(p,text,uid=None,gid=None):
 fd,name=tempfile.mkstemp(dir=p.parent,prefix='.compact-')
 try:
  with os.fdopen(fd,'w') as f:f.write(text);f.flush();os.fsync(f.fileno())
  os.chmod(name,0o600)
  if uid is not None:os.chown(name,uid,gid)
  os.replace(name,p)
 finally:
  if os.path.exists(name):os.unlink(name)

def resume(release,st,*,candidate=True):
 p=Path('/var/lib/autotrader/deployment-resume');p.mkdir(parents=True,exist_ok=True)
 p.chmod(0o700);os.chown(p,st.st_uid,st.st_gid);now=time.time()
 atomic(p/'pending.json',json.dumps(dict(release=str(release.resolve()),created_at=now,expires_at=now+600,
  username='chickenbananalab',core=True,core_mode='ROLLBACK',candidate_c='live' if candidate else None)),st.st_uid,st.st_gid)

def migrate(cfg,client,ledger,epochs,*,apply=True,journal=None):
 """Caller holds account lock and service is stopped; no cancel/quantity change."""
 from candidate_c_protection_bounds import normalized_prices
 from stop_contract import round_stop_price_never_loosening
 record,ambiguous=ledger.find_protected_entry();assert not ambiguous and record,'migration_owned_entry_missing'
 epoch=epochs.get(record.intent_id)
 before=client.fetch_position();assert before and before['side']==epoch.side
 assert str(before.get('position_id'))==str(epoch.exchange_position_id),'migration_position_identity'
 assert str(before.get('entry_timestamp_ms'))==str(epoch.exchange_entry_timestamp_ms),'migration_entry_identity'
 assert float(before['entry_price'])==float(epoch.raw_entry_price),'migration_entry_price'
 assert float(before['contracts'])==float(epoch.remaining_contracts),'migration_quantity'
 ids=client.fetch_pending_protection_algo_ids();assert ids==epoch.protective_algo_ids and len(ids)==1,'migration_algo_identity'
 old=client.fetch_protection_order_by_algo_id(ids[0])
 assert old and old['algo_cl_ord_id']==epoch.attach_algo_cl_ord_id,'migration_algo_owner'
 assert old['side']==('buy' if epoch.side=='short' else 'sell') and float(old['sz'])==float(before['contracts']),'migration_coverage'
 entry=float(epoch.raw_entry_price)
 cap_stop=entry*(1.02 if epoch.side=='short' else .98)
 stop,target=normalized_prices(epoch.side,entry,cap_stop,float(old['tp_price']),epoch.tick_size)
 existing_stop=round_stop_price_never_loosening(epoch.side,float(old['sl_price']),epoch.tick_size)
 stop=min(stop,existing_stop) if epoch.side=='short' else max(stop,existing_stop)
 assert stop<=float(old['sl_price']) if epoch.side=='short' else stop>=float(old['sl_price']),'migration_would_loosen'
 mark=float(client.fetch_last_price())
 assert target<mark<stop if epoch.side=='short' else stop<mark<target,'migration_trigger_crossed'
 before_identity={k:before.get(k) for k in ('side','contracts','entry_price','position_id','entry_timestamp_ms')}
 if not apply:
  return dict(symbol=epoch.symbol,algo_id=ids[0],contracts=before['contracts'],entry=before['entry_price'],
              old_sl=old['sl_price'],old_tp=old['tp_price'],sl=stop,tp=target,planned=True)
 if journal is not None:
  journal(dict(status='pending',symbol=epoch.symbol,entry_intent_id=record.intent_id,
               position=before_identity,algo_id=ids[0],algo_cl_ord_id=old['algo_cl_ord_id'],
               old_sl=old['sl_price'],old_tp=old['tp_price'],sl=stop,tp=target))
 if (stop,target)!=(float(old['sl_price']),float(old['tp_price'])):
  try:client.amend_protective_stop(ids[0],new_sl_price=stop,new_tp_price=target)
  except Exception:
   # Request may have succeeded. Actual order+position read is authoritative.
   pass
 verified=client.fetch_protection_order_by_algo_id(ids[0]);after=client.fetch_position()
 assert after and {k:after.get(k) for k in before_identity}==before_identity,'migration_position_changed'
 assert verified and verified['algo_cl_ord_id']==old['algo_cl_ord_id'] and verified['side']==old['side'],'migration_owner_changed'
 assert float(verified['sz'])==float(old['sz']),'migration_qty_changed'
 assert (float(verified['sl_price']),float(verified['tp_price']))==(stop,target),'migration_amend_unverified'
 # Only mutable protection fields; initial risk/strategy snapshots remain immutable.
 old_snapshot=copy.deepcopy(epoch.__dict__)
 epoch.current_stop_price=stop;epoch.target_price=target
 assert {k:v for k,v in epoch.__dict__.items() if k not in ('current_stop_price','target_price')}=={k:v for k,v in old_snapshot.items() if k not in ('current_stop_price','target_price')}
 epochs.save(record.intent_id,epoch)
 if journal is not None:journal(dict(status='completed',symbol=epoch.symbol,entry_intent_id=record.intent_id,sl=stop,tp=target))
 return dict(symbol=epoch.symbol,algo_id=ids[0],contracts=after['contracts'],entry=after['entry_price'],
             old_sl=old['sl_price'],old_tp=old['tp_price'],sl=stop,tp=target)

if __name__=='__main__':
 PATCH=Path(sys.argv[1]);apply='--apply' in sys.argv
 lock=Path('/var/lib/autotrader/deployment-backups/.compact-v19.lock');lock.parent.mkdir(parents=True,exist_ok=True)
 f=lock.open('a');fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
 assert LINK.resolve()==EXPECTED,'production_release_changed'
 manifest=json.loads((PATCH/'manifest.json').read_text())
 for name,v in manifest.items():
  p=Path(name);assert not p.is_absolute() and '..' not in p.parts and p.parts[0] not in ('users','logs','.venv')
  assert not (LINK/p).is_symlink()
  assert (sha(LINK/p) if (LINK/p).exists() else None)==v['baseline'],name
  assert sha(PATCH/'source'/p)==v['candidate'],name
 prepared=PATCH/'prepared-release.json'
 if prepared.exists():new=Path(json.loads(prepared.read_text())['release'])
 else:
  new=Path('/opt/autotrader-releases')/('compact_bounds_v19_'+datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).strftime('%Y%m%dT%H%M%SKST'))
  shutil.copytree(EXPECTED,new,symlinks=True,ignore=shutil.ignore_patterns('__pycache__','.pytest_cache'))
  for name in manifest:
   dest=new/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(PATCH/'source'/name,dest)
  atomic(prepared,json.dumps({'release':str(new)}))
 for name,v in manifest.items():assert sha(new/name)==v['candidate'],name
 os.environ['AUTOTRADER_PROJECT_DIR']=str(new);sys.path.insert(0,str(new))
 import config,accounts,candidate_c_runtime,candidate_c_hybrid_ownership as ownership
 import candidate_c_exit_management as cem,candidate_c_intent_ledger as il
 from okx_client import OkxClient
 cfg=config.UserConfig(str(new/'users/chickenbananalab'));settings=Path(cfg.user_dir)/'.env';st=settings.stat();settings_sha=sha(settings)
 assert cfg.CANDIDATE_C_COMPACT_PROTECTION_ENABLED and cfg.CANDIDATE_C_ORDER_MODE=='AUTO_ALL'
 assert cfg.CANDIDATE_C_SIZING_MODE=='VARIABLE_RISK' and cfg.CANDIDATE_C_RISK_PER_TRADE_PCT==1 and cfg.CANDIDATE_C_LEVERAGE==5
 assert cfg.ADAPTIVE_EXIT_MODE=='LIVE_BOUNDED' and cfg.CORE_UNIFIED_MODE=='ROLLBACK' and cfg.EXECUTION_MODE=='LIVE'
 assert not candidate_c_runtime.live_activation_blockers(cfg,cfg.user_dir)
 assert candidate_c_runtime.backtest_live_parity_evidence()['verified']
 clients={s:OkxClient(s,cfg) for s in ('DOGE/USDT:USDT','SOL/USDT:USDT')}
 exchange=next(iter(clients.values())).exchange
 def coverage():
  def get(n,a):
   r=getattr(exchange,n)(a);assert str(r['code'])=='0';return r['data']
  ps=[p for p in get('private_get_account_positions',{'instType':'SWAP'}) if float(p.get('pos') or 0)]
  oo=get('private_get_trade_orders_algo_pending',{'ordType':'oco','instType':'SWAP'})
  assert not get('private_get_trade_orders_pending',{'instType':'SWAP'}),'regular_order_pending'
  for p in ps:
   matches=[o for o in oo if o['instId']==p['instId'] and o['side']==('buy' if float(p['pos'])<0 else 'sell') and str(o.get('reduceOnly')).lower()=='true' and o.get('state')=='live']
   assert len(matches)==1 and float(matches[0]['sz'])==abs(float(p['pos'])),'coverage'
   assert float(matches[0]['slTriggerPx'])>0 and float(matches[0]['tpTriggerPx'])>0
  return dict(positions=[{k:p.get(k) for k in ('instId','pos','posId','avgPx','cTime')} for p in ps],
              oco=[{k:o.get(k) for k in ('instId','algoId','algoClOrdId','sz','side','slTriggerPx','tpTriggerPx','state')} for o in oo])
 assert run('systemctl','is-active','autotrader.service')=='active'
 before=coverage()
 if not apply:
  epochs=cem.PositionEpochStore.load(str(Path(cfg.user_dir)/'candidate_c_epoch_store.jsonl'));assert not epochs.integrity_error
  for symbol,client in clients.items():
   if client.fetch_position() is None:continue
   safe=symbol.replace('/','_').replace(':','_')
   ledger=il.IntentLedger.load(str(Path(cfg.user_dir)/('candidate_c_intent_ledger_'+safe+'.jsonl')),cfg.user_dir,symbol)
   print(json.dumps(migrate(cfg,client,ledger,epochs,apply=False)),flush=True)
  known_baseline_failures=["tests/test_analysis_report.py::test_build_report_has_fixed_chatgpt_sections_and_no_secrets","tests/test_analysis_report_adaptive_live_status.py::test_periodic_review_reports_branch_health_and_root_error","tests/test_candidate_c_partial_tp_dashboard.py::test_partial_tp_reason_has_human_readable_label","tests/test_core_manual_entry_cooldown_ui.py::test_manual_entry_after_elapsed_cooldown_bypasses_ai_fresh_bar_only","tests/test_observability_fix_20260927.py::test_dashboard_separates_gpt_reduce_proposal_from_execution_gate","tests/test_recovery_ui_final.py::test_actual_ui_renders_cost_metadata_without_inventing_unknown_retries","tests/test_recovery_ui_final.py::test_actual_entry_ui_never_renders_approval_as_fill_or_interprets_reason_html","tests/test_operating_observability_ui.py::test_dashboard_renders_explicit_cost_and_analysis_action_labels"]
  baseline=subprocess.run([str(EXPECTED/'.venv/bin/python3'),'-m','pytest','-q','--tb=short',*known_baseline_failures],cwd=EXPECTED,capture_output=True,text=True)
  (PATCH/'baseline-test-output.txt').write_text(baseline.stdout+baseline.stderr)
  failures=failed_nodeids(baseline.stdout)
  assert baseline.returncode==1 and failures==set(known_baseline_failures),'baseline_failure_set_changed'
  result=subprocess.run([str(new/'.venv/bin/python3'),'-m','pytest','-q','tests','--disable-warnings','--tb=short',
    *['--deselect='+node for node in known_baseline_failures]],cwd=new,capture_output=True,text=True)
  (PATCH/'qualification-output.txt').write_text(result.stdout+result.stderr)
  print(result.stdout[-4500:]);assert result.returncode==0,'qualified_suite_failed'
  atomic(PATCH/'qualification.json',json.dumps(dict(verified=True,manifest=manifest,release=str(new),exit=result.returncode,baseline_failure_nodeids=known_baseline_failures,at=time.time())))
  print(json.dumps(dict(preflight=True,release=str(new),positions=len(before['positions']),oco=len(before['oco']))));sys.exit(0)
 qualification=json.loads((PATCH/'qualification.json').read_text())
 assert qualification['verified'] and qualification['manifest']==manifest and qualification['release']==str(new) and qualification['exit']==0
 assert time.time()-qualification['at']<3600,'qualification_expired'
 assert not (PATCH/'deployment-record.json').exists(),'already_attempted'
 assert not Path('/var/lib/autotrader/deployment-resume/pending.json').exists(),'pending_resume_exists'
 for prop in ('ExecStop','ExecStopPost','TriggeredBy'):assert not run('systemctl','show','autotrader.service','--value','-p',prop),prop
 backup=Path('/var/lib/autotrader/deployment-backups')/new.name;backup.mkdir(mode=0o700)
 atomic(backup/'settings-original.env',settings.read_text(),st.st_uid,st.st_gid)
 record=dict(previous=str(EXPECTED),release=str(new),settings_sha=settings_sha,manifest=manifest,at=time.time(),migrations=[])
 atomic(PATCH/'deployment-record.json',json.dumps(record))
 stopped=False;switched=False
 try:
  run('systemctl','stop','autotrader.service');stopped=True
  with ownership.account_order_lock(cfg.user_dir):
   assert sha(settings)==settings_sha,'settings_changed'
   before=coverage();atomic(backup/'before.json',json.dumps(before))
   ep=Path(cfg.user_dir)/'candidate_c_epoch_store.jsonl';shutil.copy2(ep,backup/ep.name)
   epochs=cem.PositionEpochStore.load(str(ep));assert not epochs.integrity_error
   for symbol,client in clients.items():
    position=client.fetch_position()
    if position is None:continue
    safe=symbol.replace('/','_').replace(':','_')
    ledger=il.IntentLedger.load(str(Path(cfg.user_dir)/('candidate_c_intent_ledger_'+safe+'.jsonl')),cfg.user_dir,symbol)
    migration=migrate(cfg,client,ledger,epochs,journal=lambda row:atomic(PATCH/('migration-'+safe+'.json'),json.dumps(row)))
    record['migrations'].append(migration);atomic(PATCH/'deployment-record.json',json.dumps(record));print(json.dumps(migration),flush=True)
   after=coverage();atomic(backup/'after.json',json.dumps(after))
   assert sha(settings)==settings_sha,'settings_changed'
   link=Path('/opt/.autotrader-compact-v19');assert not link.exists();link.symlink_to(new);os.replace(link,LINK);switched=True
  resume(new,st);run('systemctl','start','autotrader.service');stopped=False
  assert run('systemctl','is-active','autotrader.service')=='active'
  record.update(status='switched_pending_runtime_verification',pid=run('systemctl','show','autotrader.service','-p','MainPID','--value'))
  atomic(PATCH/'deployment-record.json',json.dumps(record));print(json.dumps(record|{'manifest_count':len(manifest),'manifest':None}))
 except Exception as exc:
  record.update(status='failed',error=str(exc),switched=switched);atomic(PATCH/'deployment-record.json',json.dumps(record))
  if stopped:
   resume(new if switched else EXPECTED,st,candidate=False);run('systemctl','start','autotrader.service')
  raise
