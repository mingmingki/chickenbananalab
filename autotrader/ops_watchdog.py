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
        r=requests.get("http://127.0.0.1:8080/api/watchdog_state",
                       cookies={"session":cookie},timeout=5)
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
    # Health runs independently of historical trade/cost calculations.
    now=dt.datetime.now(obs.KST)
    metrics=obs._json(user_dir/"ops_analysis_metrics.json",{}) or {}
    previous=obs._json(user_dir/obs.SNAPSHOT_FILE,{}) or {}
    health=obs.evaluate_health(
        str(user_dir),service_active=service_active,
        live_state=_live_state() if service_active else None,
        recent_journal_text=_journal("-20 minutes"),now_epoch=now.timestamp())
    # Watchdog intentionally builds this snapshot directly rather than calling
    # ops_observability.build_snapshot(). The v33-10 audit must be surfaced in
    # this actual scheduled-worker entry point, not only its library helper.
    try:
        import execution_quality_audit_v33_10
        trade_execution_audit=execution_quality_audit_v33_10.from_user_dir(
            str(user_dir),now=now,days=7)
    except Exception as exc:
        trade_execution_audit={"status":"UNKNOWN","reason":type(exc).__name__}
    # v33-12: observational CORE profit-floor causality.
    # No additional exchange calls, no order mutation, no performance claim.
    try:
        import core_profit_retention_audit_v33_12
        profit_retention=core_profit_retention_audit_v33_12.from_user_dir(
            str(user_dir),now=now)
    except Exception as exc:
        profit_retention={"status":"UNKNOWN","reason":type(exc).__name__}
    # v33-13: READ-ONLY realized exit expectancy evidence; insufficient proof
    # never generates a trading/AI-control instruction.
    try:
        import core_exit_expectancy_audit_v33_13 as expectancy_v33_13
        expectancy_evidence=expectancy_v33_13.from_user_dir(
            str(user_dir),now=now,days=7)
    except Exception as exc:
        expectancy_evidence={"status":"UNKNOWN","reason":type(exc).__name__}
    # v33-14: BOUNDED READ-ONLY OKX order-fill verification, maximum one
    # terminal owned AI close receipt per 5-minute Watchdog run.
    # All exceptions are isolated from trading, alerting, and watchdog health.
    try:
        import core_ai_close_receipt_reconcile_v33_14 as receipt_v33_14
        receipt_probe=receipt_v33_14.reconcile(str(user_dir),max_queries=1)
        receipt_quality=receipt_v33_14.summary(str(user_dir))
        receipt_quality['probe']=receipt_probe
    except Exception as exc:
        receipt_quality={"status":"UNKNOWN","reason":type(exc).__name__}
    try:
        import core_ai_exit_horizon_v33_14 as horizon_v33_14
        post_exit_horizon=horizon_v33_14.from_user_dir(str(user_dir),now=now,days=7)
    except Exception as exc:
        post_exit_horizon={"status":"UNKNOWN","reason":type(exc).__name__}
    # v33-17: distinguish AI HOLD from historical global safety blocks.
    # File/log-only; never changes gate criteria or grants order authority.
    try:
        import core_entry_reason_audit_v33_17
        entry_reason_audit=core_entry_reason_audit_v33_17.from_user_dir(
            str(user_dir),now=now)
    except Exception as exc:
        entry_reason_audit={"status":"UNKNOWN","reason":type(exc).__name__}
    try:
        import core_pre_gpt_gate_audit_v33_18
        pre_gpt_evidence=core_pre_gpt_gate_audit_v33_18.read(str(user_dir),now=now)
    except Exception as exc:
        pre_gpt_evidence={"status":"UNKNOWN","reason":type(exc).__name__}
    # v33-19: read-only provenance of repeated reentry cooldown/freshness blocks.
    try:
        import core_reentry_forensics_v33_19
        reentry_forensics=core_reentry_forensics_v33_19.audit(str(user_dir),now=now)
    except Exception as exc:
        reentry_forensics={"status":"UNKNOWN","reason":type(exc).__name__}
    snapshot={
        "schema_version":1, "generated_at":now.isoformat(timespec="seconds"),
        "live_authority":False,
        "strategy":metrics.get("strategy") or previous.get("strategy") or {},
        "cost_efficiency":metrics.get("cost_efficiency") or previous.get("cost_efficiency") or {},
        "trade_execution_audit":trade_execution_audit,
        "core_profit_retention_audit":profit_retention,
        "core_exit_expectancy_audit":expectancy_evidence,
        "core_ai_close_receipt_audit":receipt_quality,
        "core_ai_exit_horizon_audit":post_exit_horizon,
        "core_entry_reason_audit":entry_reason_audit,
        "core_pre_gpt_gate_audit":pre_gpt_evidence,
        "core_reentry_forensics":reentry_forensics,
        "health":health,
        "analysis_generated_at":metrics.get("analysis_generated_at"),
        "analysis_metrics_refreshed_at":metrics.get("generated_at"),
    }
    obs.save_snapshot(str(user_dir),snapshot)
    _notify_changes(user_dir,snapshot)
    print(json.dumps({"ok":True,"generated_at":snapshot["generated_at"],
                      "issues":snapshot["health"]["issue_count"],
                      "critical":snapshot["health"]["critical_count"],
                      "analysis_cache_reused":bool(metrics.get("analysis_cache_reused")),
                      "analysis_generated_at":metrics.get("analysis_generated_at")},ensure_ascii=False))
    return 0

if __name__=="__main__":
    raise SystemExit(main())
