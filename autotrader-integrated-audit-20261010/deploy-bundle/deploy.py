"""Apply the reviewed v6 patch; shared settings and exchange orders remain untouched."""
import os, sys, json, time, hashlib, tempfile, shutil, subprocess, datetime, sqlite3, fcntl
from pathlib import Path

LINK = Path('/opt/autotrader')
EXPECTED = Path('/opt/autotrader-releases/core_gemini_gpt_sltp_v7_20261010')
PATCH = Path(sys.argv[1])
lock_path=Path('/var/lib/autotrader/deployment-backups/.integrated-audit-deploy.lock')
lock_path.parent.mkdir(parents=True,exist_ok=True)
lock_file=lock_path.open('a')
fcntl.flock(lock_file,fcntl.LOCK_EX|fcntl.LOCK_NB)
assert not (PATCH/'deployment-record.json').exists() or '--rollback' in sys.argv, 'already attempted; inspect record, do not duplicate'
MANIFEST = json.loads((PATCH/'manifest.json').read_text())
STATE = Path('/var/lib/autotrader/deployment-backups') / ('integrated-audit-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
def run(*args):
    return subprocess.check_output(args, text=True).strip()
def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()
def atomic(path, text, uid=None, gid=None):
    fd, name = tempfile.mkstemp(prefix='.v6-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as f:
            f.write(text); f.flush(); os.fsync(f.fileno())
        os.chmod(name, 0o600)
        if uid is not None: os.chown(name, uid, gid)
        os.replace(name, path)
    finally:
        if os.path.exists(name): os.unlink(name)
def switch(release):
    path=Path('/opt/.autotrader-v6-switch')
    assert not path.exists(), 'stale switch path requires inspection'
    path.symlink_to(release); os.replace(path, LINK)
def resume(release, uid, gid):
    path=Path('/var/lib/autotrader/deployment-resume')
    path.mkdir(parents=True,exist_ok=True); path.chmod(0o700); os.chown(path,uid,gid)
    now=time.time()
    atomic(path/'pending.json',json.dumps(dict(release=str(release),created_at=now,expires_at=now+600,
        username='chickenbananalab',core=True,core_mode='ROLLBACK',candidate_c='live')),uid,gid)
if '--rollback' in sys.argv:
    record=json.loads((PATCH/'deployment-record.json').read_text())
    run('systemctl','stop','autotrader.service'); switch(Path(record['previous']))
    resume(Path(record['previous']),record['uid'],record['gid'])
    run('systemctl','start','autotrader.service')
    assert run('systemctl','is-active','autotrader.service')=='active'
    print(json.dumps(dict(operation='rolled_back',release=str(LINK.resolve()),pid=run('systemctl','show','autotrader.service','-p','MainPID','--value'))));sys.exit(0)
assert LINK.is_symlink() and LINK.resolve()==EXPECTED, 'production changed; compare before deployment'
assert run('systemctl','is-active','autotrader.service')=='active'
for prop in ('ExecStop','ExecStopPost','TriggeredBy'):
    assert not run('systemctl','show','autotrader.service','--value','-p',prop),prop
assert not Path('/var/lib/autotrader/deployment-resume/pending.json').exists(),'pending existing deployment'
assert MANIFEST and isinstance(MANIFEST,dict)
for name,h in MANIFEST.items():
    rel=Path(name)
    assert not rel.is_absolute() and '..' not in rel.parts and rel.parts[0] not in ('users','logs','.venv'),name
    assert name not in ('.env','accounts.json','flask_secret.key','vapid_private_key.pem'),name
    target=LINK/rel
    assert not target.is_symlink(),name
    assert (sha(target) if target.exists() else None)==h['baseline'],name
    assert sha(PATCH/'source'/rel)==h['candidate'],name
settings=LINK/'users/chickenbananalab/.env'; original_settings_sha=sha(settings); st=settings.stat()
new=Path('/opt/autotrader-releases')/('integrated_audit_v8_'+datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).strftime('%Y%m%dT%H%M%SKST'))
assert not new.exists()
shutil.copytree(EXPECTED,new,symlinks=True,ignore=shutil.ignore_patterns('__pycache__','.pytest_cache'))
for name in MANIFEST:
    dest=new/name;dest.parent.mkdir(parents=True,exist_ok=True)
    assert not dest.is_symlink(),name; shutil.copy2(PATCH/'source'/name,dest)
for p in EXPECTED.iterdir():
    if p.is_symlink(): assert (new/p.name).is_symlink() and os.readlink(new/p.name)==os.readlink(p),p.name
identity=dict(release=str(new),created_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),previous_release=str(EXPECTED))
(new/'DEPLOYMENT_IDENTITY.json').write_text(json.dumps(identity,indent=2)+'\n')
run(str(new/'.venv/bin/python'),'-m','py_compile',*[str(new/name) for name in MANIFEST if name.endswith('.py')])
# Validate activation against the NEW code and its parity evidence before stopping LIVE.
sys.path.insert(0,str(new));os.environ['AUTOTRADER_PROJECT_DIR']=str(new)
import config,accounts,core_unified_service,candidate_c_runtime
cfg=config.UserConfig(accounts.user_dir('chickenbananalab'))
assert accounts.is_approved('chickenbananalab')
assert cfg.CORE_UNIFIED_MODE=='ROLLBACK' and cfg.EXECUTION_MODE=='LIVE'
assert cfg.GPT_ENTRY_GATE_ENABLED and cfg.CORE_GPT_ENTRY_TIMEOUT_BYPASS and not cfg.CORE_PAID_SHADOW_ENABLED
assert all(core_unified_service.owner(cfg,s)=='legacy' for s in cfg.ENABLED_SYMBOLS)
assert not candidate_c_runtime.live_activation_blockers(cfg,cfg.user_dir)
assert candidate_c_runtime.backtest_live_parity_evidence()['verified']
import requests
from flask import Flask
app=Flask('v6-predeploy');app.secret_key=(LINK/'flask_secret.key').read_text().strip()
session=requests.Session()
session.cookies.set('session',app.session_interface.get_signing_serializer(app).dumps({'username':'chickenbananalab','authenticated':True}))
runtime={}
for key,path in [('core','/api/state'),('candidate_c','/api/candidate_c_state')]:
    response=session.get('http://127.0.0.1:8080'+path,timeout=45)
    response.raise_for_status();runtime[key]=response.json()
