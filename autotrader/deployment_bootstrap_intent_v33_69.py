"""Initial deployment preflight for release with no in-process checkpoint.

Makes an exact, short-lived deployment intent only after two independent
runtime observations and read-only OKX protection verification.
"""
import argparse
import json
import os
import subprocess
from pathlib import Path

import config
from deployment_bootstrap_snapshot_v33_69 import snapshot
from deployment_preflight_v33_69 import exchange_preflight,build_intent


def make_intent(*, old_pid, target, dry_run):
    old = Path("/opt/autotrader").resolve()
    first=snapshot(pid=old_pid)
    user=Path("/opt/autotrader/users/chickenbananalab")
    positions=exchange_preflight(config.UserConfig(str(user)))
    final=snapshot(pid=old_pid)
    if first["config_sha256"]!=final["config_sha256"]:
        raise ValueError("config_changed_during_exchange_preflight")
    if subprocess.check_output(["systemctl","show","autotrader.service","-p",
        "MainPID","--value"],text=True,timeout=5).strip()!=str(old_pid):
        raise ValueError("service_pid_changed")
    intent=build_intent(checkpoint=final,current_release=old,
        current_pid=old_pid,next_release=target,user_dir=user,
        protected_positions_verified=True)
    if not dry_run:
        pending=Path("/var/lib/autotrader/deployment-resume/pending.json")
        fd=os.open(pending,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,"w") as f:
            json.dump(intent,f)
            f.flush();os.fsync(f.fileno())
    return dict(validated=True,dry_run=dry_run,positions_protected=positions,
                old_pid=old_pid,requested_core=intent["core"],
                requested_candidate=intent["candidate_c"])


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--pid",type=int,required=True)
    p.add_argument("--target",required=True)
    p.add_argument("--dry-run",action="store_true")
    args=p.parse_args()
    print(json.dumps(make_intent(old_pid=args.pid,target=args.target,
                                 dry_run=args.dry_run)))


if __name__=="__main__":
    main()
