"""One explicitly invoked deployment, preserving the approved 1% configuration.

The old v33-69 operator gate pins 0.5%; it cannot be used after the user's
1% setting change. This procedure changes no trading or risk configuration.
Run from the staged release with --apply only after the printed dry run.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

OLD=Path('/opt/autotrader-releases/dashboard_refresh_v33_114_20261024KST')
NEW=Path('/opt/autotrader-releases/live_close_refresh_v33_115_20261025KST')
LINK=Path('/opt/autotrader')
PENDING=Path('/var/lib/autotrader/deployment-resume/pending.json')
RESULT=PENDING.with_name('result.json')
USER=LINK/'users/chickenbananalab'
EVIDENCE=Path('/var/lib/autotrader/deployment-evidence/live_close_refresh_v33_115_20261025KST')


def run(*args):
    return subprocess.check_output(args,text=True,timeout=20).strip()


def write_intent(data):
    fd=os.open(PENDING,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as stream:
        json.dump(data,stream);stream.flush();os.fsync(stream.fileno())


def switch(target):
    temporary=Path('/opt/.autotrader_v33_115_swap')
    assert not temporary.exists() and not temporary.is_symlink()
    run('sudo','-n','ln','-s',str(target),str(temporary))
    run('sudo','-n','mv','-Tf',str(temporary),str(LINK))
    run('sudo','-n','systemctl','restart','autotrader')


def wait_healthy(target,intent,old_pid):
    deadline=time.monotonic()+100
    while time.monotonic()<deadline:
        try:
            pid=int(run('systemctl','show','autotrader','-p','MainPID','--value'))
            result=json.loads(RESULT.read_text())
            current=json.loads(runtime.PATH.read_text())
            if pid!=old_pid and result.get('release')==str(target) and result.get('success') is True:
                runtime.validate_checkpoint(current,current_pid=pid,current_release=target)
                assert current['core_running'] is intent['core']
                assert current['candidate_running'] is bool(intent['candidate_c'])
                assert result['requested_core'] is intent['core']
                assert result['requested_candidate'] is bool(intent['candidate_c'])
                assert current['config_sha256']==intent['config_sha256']
                assert LINK.resolve()==target
                assert run('curl','-s','--max-time','3','-o','/dev/null','-w','%{http_code}','http://127.0.0.1:8080/') in ('200','302')
                return dict(pid=pid,runtime=current,resume=result)
        except (OSError,ValueError,KeyError,AssertionError,subprocess.SubprocessError):
            pass
        time.sleep(2)
    raise RuntimeError('release_or_engine_health_not_verified')


if __name__=='__main__':
    assert Path.cwd().resolve()==NEW
    sys.path.insert(0,str(NEW))
    import accounts
    import config
    import deployment_runtime_v33_69 as runtime
    import deployment_resume
    from deployment_preflight_v33_69 import exchange_preflight
    apply=sys.argv[1:]==['--apply']
    assert sys.argv[1:] in ([],['--apply'])
    assert LINK.resolve()==OLD
    assert not PENDING.exists()
    old_pid=int(run('systemctl','show','autotrader','-p','MainPID','--value'))
    checkpoint=json.loads(runtime.PATH.read_text())
    runtime.validate_checkpoint(checkpoint,current_pid=old_pid,current_release=OLD)
    cfg=config.UserConfig(str(USER));cfg.validate()
    assert accounts.is_approved('chickenbananalab')
    assert cfg.EXECUTION_MODE=='LIVE'
    assert (float(cfg.RISK_PER_TRADE_PCT),float(cfg.CANDIDATE_C_RISK_PER_TRADE_PCT),
            int(cfg.LEVERAGE),int(cfg.CANDIDATE_C_LEVERAGE))==(1.,1.,5,5)
    assert (float(cfg.MAX_DAILY_LOSS_PCT),float(cfg.CANDIDATE_C_MAX_DAILY_LOSS_PCT))==(5.,10.)
    assert cfg.ENABLED_SYMBOLS==checkpoint['core_symbols']
    assert cfg.CANDIDATE_C_SYMBOLS==checkpoint['candidate_symbols']
    if checkpoint['candidate_running']:
        assert cfg.CANDIDATE_C_ENABLED
        if checkpoint['candidate_mode']=='live':
            import candidate_c_runtime
            assert cfg.CANDIDATE_C_LIVE_EXECUTE
            assert not candidate_c_runtime.live_activation_blockers(cfg,user_dir=str(USER))
    env_hash=hashlib.sha256((USER/'.env').read_bytes()).hexdigest()
    assert env_hash==checkpoint['config_sha256']
    protected_count=exchange_preflight(cfg)
    final=json.loads(runtime.PATH.read_text())
    runtime.validate_checkpoint(final,current_pid=old_pid,current_release=OLD)
    for key in ('config_sha256','core_requested','candidate_requested','core_running','candidate_running','candidate_mode'):
        assert checkpoint[key]==final[key]
    for name in ('config.py','candidate_c_trader_adapter.py','core_trend_simplify_v33_106.py'):
        assert (NEW/name).read_bytes()==(OLD/name).read_bytes()
    created=time.time()
    intent=dict(release=str(NEW),created_at=created,expires_at=created+240,
                username='chickenbananalab',core=bool(final['core_running']),
                core_mode=cfg.CORE_UNIFIED_MODE,
                candidate_c=final['candidate_mode'] if final['candidate_running'] else None,
                config_sha256=env_hash)
    assert deployment_resume.settings_match(intent,cfg)
    assert old_pid==int(run('systemctl','show','autotrader','-p','MainPID','--value'))
    report=dict(stage='preflight',apply=apply,old_pid=old_pid,protected_positions=protected_count,
                core=intent['core'],candidate_c=intent['candidate_c'],risk_pct=1.,config_sha256=env_hash)
    print(json.dumps(report),flush=True)
    if not apply:sys.exit(0)
    marker=USER/'core_ai_plan_shadow_v33_112.enabled'
    assert marker.exists(), 'existing_observer_marker_missing'
    switched=False
    try:
        assert hashlib.sha256((USER/'.env').read_bytes()).hexdigest()==env_hash
        write_intent(intent)
        assert LINK.resolve()==OLD
        switch(NEW);switched=True
        health=wait_healthy(NEW,intent,old_pid)
        assert run('curl','-s','--max-time','3','-o','/dev/null','-w','%{http_code}',
                   'http://127.0.0.1:8080/api/ops/core-ai-plan-shadow-v33-112')=='401'
        assert hashlib.sha256((USER/'.env').read_bytes()).hexdigest()==env_hash
        report.update(stage='deployed',release=str(NEW),new_pid=health['pid'],
                      core_running=health['runtime']['core_running'],
                      candidate_running=health['runtime']['candidate_running'])
        (EVIDENCE/'deployment_result.json').write_text(json.dumps(report,indent=2))
        print(json.dumps(report),flush=True)
    except Exception:
        if LINK.resolve()==NEW:
            if PENDING.exists():
                pending=json.loads(PENDING.read_text())
                assert pending.get('release')==str(NEW)
                PENDING.rename(EVIDENCE/'failed_pending.json')
            rollback=dict(intent,release=str(OLD),created_at=time.time(),expires_at=time.time()+240)
            write_intent(rollback)
            before=int(run('systemctl','show','autotrader','-p','MainPID','--value'))
            switch(OLD)
            wait_healthy(OLD,rollback,before)
            print('V33_115_ROLLBACK_ENGINES_VERIFIED',flush=True)
        elif PENDING.exists():
            pending=json.loads(PENDING.read_text())
            if pending.get('release')==str(NEW):PENDING.rename(EVIDENCE/'aborted_pending.json')
        raise
