"""v33-69 pre-restart validation. Never changes orders or protection."""
import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

import accounts
import config
import deployment_resume
import deployment_runtime_v33_69 as runtime

SYMBOLS = ("BTC/USDT:USDT","ETH/USDT:USDT","XRP/USDT:USDT",
           "ADA/USDT:USDT","DOGE/USDT:USDT","SOL/USDT:USDT")


def build_intent(*, checkpoint, current_release, current_pid, next_release,
                 user_dir, now=None, protected_positions_verified=False):
    now = time.time() if now is None else now
    runtime.validate_checkpoint(checkpoint, current_pid=current_pid,
                                 current_release=current_release, now=now)
    c = config.UserConfig(str(user_dir))
    if not accounts.is_approved("chickenbananalab"):
        raise ValueError("account_not_approved")
    c.validate()
    if hashlib.sha256((Path(user_dir)/".env").read_bytes()).hexdigest() != checkpoint["config_sha256"]:
        raise ValueError("config_changed_since_observation")
    if c.EXECUTION_MODE != "LIVE":
        raise ValueError("execution_not_live")
    if (checkpoint["core_running"] and
        (c.CORE_UNIFIED_MODE != "ROLLBACK" or not c.GPT_ENTRY_GATE_ENABLED)):
        raise ValueError("core_mode_incompatible")
    if (float(c.RISK_PER_TRADE_PCT)!=0.5 or float(c.CANDIDATE_C_RISK_PER_TRADE_PCT)!=0.5
        or int(c.LEVERAGE)!=5 or int(c.CANDIDATE_C_LEVERAGE)!=5
        or float(c.MAX_DAILY_LOSS_PCT)!=5.0
        or float(c.CANDIDATE_C_MAX_DAILY_LOSS_PCT)!=10.0):
        raise ValueError("risk_contract_changed")
    if checkpoint["core_symbols"] != c.ENABLED_SYMBOLS or checkpoint["candidate_symbols"] != c.CANDIDATE_C_SYMBOLS:
        raise ValueError("symbol_configuration_changed")
    if checkpoint["candidate_mode"] == "live":
        import candidate_c_runtime
        if not c.CANDIDATE_C_ENABLED or not c.CANDIDATE_C_LIVE_EXECUTE:
            raise ValueError("candidate_live_disabled")
        if candidate_c_runtime.live_activation_blockers(c, user_dir=str(user_dir)):
            raise ValueError("candidate_activation_blocked")
    if not protected_positions_verified:
        raise ValueError("live_exchange_protection_not_confirmed")
    target = Path(next_release).resolve()
    if not target.is_dir() or not str(target).startswith("/opt/autotrader-releases/"):
        raise ValueError("invalid_deployment_release")
    if not (target/"deployment_runtime_v33_69.py").is_file():
        raise ValueError("release_without_runtime_guard")
    intent = dict(
        release=str(target),
        created_at=now,
        expires_at=now+240,
        username="chickenbananalab",
        core=bool(checkpoint["core_running"]),
        core_mode=c.CORE_UNIFIED_MODE,
        candidate_c=checkpoint["candidate_mode"] if checkpoint["candidate_running"] else None,
        config_sha256=checkpoint["config_sha256"])
    if not deployment_resume.settings_match(intent,c):
        raise ValueError("resume_settings_invalid")
    return intent


def exchange_preflight(cfg):
    """All currently live positions must have one matching full-size OCO."""
    import okx_client
    exchange = okx_client.OkxClient(SYMBOLS[0],cfg).exchange
    positions = [p for p in exchange.fetch_positions(list(SYMBOLS))
                 if float(p.get("contracts") or 0)>0]
    for p in positions:
        symbol = p.get("symbol")
        if symbol not in SYMBOLS or p.get("side") not in ("long","short"):
            raise ValueError("unknown_live_position")
        client = okx_client.OkxClient(symbol,cfg)
        expected_side = "sell" if p["side"] == "long" else "buy"
        guards = [x for x in client.fetch_pending_protection_orders()
                  if x.get("state")=="live" and x.get("ordType")=="oco"
                  and x.get("side")==expected_side
                  and str(x.get("reduceOnly")).lower()=="true"
                  and abs(float(x.get("sz") or 0)-float(p["contracts"])) < 1e-8
                  and float(x.get("slTriggerPx") or 0)>0
                  and float(x.get("tpTriggerPx") or 0)>0]
        if len(guards)!=1:raise ValueError("unprotected_or_ambiguous_position:"+str(symbol))
    return len(positions)


def main(argv=None):
    parser=argparse.ArgumentParser()
    parser.add_argument("--target",required=True)
    parser.add_argument("--pid",required=True,type=int)
    parser.add_argument("--dry-run",action="store_true")
    args=parser.parse_args(argv)
    root=Path("/opt/autotrader/users/chickenbananalab")
    check=json.loads(runtime.PATH.read_text())
    source=Path("/opt/autotrader").resolve()
    # Checking keys first avoids an exchange call when state is stale.
    runtime.validate_checkpoint(check,current_pid=args.pid,current_release=source)
    cfg=config.UserConfig(str(root))
    cfg.validate()
    protected_count=exchange_preflight(cfg)
    # Re-read snapshot to rule out a stop/death while exchange was queried.
    final=json.loads(runtime.PATH.read_text())
    runtime.validate_checkpoint(final,current_pid=args.pid,current_release=source)
    if check["config_sha256"]!=final["config_sha256"] or (
            check["core_requested"],check["candidate_requested"])!=(
            final["core_requested"],final["candidate_requested"]):
        raise ValueError("runtime_changed_during_preflight")
    intent=build_intent(checkpoint=final,current_release=source,current_pid=args.pid,
                        next_release=args.target,user_dir=root,protected_positions_verified=True)
    if not args.dry_run:
        target=Path("/var/lib/autotrader/deployment-resume/pending.json")
        fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,"w") as handle:
            json.dump(intent,handle)
            handle.flush();os.fsync(handle.fileno())
    print(json.dumps({"ok":True,"dry_run":args.dry_run,"core":intent["core"],
        "candidate_c":intent["candidate_c"],"protected_positions":protected_count,
        "target":intent["release"]},ensure_ascii=False))


if __name__=="__main__":
    main()
