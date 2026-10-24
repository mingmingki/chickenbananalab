"""Read-only v33-69 post-restart acceptance; never creates/cancels orders."""
import argparse
import json
import os
import subprocess
from pathlib import Path

import config
from deployment_preflight_v33_69 import exchange_preflight


def validate_result(data, *, release, pid):
    if not isinstance(data, dict) or data.get("version") != "v33-69":
        raise ValueError("unexpected_resume_version")
    if data.get("release") != str(Path(release).resolve()):
        raise ValueError("wrong_release")
    if data.get("success") is not True:
        raise ValueError("engine_resume_not_verified")
    requested_core=data.get("requested_core")
    requested_candidate=data.get("requested_candidate")
    if type(requested_core) is not bool or type(requested_candidate) is not bool:
        raise ValueError("missing_requested_engine_flags")
    if not requested_core and not requested_candidate:
        raise ValueError("no_requested_engines")
    if data.get("core_running") is not requested_core:
        raise ValueError("core_running_mismatch")
    if data.get("candidate_running") is not requested_candidate:
        raise ValueError("candidate_running_mismatch")
    if data.get("pid") != pid:
        raise ValueError("service_process_mismatch")
    return requested_core,requested_candidate


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--expected-pid",type=int,required=True)
    args=parser.parse_args()
    root=Path("/opt/autotrader")
    data=json.loads(Path("/var/lib/autotrader/deployment-resume/result.json").read_text())
    core,candidate=validate_result(data,release=root,pid=args.expected_pid)
    cfg=config.UserConfig("/opt/autotrader/users/chickenbananalab")
    cfg.validate()
    positions=exchange_preflight(cfg)
    print(json.dumps(dict(ok=True,release=str(root.resolve()),service_pid=args.expected_pid,
        core_running=core,candidate_running=candidate,okx_protected_positions=positions),
        ensure_ascii=False))


if __name__=="__main__":
    main()
