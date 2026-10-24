"""v33-69: durable observation-only runtime intent and health evidence."""
import hashlib
import json
import logging
import os
import threading
import time
from pathlib import Path

PATH = Path("/var/lib/autotrader/deployment-resume/engine-runtime-v33-69.json")
PERIOD_SECONDS = 5
MAX_AGE_SECONDS = 18


def atomic_json(path, record):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    import tempfile
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, prefix=".engine_", delete=False) as handle:
        temporary = Path(handle.name)
        try:
            os.fchmod(handle.fileno(), 0o600)
            json.dump(record, handle, ensure_ascii=False, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def runtime_state(ctx):
    import candidate_c_runtime
    root = Path(ctx.dir)
    cfg_path = root / ".env"
    state = ctx.state.snapshot()
    core_thread = ctx.run_thread
    core_wanted = bool(core_thread and ctx.stop_event and not ctx.stop_event.is_set())
    core_running = bool(core_wanted and core_thread.is_alive() and state.get("running"))
    watched = list(ctx.cfg.CANDIDATE_C_SYMBOLS)
    c_threads = list(ctx.candidate_c_threads)
    c_wanted = bool(c_threads and ctx.candidate_c_stop_event and not ctx.candidate_c_stop_event.is_set())
    c_snapshots = [candidate_c_runtime.snapshot(ctx.dir, symbol) for symbol in watched] if c_wanted else []
    # Two trading threads plus one independent account-display refresh thread.
    # Requiring exactly two falsely reports a live Candidate C engine as stopped.
    c_running = bool(c_wanted and watched and len(c_threads) == len(watched)+1
        and all(t.is_alive() for t in c_threads)
        and all(s.get("running") and s.get("observation_fresh") for s in c_snapshots))
    mode = getattr(ctx, "candidate_c_run_mode", None)
    if c_wanted and mode not in ("live", "shadow"):
        mode = "live" if ctx.cfg.CANDIDATE_C_LIVE_EXECUTE else "shadow"
    return dict(
        version="v33-69", username=ctx.username, pid=os.getpid(),
        release=str(Path(__file__).resolve().parent),
        observed_at=time.time(),
        config_sha256=hashlib.sha256(cfg_path.read_bytes()).hexdigest(),
        core_requested=core_wanted, core_running=core_running,
        candidate_requested=c_wanted, candidate_running=c_running,
        candidate_mode=mode if c_wanted else None,
        core_symbols=list(ctx.cfg.ENABLED_SYMBOLS),
        candidate_symbols=watched,
        candidate_checks=[{"symbol":sym, "running":bool(s.get("running")),
            "fresh":bool(s.get("observation_fresh"))} for sym,s in zip(watched,c_snapshots)])


def validate_checkpoint(record, *, current_pid, current_release, max_age=MAX_AGE_SECONDS, now=None):
    now = time.time() if now is None else now
    if not isinstance(record, dict) or record.get("version") != "v33-69":
        raise ValueError("invalid_runtime_version")
    if record.get("username") != "chickenbananalab":
        raise ValueError("unauthorized_account")
    if record.get("pid") != current_pid or record.get("release") != str(Path(current_release).resolve()):
        raise ValueError("runtime_identity_mismatch")
    stamp = record.get("observed_at")
    if type(stamp) not in (int,float) or not 0 <= now-stamp <= max_age:
        raise ValueError("runtime_stale")
    if record.get("core_requested") != record.get("core_running"):
        raise ValueError("core_not_healthy_at_cutover")
    if record.get("candidate_requested") != record.get("candidate_running"):
        raise ValueError("candidate_not_healthy_at_cutover")
    if not (record.get("core_running") or record.get("candidate_running")):
        raise ValueError("no_active_engines_to_resume")
    if record.get("candidate_running") and record.get("candidate_mode") not in ("live","shadow"):
        raise ValueError("unknown_candidate_mode")
    if not isinstance(record.get("config_sha256"),str) or len(record["config_sha256"]) != 64:
        raise ValueError("missing_config_hash")
    return True



def interrupted_prior_owner(prior, *, current_pid, now):
    """Report interruption only; never use prior state as order authority."""
    if not isinstance(prior, dict) or prior.get("version")!="v33-69":
        return False
    if prior.get("username")!="chickenbananalab" or prior.get("pid")==current_pid:
        return False
    stamp=prior.get("observed_at")
    if type(stamp) not in (float,int) or not 0 <= now-stamp <= 3600:
        return False
    return bool((prior.get("core_requested") and prior.get("core_running")) or
                (prior.get("candidate_requested") and prior.get("candidate_running")))


def monitor(context_provider, *, interval=PERIOD_SECONDS, stop_event=None):
    """Never starts or stops an engine; emit one alert per failure episode."""
    logger = logging.getLogger("deployment-runtime-v33-69")
    stop_event = stop_event or threading.Event()
    failed_since = {}
    notified = set()
    # Capture previous service owner's observed state BEFORE overwriting it.
    # An unexpected reboot otherwise erases the evidence that trading was active.
    try:
        prior = json.loads(PATH.read_text()) if PATH.exists() else None
    except (ValueError,OSError):
        prior = None
    lost_prior_owner = interrupted_prior_owner(prior,current_pid=os.getpid(),now=time.time())
    startup_alarm_at = time.monotonic()+180
    while not stop_event.is_set():
        try:
            contexts = list(context_provider())
            for ctx in contexts:
                if ctx.username != "chickenbananalab":
                    continue
                row = runtime_state(ctx)
                atomic_json(PATH, row)
                unhealthy = ((row["core_requested"] and not row["core_running"]) or
                             (row["candidate_requested"] and not row["candidate_running"]))
                if unhealthy:
                    failed_since.setdefault(ctx.username,time.time())
                    if time.time()-failed_since[ctx.username] >= 75 and ctx.username not in notified:
                        notified.add(ctx.username)
                        logger.error("V33_69_ENGINE_RUNTIME_LOST core=%s candidate=%s",
                                     row["core_running"],row["candidate_running"])
                        try:
                            import telegram_notify
                            telegram_notify.send(ctx.cfg,"[AutoTrader] 실행 중이던 매매 엔진이 중단됐습니다. CORE=%s Candidate C=%s. 자동 재주문/재시작하지 않았습니다." %
                                                 (row["core_running"],row["candidate_running"]))
                        except Exception:
                            logger.exception("V33_69_ALERT_DELIVERY_FAILED")
                else:
                    failed_since.pop(ctx.username,None)
                    notified.discard(ctx.username)
            if lost_prior_owner and time.monotonic() >= startup_alarm_at:
                healthy = any(
                    (not prior.get("core_requested") or
                     runtime_state(ctx).get("core_running")) and
                    (not prior.get("candidate_requested") or
                     runtime_state(ctx).get("candidate_running"))
                    for ctx in contexts if ctx.username == "chickenbananalab"
                )
                if not healthy:
                    logger.error("V33_69_PREVIOUS_LIVE_ENGINES_NOT_RESUMED")
                    try:
                        import config, telegram_notify
                        cfg=next((ctx.cfg for ctx in contexts
                                  if ctx.username=="chickenbananalab"),None)
                        if cfg is None:
                            cfg=config.UserConfig("/opt/autotrader/users/chickenbananalab")
                        telegram_notify.send(cfg,
                            "[AutoTrader] 서비스 재시작 후 기존 매매 엔진이 복구되지 않았습니다. "
                            "거래소 보호주문을 확인하고 운영 상태를 점검하세요. 자동 재주문 없음.")
                    except Exception:
                        logger.exception("V33_69_BOOT_ALERT_FAILED")
                lost_prior_owner=False
        except Exception:
            logger.exception("V33_69_RUNTIME_OBSERVATION_FAILED")
        stop_event.wait(interval)