assert runtime['core'].get('running') is True,'CORE stopped during preparation'
assert runtime['candidate_c'].get('runtime',{}).get('running') is True,'Candidate C stopped during preparation'
assert runtime['candidate_c'].get('engine_effective_mode')=='live'
from okx_client import OkxClient
exchange=OkxClient('BTC/USDT:USDT',cfg).exchange
def preservation():
    def get(name,args):
        response=getattr(exchange,name)(args)
        assert str(response.get('code'))=='0',name
        return response.get('data',[])
    positions=[p for p in get('private_get_account_positions',{'instType':'SWAP'}) if float(p.get('pos') or 0)]
    oco=get('private_get_trade_orders_algo_pending',{'ordType':'oco','instType':'SWAP'})
    pending=get('private_get_trade_orders_pending',{'instType':'SWAP'})
    for p in positions:
        side='sell' if float(p['pos'])>0 else 'buy'
        match=[o for o in oco if o.get('instId')==p['instId'] and o.get('side')==side and str(o.get('reduceOnly')).lower()=='true' and o.get('state')=='live']
        assert len(match)==1 and abs(float(match[0]['sz'])-abs(float(p['pos'])))<1e-9,'protection_coverage'
        assert float(match[0].get('slTriggerPx') or 0)>0 and float(match[0].get('tpTriggerPx') or 0)>0,'SL_TP_missing'
    assert not pending,'pending_regular_order'
    pos_fields=('instId','pos','posSide','posId','avgPx')
    oco_fields=('instId','algoId','side','reduceOnly','sz','slTriggerPx','tpTriggerPx','state')
    return dict(positions=sorted([{k:p.get(k) for k in pos_fields} for p in positions],key=lambda p:p['instId']),
                oco=sorted([{k:o.get(k) for k in oco_fields} for o in oco],key=lambda o:o['algoId']))
before=preservation()
STATE.mkdir(parents=True,mode=0o700);STATE.chmod(0o700)
atomic(STATE/'preservation-before.json',json.dumps(before,indent=2))
record=dict(previous=str(EXPECTED),release=str(new),uid=st.st_uid,gid=st.st_gid,backup=str(STATE),
    settings_sha256=original_settings_sha,manifest=MANIFEST,started_at=time.time())
atomic(STATE/'record.json',json.dumps(record,indent=2)); atomic(PATCH/'deployment-record.json',json.dumps(record,indent=2))
try:
    run('systemctl','stop','autotrader.service')
    assert sha(settings)==original_settings_sha, 'settings changed during preparation'
    assert preservation()==before,'positions_or_protection_changed_before_switch'
    # Private database copies are rollback evidence; never overwrite active shared state.
    for p in Path(cfg.user_dir).iterdir():
        if p.suffix in ('.sqlite','.sqlite3','.db') and p.is_file():
            source=sqlite3.connect('file:'+str(p)+'?mode=ro',uri=True)
            dest=sqlite3.connect(str(STATE/p.name))
            source.backup(dest);dest.close();source.close();(STATE/p.name).chmod(0o600)
    switch(new)
    assert preservation()==before,'positions_or_protection_changed_during_switch'
    resume(new,st.st_uid,st.st_gid);run('systemctl','start','autotrader.service')
    assert run('systemctl','is-active','autotrader.service')=='active'
    assert sha(settings)==original_settings_sha
    for name,h in MANIFEST.items():assert sha(LINK/name)==h['candidate'],name
    for p in new.rglob('*'):
        if p.is_symlink():continue
        if p.is_file():p.chmod(0o444)
        elif p.is_dir():p.chmod(0o555)
    new.chmod(0o555)
    print(json.dumps(dict(operation='deployed',release=str(LINK.resolve()),pid=run('systemctl','show','autotrader.service','-p','MainPID','--value'),
        changed_files=len(MANIFEST),settings_sha_unchanged=True,backup=str(STATE)),ensure_ascii=False),flush=True)
except BaseException:
    run('systemctl','stop','autotrader.service')
    if LINK.resolve()!=EXPECTED:switch(EXPECTED)
    resume(EXPECTED,st.st_uid,st.st_gid);run('systemctl','start','autotrader.service')
    print(json.dumps(dict(operation='rolled_back_after_error',release=str(LINK.resolve()))),flush=True)
    raise
