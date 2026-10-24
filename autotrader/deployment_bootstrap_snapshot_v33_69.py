"""One-time v33-67 -> v33-69 runtime observation file.

Observation only: never sends orders, alters protections, or starts threads.
A failed or stale observation cannot authorize any deployment.
"""
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path

import accounts
import config
import deployment_runtime_v33_69 as runtime
from deployment_bootstrap_audit_v33_69 import verify_activity


def snapshot(*, pid, now=None, activity=None):
    now=time.time() if now is None else now
    current=Path("/opt/autotrader").resolve()
    allowed_sources={"core_pnl_exact_bill_repair_v33_67_20261020KST",
       "deploy_runtime_recovery_v33_69_20261020KST"}
    if current.name not in allowed_sources:
        raise ValueError("unsupported_first_cutover_release")
    user=Path("/opt/autotrader/users/chickenbananalab")
    cfg=config.UserConfig(str(user))
    cfg.validate()
    if not accounts.is_approved("chickenbananalab"):
        raise ValueError("unapproved_account")
    if (cfg.CORE_UNIFIED_MODE!="ROLLBACK" or cfg.EXECUTION_MODE!="LIVE"
        or not cfg.CANDIDATE_C_ENABLED or not cfg.CANDIDATE_C_LIVE_EXECUTE
        or not cfg.GPT_ENTRY_GATE_ENABLED):
        raise ValueError("mode_mismatch")
    activity=verify_activity(pid=pid,user_dir=user,now=now) if activity is None else activity
    if activity.get("pid")!=pid:raise ValueError("activity_pid_mismatch")
    if (list(cfg.ENABLED_SYMBOLS)!=["BTC/USDT:USDT","ETH/USDT:USDT",
                                    "XRP/USDT:USDT","ADA/USDT:USDT"]
        or list(cfg.CANDIDATE_C_SYMBOLS)!=["DOGE/USDT:USDT","SOL/USDT:USDT"]):
        raise ValueError("changed_symbol_ownership")
    record=dict(version="v33-69",username="chickenbananalab",
       pid=pid,release=str(current),observed_at=now,
       config_sha256=hashlib.sha256((user/".env").read_bytes()).hexdigest(),
       core_requested=True,core_running=True,candidate_requested=True,
       candidate_running=True,candidate_mode="live",
       core_symbols=list(cfg.ENABLED_SYMBOLS),
       candidate_symbols=list(cfg.CANDIDATE_C_SYMBOLS))
    runtime.validate_checkpoint(record,current_pid=pid,current_release=current,now=now)
    return record


def main():
    pid=int(subprocess.check_output(
        ["systemctl","show","autotrader.service","-p","MainPID","--value"],
        text=True,timeout=5).strip())
    record=snapshot(pid=pid)
    runtime.atomic_json(runtime.PATH,record)
    print(json.dumps({"status":"V33_69_BOOTSTRAP_OBSERVED",
        "pid":pid,"expires_in_seconds":runtime.MAX_AGE_SECONDS,
        "checkpoint_path":str(runtime.PATH)}))


if __name__=="__main__":
    main()
