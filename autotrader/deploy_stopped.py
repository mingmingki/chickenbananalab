"""Operator-authorized code deployment only. Never starts a service or calls an exchange."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import time

def run(*args):
    return subprocess.run(args,check=True,text=True,capture_output=True).stdout.strip()

def main():
    archive=Path(sys.argv[1]); expected=sys.argv[2]
    if hashlib.sha256(archive.read_bytes()).hexdigest()!=expected: raise RuntimeError('archive_checksum')
    active=Path('/opt/autotrader'); old=active.resolve(strict=True)
    required={'trader.py':'2e970022e33812e7e05b0ad0b025593810fe3ca2666b09310a49adf08e5a064e',
              'config.py':'fa2aafb329b3b49a127ba8a5bbeba583d3dd156effd508deb80bd0c0a439a3e9'}
    for name,value in required.items():
        if hashlib.sha256((old/name).read_bytes()).hexdigest()!=value:
            raise RuntimeError('baseline_changed:'+name)
    for prop in ('ExecStop','ExecStopPost','TriggeredBy'):
        if run('systemctl','show','autotrader.service','--value','-p',prop):
            raise RuntimeError('unexpected_service_hook:'+prop)
    release=Path('/opt/autotrader-releases')/time.strftime('core_unified_stopped_%Y%m%dT%H%M%SZ',time.gmtime())
    shutil.copytree(old,release,symlinks=True,ignore=shutil.ignore_patterns('__pycache__','.pytest_cache'))
    with tarfile.open(archive) as bundle:
        for member in bundle.getmembers():
            path=Path(member.name)
            if (path.is_absolute() or '..' in path.parts or
                    not (member.isfile() or member.isdir()) or
                    not (release/path).resolve().is_relative_to(release.resolve())):
                raise RuntimeError('unsafe_archive_member')
        # VM's Python predates extraction filters; validate every path/type above.
        bundle.extractall(release)
    # Compilation checks code without importing configuration, clients or the app.
    run(str(release/'.venv/bin/python3'),'-m','py_compile',*[str(p) for p in release.glob('*.py')])
    for name in ('.venv','users','logs','.env','accounts.json','flask_secret.key','vapid_private_key.pem',
                 'trades_log.jsonl','pnl_state.json'):
        if not (old/name).is_symlink() or os.readlink(old/name)!=os.readlink(release/name):
            raise RuntimeError('shared_path_changed:'+name)
    # systemd stop sends SIGTERM; this app has no SIGTERM close handler and no ExecStop.
    run('systemctl','stop','autotrader.service')
    if run('systemctl','show','autotrader.service','--value','-p','ActiveState')!='inactive':
        raise RuntimeError('service_not_inactive')
    if run('systemctl','show','autotrader.service','--value','-p','MainPID')!='0':
        raise RuntimeError('service_process_remains')
    env=Path('/var/lib/autotrader/users/chickenbananalab/.env')
    backup=env.with_name('.env.before_'+release.name)
    shutil.copy2(env,backup)
    lines=env.read_text().splitlines()
    lines=[line for line in lines if line.partition('=')[0].strip()!='CORE_UNIFIED_MODE']
    lines.append('CORE_UNIFIED_MODE=LIVE')
    temporary=env.with_name('.env.unified_staged')
    shutil.copy2(env,temporary); temporary.write_text('\n'.join(lines)+'\n')
    st=env.stat(); os.chown(temporary,st.st_uid,st.st_gid); os.replace(temporary,env)
    link=active.with_name('autotrader.next')
    if link.exists() or link.is_symlink(): raise RuntimeError('deployment_link_exists')
    link.symlink_to(release); os.replace(link,active)
    manifest={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in release.glob('*.py')}
    (release/'UNIFIED_DEPLOYMENT.json').write_text(json.dumps(dict(previous_release=str(old),
        archive_sha256=expected,service='inactive',config_backup=str(backup),source_sha256=manifest),indent=2)+'\n')
    print(json.dumps(dict(release=str(active.resolve()),previous_release=str(old),
        service=run('systemctl','show','autotrader.service','--value','-p','ActiveState'),
        main_pid=run('systemctl','show','autotrader.service','--value','-p','MainPID'),
        trading_started=False,position_close_called=False)))

if __name__=='__main__': main()
