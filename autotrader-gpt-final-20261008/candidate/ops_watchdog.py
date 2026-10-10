"""Independent read-only watchdog. It never submits, changes, or cancels orders."""
from __future__ import annotations
import json
import os
import subprocess
import sys
from pathlib import Path
import datetime as dt

PROJECT=Path(os.environ.get("AUTOTRADER_PROJECT_DIR","/opt/autotrader"))
USER=os.environ.get("AUTOTRADER_OBS_USER","chickenbananalab")
sys.path.insert(0,str(PROJECT))
import config
import ops_observability as obs
import trade_learning_cache
import telegram_notify

def _run(*args,timeout=12):
    return subprocess.run(args,text=True,capture_output=True,timeout=timeout,check=False)

def _journal(since="-24 hours"):
    r=_run("journalctl","-u","autotrader.service","--since",since,"--no-pager","-o","cat",timeout=15)
    return r.stdout if r.returncode==0 else ""

def _live_state():
    """Read the current authenticated dashboard state; no exchange writes."""
    try:
        from flask import Flask
        import requests
        app=Flask("ops_watchdog_readonly")
        app.secret_key=(PROJECT/"flask_secret.key").read_text().strip()
        cookie=app.session_interface.get_signing_serializer(app).dumps(
            {"authenticated":True,"username":USER})
        r=requests.get("http://127.0.0.1:8080/api/state",
                       cookies={"session":cookie},timeout=20)
        r.raise_for_status()
        value=r.json()
        return value if isinstance(value,dict) else None
    except Exception as exc:
        print("state_api_warning",type(exc).__name__,file=sys.stderr)
        return None

def _service_active():
    r=_run("systemctl","is-active","autotrader.service",timeout=5)
    return r.returncode==0 and r.stdout.strip()=="active"

def _fingerprint(issue):
    # Incident identity is stable. A changing numeric percentage or count is
    # data on an ongoing incident, not a recovered event followed by a new one.
    return "|".join(str(issue.get(k) or "") for k in ("code","symbol"))


def _saved_fingerprint(value):
    # Existing alert_state persisted the old code|symbol|detail format.
    # Migrate without sending a phantom recovery/new warning on deployment.
    parts=str(value).split("|",2)
    return "|".join((parts[0], parts[1] if len(parts)>1 else ""))

def _atomic_json(path,value):
    path=Path(path); tmp=path.with_suffix(path.suffix+".tmp")
    tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    os.replace(tmp,path)

def _notify_changes(user_dir,snapshot):
    state_path=Path(user_dir)/obs.ALERT_STATE_FILE
    old=obs._json(state_path,{}) or {}
    old_active={_saved_fingerprint(v) for v in (old.get("active") or [])}
    issues=list((snapshot.get("health") or {}).get("issues") or [])
    current={_fingerprint(x):x for x in issues}
    current_keys=set(current)
    new=sorted(current_keys-old_active)
    recovered=sorted(old_active-current_keys)
    if new or recovered:
        cfg=config.UserConfig(str(user_dir))
        lines=["⚠️ 자동매매 운영 감시"]
        for key in new:
            x=current[key]
            lines.append(f"신규 {x.get('severity','warning').upper()}: {x.get('code')} {x.get('symbol') or ''} {x.get('detail') or ''}".strip())
        for key in recovered:
            code=key.split("|",1)[0]
            lines.append(f"복구: {code}")
        try:
            telegram_notify.send(cfg,"\n".join(lines))
        except Exception as exc:
            print("telegram_warning",type(exc).__name__,file=sys.stderr)
    _atomic_json(state_path,{"active":sorted(current_keys),"updated_at":snapshot.get("generated_at")})

def main():
    user_dir=PROJECT/"users"/USER
    service_active=_service_active()
    # Keep observability statistics fresh without appending learning hypotheses or
    # gaining any trading authority. Heavy analysis only reruns when source fingerprint changes.
    analysis_cache=trade_learning_cache.run_analysis(str(user_dir),update_hypotheses=False)
    snapshot=obs.build_snapshot(
        str(user_dir),
        journal_text=_journal("-24 hours"),
        recent_journal_text=_journal("-20 minutes"),
        service_active=service_active,
        live_state=_live_state() if service_active else None,
        now=dt.datetime.now(obs.KST),
    )
    obs.save_snapshot(str(user_dir),snapshot)
    _notify_changes(user_dir,snapshot)
    print(json.dumps({"ok":True,"generated_at":snapshot["generated_at"],
                      "issues":snapshot["health"]["issue_count"],
                      "critical":snapshot["health"]["critical_count"],
                      "analysis_cache_reused":bool(analysis_cache.get("reused_cache")),
                      "analysis_generated_at":analysis_cache.get("generated_at")},ensure_ascii=False))
    return 0

if __name__=="__main__":
    raise SystemExit(main())
