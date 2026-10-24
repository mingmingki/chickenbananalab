"""Authorized Gemini-only release: stage/test first, switch separately."""
import hashlib,json,os,pwd,shutil,subprocess,sys,tarfile,tempfile,time
from pathlib import Path

def run(*args,**kwargs):
    p=subprocess.run(args,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,**kwargs)
    if p.returncode: raise RuntimeError(p.stdout[-12000:])
    return p.stdout.strip()

def verify_source(root,expected):
    for name,value in expected.items():
        if hashlib.sha256((root/name).read_bytes()).hexdigest()!=value:
            raise RuntimeError('source_changed:'+name)

def prepare(archive,sha):
    archive=Path(archive)
    if hashlib.sha256(archive.read_bytes()).hexdigest()!=sha: raise RuntimeError('archive_checksum')
    old=Path('/opt/autotrader').resolve(strict=True)
    release=Path('/opt/autotrader-releases')/time.strftime('core_restore_%Y%m%dT%H%M%SZ',time.gmtime())
    shutil.copytree(old,release,symlinks=True,ignore=shutil.ignore_patterns('__pycache__','.pytest_cache'))
    with tarfile.open(archive) as bundle:
        for member in bundle.getmembers():
            p=Path(member.name)
            if p.is_absolute() or '..' in p.parts or not member.isfile() or not (release/p).resolve().is_relative_to(release.resolve()):
                raise RuntimeError('unsafe_archive_member')
        bundle.extractall(release)
    manifest=json.loads((release/'release_manifest.json').read_text())
    verify_source(old,manifest['expected']);verify_source(release,manifest['updated'])
    python=str(release/'.venv/bin/python3')
    run(python,'-m','py_compile',*[str(release/n) for n in manifest['updated'] if n.endswith('.py')])
    with tempfile.TemporaryDirectory(prefix='core-settings-test-') as directory:
        env=dict(os.environ,AUTOTRADER_PROJECT_DIR=directory,PYTHONPATH=str(release),PYTHONDONTWRITEBYTECODE='1')
        print(run(python,'-m','unittest','discover','-s',str(release/'rollback_checks'),'-q',cwd=release,env=env))
        # Render without account secrets and check frontend JavaScript separately locally.
        render="from jinja2 import Environment,FileSystemLoader; e=Environment(loader=FileSystemLoader('templates')); s=e.get_template('dashboard.html').render(username='test',is_admin=False,symbols=['BTC/USDT:USDT']); assert 'position-ai-review-enabled' in s and 'hold-audit-enabled' not in s; print('dashboard render OK')"
        print(run(python,'-c',render,cwd=release,env=env))
        schema="from google.genai import types; from core_unified_adapters import GEMINI_REVIEW_SCHEMA, GEMINI_EVENT_SCHEMA; types.Schema.model_validate(GEMINI_EVENT_SCHEMA); s=types.Schema.model_validate(GEMINI_REVIEW_SCHEMA); c=types.GenerateContentConfig(response_mime_type='application/json',response_schema=s); assert 'close' in c.response_schema.properties['action'].enum; print('Gemini SDK schema OK')"
        print(run(python,'-c',schema,cwd=release,env=env))
    user=pwd.getpwnam('bagmingi')
    for directory,dirs,files in os.walk(release,followlinks=False):
        for p in [Path(directory)]+[Path(directory)/n for n in dirs+files]:
            if not p.is_symlink(): os.chown(p,user.pw_uid,user.pw_gid)
    for name in ('.venv','users','logs','.env','accounts.json','flask_secret.key','vapid_private_key.pem','trades_log.jsonl','pnl_state.json'):
        if not (old/name).is_symlink() or os.readlink(old/name)!=os.readlink(release/name):
            raise RuntimeError('shared_path_changed:'+name)
    (release/'GEMINI_DEPLOYMENT.json').write_text(json.dumps(dict(previous_release=str(old),archive_sha256=sha,prepared_at=time.time())))
    print(json.dumps(dict(prepared_release=str(release),service_unchanged=True)))

