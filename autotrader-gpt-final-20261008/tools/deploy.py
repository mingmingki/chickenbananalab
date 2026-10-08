"""Native operator deployment. Preserves shared paths; never calls trade/stop APIs."""
import os,sys,json,time,hashlib,shutil,subprocess,tarfile,tempfile,datetime
from pathlib import Path
PATCH=Path('/tmp/gpt-recovery-qualified-patch');LINK=Path('/opt/autotrader')
PREVIOUS=Path('/opt/autotrader-releases/early_entry_risk_rr_repair_v4_20261008T0156KST')
STATE=Path('/var/lib/autotrader/deployment-backups/core-gpt-recovery-20261008')
MANIFEST=json.loads((PATCH/'changed-files.json').read_text())
RUNTIME=json.loads(Path('/tmp/gpt-recovery-pre-runtime-final.json').read_text())
assert RUNTIME['core']['running'] is True
assert RUNTIME['candidate_c']['engine_effective_mode']=='live'
def run(*args):return subprocess.check_output(args,text=True).strip()
def atomic(path,data,mode=0o600,uid=None,gid=None):
 fd,name=tempfile.mkstemp(prefix='.gpt-recovery-',dir=path.parent)
 try:
  with os.fdopen(fd,'w') as f:f.write(data);f.flush();os.fsync(f.fileno())
  os.chmod(name,mode)
  if uid is not None:os.chown(name,uid,gid)
  os.replace(name,path)
 finally:
  if os.path.exists(name):os.unlink(name)
def switch(release):
 path=Path('/opt/.autotrader-gpt-switch');assert not path.exists();path.symlink_to(release);os.replace(path,LINK)
def resume(release):
 p=Path('/var/lib/autotrader/deployment-resume');p.mkdir(parents=True,exist_ok=True)
 st=(LINK/'users/chickenbananalab').stat();os.chown(p,st.st_uid,st.st_gid);p.chmod(0o700)
 now=time.time();data=dict(release=str(release),created_at=now,expires_at=now+600,username='chickenbananalab',core=True,core_mode='ROLLBACK',candidate_c='live')
 atomic(p/'pending.json',json.dumps(data),uid=st.st_uid,gid=st.st_gid)
def rollback():
 record=json.loads((STATE/'private-state.json').read_text())
 run('systemctl','stop','autotrader.service')
 p=Path(record['settings_path']);atomic(p,(STATE/'account.env').read_text(),record['settings_mode'],record['uid'],record['gid'])
 switch(Path(record['previous']));resume(Path(record['previous']));run('systemctl','start','autotrader.service')
 print(json.dumps({'operation':'rollback','release':str(LINK.resolve()),'pid':run('systemctl','show','autotrader.service','-p','MainPID','--value')}),flush=True)
if '--rollback' in sys.argv:rollback();sys.exit(0)
assert LINK.resolve()==PREVIOUS
assert run('systemctl','is-active','autotrader.service')=='active'
for name,hashes in MANIFEST.items():
 old=LINK/name
 if hashes['baseline'] is None:assert not old.exists(),name
 else:assert hashlib.sha256(old.read_bytes()).hexdigest()==hashes['baseline'],name
 assert hashlib.sha256((PATCH/'source'/name).read_bytes()).hexdigest()==hashes['candidate'],name
sys.path.insert(0,str(LINK));os.environ['AUTOTRADER_PROJECT_DIR']=str(LINK)
import config,accounts,core_unified_service,candidate_c_runtime
cfg=config.UserConfig(accounts.user_dir('chickenbananalab'))
assert accounts.is_approved('chickenbananalab')
assert cfg.CORE_UNIFIED_MODE=='ROLLBACK' and cfg.EXECUTION_MODE=='LIVE'
assert cfg.GPT_ENTRY_GATE_ENABLED and not cfg.CORE_EVENT_AI_ENABLED and not cfg.HOLD_AUDIT_ENABLED
assert all(core_unified_service.owner(cfg,s)=='legacy' for s in cfg.ENABLED_SYMBOLS)
assert not candidate_c_runtime.live_activation_blockers(cfg,cfg.user_dir)
assert STATE.exists() is False,'backup exists: inspect before applying'
STATE.mkdir(parents=True,mode=0o700);STATE.chmod(0o700)
settings=Path(cfg.user_dir)/'.env';st=settings.stat();before=settings.read_text()
record=dict(previous=str(PREVIOUS),settings_path=str(settings.resolve()),settings_mode=st.st_mode&0o777,uid=st.st_uid,gid=st.st_gid,created_at=time.time())
atomic(STATE/'account.env',before)
atomic(STATE/'private-state.json',json.dumps(record))
with tarfile.open(STATE/'release-and-settings.tar.gz','w:gz') as tar:
 tar.add(PREVIOUS,arcname='release',recursive=True)
 for p in (Path('/etc/systemd/system/autotrader.service'),Path('/etc/systemd/system/autotrader.service.d'),Path('/var/lib/autotrader/shared/.env'),Path('/etc/autotrader/anchor.env'),settings,LINK/'accounts.json'):
  if p.exists():tar.add(p.resolve(),arcname='private-settings/'+str(p).lstrip('/'))
os.chmod(STATE/'release-and-settings.tar.gz',0o600)
new=Path('/opt/autotrader-releases')/('core_gpt_recovery_v5_'+datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).strftime('%Y%m%dT%H%M%SKST'))
shutil.copytree(PREVIOUS,new,symlinks=True,ignore=shutil.ignore_patterns('__pycache__','.pytest_cache'))
for name in MANIFEST:
 target=new/name;target.parent.mkdir(parents=True,exist_ok=True);assert not target.is_symlink(),name
 shutil.copy2(PATCH/'source'/name,target)
for p in PREVIOUS.iterdir():
 if p.is_symlink():assert (new/p.name).is_symlink() and os.readlink(new/p.name)==os.readlink(p),p.name
run(str(new/'.venv/bin/python'),'-m','compileall','-q',str(new))
record['new_release']=str(new);atomic(STATE/'private-state.json',json.dumps(record))
# Update only the three authorized keys while preserving every other line.
flags={'GPT_ENTRY_GATE_ENABLED':'true','CORE_GPT_ENTRY_TIMEOUT_BYPASS':'true','CORE_PAID_SHADOW_ENABLED':'false'}
seen=set();lines=[]
for line in before.splitlines():
 key=line.partition('=')[0].strip()
 if key in flags and not line.lstrip().startswith('#'):
  if key not in seen:lines.append(key+'='+flags[key])
  seen.add(key)
 else:lines.append(line)
lines.extend(k+'='+v for k,v in flags.items() if k not in seen)
after='\n'.join(lines)+'\n'
try:
 run('systemctl','stop','autotrader.service')
 atomic(settings,after,record['settings_mode'],st.st_uid,st.st_gid)
 switch(new);resume(new);run('systemctl','start','autotrader.service')
 assert run('systemctl','is-active','autotrader.service')=='active'
 print(json.dumps({'operation':'deployed','release':str(LINK.resolve()),'pid':run('systemctl','show','autotrader.service','-p','MainPID','--value'),'trader_sha256':hashlib.sha256((LINK/'trader.py').read_bytes()).hexdigest(),'private_backup_created':True,'changed_files':len(MANIFEST),'settings':flags}),flush=True)
except BaseException:
 rollback();raise
