"""Read-only first-cutover activity audit for the v33-67 service."""
import json
import re
import subprocess
import time
from pathlib import Path

CORE = ("BTC/USDT:USDT","ETH/USDT:USDT","XRP/USDT:USDT","ADA/USDT:USDT")
CANDIDATE = ("DOGE","SOL")


def verify_activity(*, pid, user_dir, now=None, journal=None):
    now=time.time() if now is None else now
    if journal is None:
        journal=subprocess.check_output(
            ["journalctl","-u","autotrader.service","--since","-25 minutes",
             "-o","json","--no-pager"],text=True,timeout=15)
    last={}
    for line in journal.splitlines():
        try:
            item=json.loads(line)
            if int(item.get("_PID",0))!=pid:continue
            msg=item.get("MESSAGE","")
            timestamp=int(item.get("__REALTIME_TIMESTAMP",0))/1e6
            if timestamp>now+5 or timestamp<now-1800:continue
            for symbol in CORE:
                if ("["+symbol+"]" in msg and
                    ("CORE_ENTRY_TIMING " in msg or "Gemini 판단:" in msg)):
                    last[symbol]=max(timestamp,last.get(symbol,0))
        except (ValueError,KeyError,TypeError):
            continue
    if set(last)!=set(CORE) or any(not 0<=now-t<=900 for t in last.values()):
        raise ValueError("core_activity_not_recent")
    states={}
    for symbol in CANDIDATE:
        path=Path(user_dir)/("candidate_c_runtime_"+symbol+"_USDT_USDT.json")
        item=json.loads(path.read_text())
        stamp=item.get("heartbeat_at")
        if (item.get("pid")!=pid or item.get("status")!="RUNNING"
            or (item.get("effective_settings") or {}).get("mode")!="live"
            or type(stamp) not in (int,float) or not 0<=now-stamp<=90):
            raise ValueError("candidate_activity_not_live:"+symbol)
        states[symbol]=round(now-stamp,1)
    return dict(core_activity_age_seconds={s:round(now-last[s],1) for s in CORE},
                candidate_heartbeat_age_seconds=states,
                pid=pid,verified_at=now)


def main():
    pid=int(subprocess.check_output(
        ["systemctl","show","autotrader.service","-p","MainPID","--value"],
        text=True,timeout=5).strip())
    source=Path("/opt/autotrader").resolve()
    if source.name!="core_pnl_exact_bill_repair_v33_67_20261020KST":
        raise ValueError("unsupported_bootstrap_source")
    print(json.dumps(verify_activity(pid=pid,
        user_dir="/opt/autotrader/users/chickenbananalab"),ensure_ascii=False,indent=2))


if __name__=="__main__":
    main()