def apply(release):
    release=Path(release).resolve(strict=True);active=Path('/opt/autotrader');old=active.resolve(strict=True)
    deployment=json.loads((release/'GEMINI_DEPLOYMENT.json').read_text())
    if deployment['previous_release']!=str(old): raise RuntimeError('active_release_changed')
    manifest=json.loads((release/'release_manifest.json').read_text())
    verify_source(old,manifest['expected']);verify_source(release,manifest['updated'])
    for prop in ('ExecStop','ExecStopPost','TriggeredBy'):
        if run('systemctl','show','autotrader.service','--value','-p',prop): raise RuntimeError('unexpected_service_hook')
    if run('systemctl','show','autotrader.service','--value','-p','ActiveState')!='active': raise RuntimeError('service_not_active')
    user_dir=Path('/var/lib/autotrader/users/chickenbananalab')
    # Resume only the Candidate C loops verified active in the service being replaced.
    pid=int(run('systemctl','show','autotrader.service','--value','-p','MainPID'))
    for name in ('DOGE','SOL'):
        r=json.loads((user_dir/f'candidate_c_runtime_{name}_USDT_USDT.json').read_text())
        if r.get('pid')!=pid or r.get('status')!='RUNNING' or not 0<=time.time()-r['heartbeat_at']<120:
            raise RuntimeError('candidate_runtime_changed:'+name)
    env=user_dir/'.env';backup=env.with_name('.env.before_'+release.name);shutil.copy2(env,backup)
    preserve_settings=manifest.get('preserve_settings',manifest.get('presentation_only')) is True
    preserved_settings=env.read_bytes()
    if preserve_settings:
        values=dict(line.partition('=')[::2] for line in env.read_text().splitlines() if '=' in line)
        if any(values.get(key,'').strip().lower()!='false' for key in ('GPT_ENTRY_GATE_ENABLED','HOLD_AUDIT_ENABLED')):
            raise RuntimeError('resume_settings_changed')
    import symbol_entry_control
    before=json.loads((user_dir/'core_rollback_entry_pause_backup.json').read_text())
    records={s:dict(before=r,during=symbol_entry_control.get_status(str(user_dir),s)) for s,r in before.items()}
    pause_manifest=user_dir/'core_rollback_pause_manifest.json'
    pause_manifest.write_text(json.dumps(records))
    st=env.stat();os.chown(pause_manifest,st.st_uid,st.st_gid)
    # SIGTERM does not invoke manual close; exchange protection stays installed.
    run('systemctl','stop','autotrader.service')
    if run('systemctl','show','autotrader.service','--value','-p','MainPID')!='0': raise RuntimeError('service_still_running')
    try:
        if not preserve_settings:
            updates={'GPT_ENTRY_GATE_ENABLED':'true','HOLD_AUDIT_ENABLED':'false','CORE_EVENT_AI_ENABLED':'false','CORE_UNIFIED_MODE':'ROLLBACK','POLL_INTERVAL_SECONDS':'300','MIN_HOLD_MINUTES':'15'}
            lines=[line for line in env.read_text().splitlines() if line.partition('=')[0].strip() not in updates]
            lines.extend(k+'='+v for k,v in updates.items())
            temporary=env.with_name('.env.gemini_staged');shutil.copy2(env,temporary)
            temporary.write_text('\n'.join(lines)+'\n');st=env.stat();os.chown(temporary,st.st_uid,st.st_gid);os.replace(temporary,env)
        elif env.read_bytes()!=preserved_settings:
            raise RuntimeError('settings_changed_during_restart')
        if manifest.get('activate_event_ai') is True:
            # Only the explicitly authorized new flag changes; every other setting is retained.
            lines=env.read_text().splitlines()
            lines=[line for line in lines if line.partition('=')[0].strip()!='CORE_EVENT_AI_ENABLED']
            lines.append('CORE_EVENT_AI_ENABLED=true')
            temporary=env.with_name('.env.event_staged');shutil.copy2(env,temporary)
            temporary.write_text('\n'.join(lines)+'\n');st=env.stat();os.chown(temporary,st.st_uid,st.st_gid);os.replace(temporary,env)
        directory=Path('/var/lib/autotrader/deployment-resume');directory.mkdir(exist_ok=True)
        user=pwd.getpwnam('bagmingi');os.chown(directory,user.pw_uid,user.pw_gid);directory.chmod(0o700)
        pending=directory/'pending.json'
        if pending.exists(): raise RuntimeError('resume_already_pending')
        now=time.time();pending.write_text(json.dumps(dict(release=str(release),username='chickenbananalab',
            core=True,core_mode='ROLLBACK',candidate_c='live',created_at=now,expires_at=now+600)))
        os.chown(pending,user.pw_uid,user.pw_gid);pending.chmod(0o600)
        link=active.with_name('autotrader.next')
        if link.exists() or link.is_symlink(): raise RuntimeError('deployment_link_exists')
        link.symlink_to(release);os.replace(link,active)
        run('systemctl','start','autotrader.service')
        deployment.update(applied_at=time.time(),config_backup=str(backup),resume='requested')
        (release/'GEMINI_DEPLOYMENT.json').write_text(json.dumps(deployment,indent=2))
        print(json.dumps(dict(release=str(release),service=run('systemctl','show','autotrader.service','--value','-p','ActiveState'),
                             manual_close_called=False)))
    except Exception:
        # Keep evidence and surface failure; never launch a second trading loop blindly.
        print(json.dumps(dict(deployment_error=True,active_release=str(active.resolve()),backup=str(backup))))
        raise

if __name__=='__main__':
    if sys.argv[1]=='prepare': prepare(sys.argv[2],sys.argv[3])
    elif sys.argv[1]=='apply': apply(sys.argv[2])
    else: raise ValueError('command')
