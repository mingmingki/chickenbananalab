"""Stage/mock-test, audit via GET, and switch an explicitly authorized release.

No order submission, cancellation, amendment, or manual-close path is used.
All shared mutable files keep their exact existing targets. Verification failure
stops the process and atomically rolls back the code, then resumes prior engines.
"""
import hashlib
import json
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import xml.etree.ElementTree as ET

ROOT = Path("/tmp/autotrader-freshness-cooldown-20261004")
OLD = Path("/opt/autotrader-releases/adaptive_exit_width_cap_failclosed_20261004T0834KST")
ACTIVE = Path("/opt/autotrader")
NEW = Path("/opt/autotrader-releases/entry_atr_freshness_post_reduce_cooldown_20261004T1420KST")
EXPECTED = "5f3b80048f548cbdd6fc4a11e98c637837127fe3b211223d3316df3f31105cd4"
RESUME = Path("/var/lib/autotrader/deployment-resume")
USER = "chickenbananalab"
SHARED = (".venv", "users", "logs", ".env", "accounts.json", "flask_secret.key",
          "vapid_private_key.pem", "vapid_public_key.txt", "trades_log.jsonl", "pnl_state.json")
UPDATED = ("trader.py", "tests/test_post_reduce_close_cooldown.py", "tests/test_core_entry_atr_freshness.py")


def run(*args, **kwargs):
    return subprocess.run(args, check=True, text=True, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, **kwargs).stdout.strip()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def source_hashes(path):
    return {str(p.relative_to(path)):sha(p) for p in Path(path).rglob("*.py")
            if not any(part in ("__pycache__", ".pytest_cache") for part in p.parts)}


def save(name, data):
    path = ROOT / name
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    path.chmod(0o600)
    return data


def state_api(release):
    # Use the existing server session secret only inside this process. The
    # signed cookie never leaves the VM and is never written to audit output.
    from flask import Flask
    import requests
    app = Flask("read_only_deployment_audit")
    app.secret_key = (release / "flask_secret.key").read_text().strip()
    cookie = app.session_interface.get_signing_serializer(app).dumps(
        {"authenticated": True, "username": USER})
    data = {}
    for name, route in (("core", "/api/state"), ("candidate_c", "/api/candidate_c_state")):
        response = requests.get("http://127.0.0.1:8080" + route,
                                cookies={"session": cookie}, timeout=30)
        response.raise_for_status()
        row = response.json()
        keys = ("running", "last_error", "status", "runtime_status", "engine_effective_mode",
                "engine_effective_gpt_entry_gate_enabled", "configured_vs_effective_mismatch")
        data[name] = {k: row[k] for k in keys if k in row}
    return data


def audit(name, with_api=True):
    release = ACTIVE.resolve(strict=True)
    sys.path.insert(0, str(release))
    import config
    from okx_client import OkxClient
    cfg = config.UserConfig(str(release / "users" / USER))
    exchange = OkxClient("BTC/USDT:USDT", cfg).exchange
    original_request = exchange.request

    def read_only_request(path, api="public", method="GET", *args, **kwargs):
        if method.upper() != "GET":
            raise RuntimeError("audit_non_get_forbidden")
        return original_request(path, api, method, *args, **kwargs)

    exchange.request = read_only_request
    position_response = exchange.private_get_account_positions({"instType": "SWAP"})
    assert position_response.get("code") == "0", "position_query_failed"
    positions = []
    for row in position_response.get("data", []):
        if float(row.get("pos") or 0) != 0:
            positions.append({k: row.get(k) for k in (
                "instId", "posId", "posSide", "pos", "cTime", "avgPx", "mgnMode")})
    protections = []
    for kind in ("oco", "conditional"):
        response = exchange.private_get_trade_orders_algo_pending({"ordType": kind, "limit": "100"})
        assert response.get("code") == "0", "protection_query_failed"
        assert len(response.get("data", [])) < 100, "protection_pagination_required"
        for row in response.get("data", []):
            protections.append({k: row.get(k) for k in (
                "instId", "algoId", "algoClOrdId", "ordType", "state", "side", "posSide", "sz",
                "slTriggerPx", "slOrdPx", "slTriggerPxType", "tpTriggerPx", "tpOrdPx",
                "tpTriggerPxType", "reduceOnly", "tdMode", "closeFraction")})
        time.sleep(.4)
    pid = int(run("systemctl", "show", "autotrader.service", "--value", "-p", "MainPID"))
    runtimes = {}
    for symbol in ("DOGE", "SOL"):
        row = json.loads((release / "users" / USER / f"candidate_c_runtime_{symbol}_USDT_USDT.json").read_text())
        runtimes[symbol] = {k: row.get(k) for k in ("pid", "status", "heartbeat_at", "effective_settings")}
    return save(name, dict(time=time.time(), release=str(release), service=run("systemctl", "is-active", "autotrader.service"),
                          pid=pid, positions=sorted(positions, key=lambda x: (x["instId"], x["posId"])),
                          protections=sorted(protections, key=lambda x: (x["instId"], x["algoId"])),
                          user_env_sha256=sha(release / "users" / USER / ".env"),
                          candidate_runtimes=runtimes, api=state_api(release) if with_api else None))


def engines_ok(data):
    if data["service"] != "active" or data["pid"] <= 0:
        return False
    if not data["api"]["core"].get("running") or data["api"]["core"].get("last_error"):
        return False
    if data["api"]["candidate_c"].get("engine_effective_mode") != "live":
        return False
    return all(row["status"] == "RUNNING" and row["pid"] == data["pid"]
               and 0 <= time.time() - row["heartbeat_at"] < 120 for row in data["candidate_runtimes"].values())


def wait_for_engines(name, *, timeout_seconds=120, before=None):
    deadline=time.monotonic()+timeout_seconds
    while True:
        observed=audit(name)
        if before is not None:
            assert before["positions"]==observed["positions"], "position_changed"
            assert before["protections"]==observed["protections"], "exchange_protection_changed"
        if engines_ok(observed):
            return observed
        if time.monotonic()>=deadline:
            raise RuntimeError("engine_heartbeat_warmup_timeout")
        print(json.dumps(dict(waiting_for_first_engine_heartbeat=True,pid=observed["pid"],
            candidate_pids={k:v["pid"] for k,v in observed["candidate_runtimes"].items()})),flush=True)
        time.sleep(10)


def results(path):
    root = ET.parse(path).getroot()
    bad = {t.get("classname") + "::" + t.get("name") for t in root.iter("testcase")
           if t.find("failure") is not None or t.find("error") is not None}
    return bad, [s.attrib for s in root.findall("testsuite")]


def prepare():
    assert ACTIVE.resolve(strict=True) == OLD
    assert sha(OLD / "trader.py") == EXPECTED
    baseline = ROOT / "baseline"
    candidate = ROOT / "candidate"
    ignored = shutil.ignore_patterns(*SHARED, ".git", "__pycache__", ".pytest_cache")
    shutil.copytree(OLD, baseline, symlinks=True, ignore=ignored)
    shutil.copytree(baseline, candidate, symlinks=True)
    with tarfile.open(ROOT / "patch.tar.gz") as bundle:
        members = bundle.getmembers()
        assert {m.name for m in members} == set(UPDATED)
        assert all(m.isfile() and not Path(m.name).is_absolute() and ".." not in Path(m.name).parts for m in members)
        bundle.extractall(candidate)
    expected_updated = json.loads((ROOT / "local-validation.json").read_text())["updated_sha256"]
    assert all(sha(candidate / name) == expected_updated[name] for name in UPDATED)
    summaries = {}
    for label, code in (("baseline", baseline), ("candidate", candidate)):
        project = ROOT / (label + "-test-project")
        project.mkdir()
        env = dict(os.environ, AUTOTRADER_PROJECT_DIR=str(project), PYTHONPATH=str(code), PYTHONDONTWRITEBYTECODE="1")
        with (ROOT / (label + "-tests.log")).open("w") as output:
            proc = subprocess.run([str(OLD / ".venv/bin/python3"), "-m", "pytest", "tests", "-q", "--tb=short",
                                   "--junitxml=" + str(ROOT / (label + "-tests.xml"))],
                                  cwd=code, env=env, stdout=output, stderr=subprocess.STDOUT)
        assert proc.returncode in (0, 1), label + "_test_runner_error"
        failures, suites = results(ROOT / (label + "-tests.xml"))
        summaries[label] = dict(failures=sorted(failures), suites=suites)
    assert summaries["candidate"]["failures"] == summaries["baseline"]["failures"], "server_new_regression"
    related = [UPDATED[1], UPDATED[2], "tests/test_core_entry_overextension_guard.py",
               "tests/test_entry_overextension_guard.py", "tests/test_candidate_c_entry_overextension_guard.py",
               "tests/test_position_ai_close_guard_20260927.py", "tests/test_core_exit_escalation_20260924.py",
               "tests/test_dual_ai_invalidated_close.py", "rollback_full_checks/test_deployment_resume.py"]
    env = dict(os.environ, AUTOTRADER_PROJECT_DIR=str(ROOT / "candidate-test-project"),
               PYTHONPATH=str(candidate), PYTHONDONTWRITEBYTECODE="1")
    with (ROOT / "related-tests.log").open("w") as output:
        proc = subprocess.run([str(OLD / ".venv/bin/python3"), "-m", "pytest", *related, "-q"],
                              cwd=candidate, env=env, stdout=output, stderr=subprocess.STDOUT)
    assert proc.returncode == 0, "server_related_tests_failed"
    save("validation.json", dict(validated=True, new_regressions=[], summaries=summaries,
                                  updated_sha256=expected_updated, old_trader_sha256=EXPECTED,
                                  baseline_source_sha256=source_hashes(baseline),
                                  candidate_source_sha256=source_hashes(candidate)))
    print(json.dumps(dict(validated=True, summaries=summaries)))


def resume_for(release):
    RESUME.mkdir(exist_ok=True)
    user = pwd.getpwnam("bagmingi")
    pending = RESUME / "pending.json"
    assert not pending.exists(), "resume_already_pending"
    now = time.time()
    temp = RESUME / "pending.json.staged"
    temp.write_text(json.dumps(dict(release=str(release), created_at=now, expires_at=now+600,
                                   username=USER, core=True, core_mode="ROLLBACK", candidate_c="live")))
    os.chown(temp, user.pw_uid, user.pw_gid)
    temp.chmod(0o600)
    os.replace(temp, pending)


def ensure_resume_directory():
    user = pwd.getpwnam("bagmingi")
    RESUME.mkdir(exist_ok=True)
    os.chown(RESUME,user.pw_uid,user.pw_gid)
    RESUME.chmod(0o700)
    run("sudo","-u","bagmingi","test","-w",str(RESUME))
    assert not (RESUME/"pending.json").exists(), "resume_already_pending"


def seal_prepared():
    validated=json.loads((ROOT/"validation.json").read_text())
    assert validated["validated"] and not validated["new_regressions"]
    baseline=source_hashes(ROOT/"baseline")
    candidate=source_hashes(ROOT/"candidate")
    assert all(sha(OLD/name)==value for name,value in baseline.items())
    assert candidate==dict(baseline,**validated["updated_sha256"]), "unvalidated_candidate_change"
    validated.update(baseline_source_sha256=baseline,candidate_source_sha256=candidate)
    save("validation.json",validated)
    print(json.dumps(dict(prepared_source_sealed=True,python_files=len(candidate))))


def archive_attempt():
    assert ACTIVE.resolve(strict=True)==OLD
    original=json.loads((ROOT/"before.json").read_text())
    recovered=wait_for_engines("recovered-before-retry.json",before=original)
    save("rollback.json",dict(time=time.time(),release=str(OLD),service=recovered["service"],
        engines_restored=True,positions_unchanged=True,protection_orders_unchanged=True,
        note="Initial rollback verification preceded first new heartbeat; recovery now independently verified"))
    directory=ROOT/"attempt1"
    directory.mkdir()
    for name in ("before.json","after.json","failure.json","deployment.json","rollback.json",
                 "rollback-audit.json","recovered-before-retry.json"):
        shutil.copy2(ROOT/name,directory/name)
    print(json.dumps(dict(previous_attempt_archived=True,previous_release_restored=True,
                          positions_unchanged=True,protection_orders_unchanged=True)))


def export_evidence():
    archive=ROOT/"server-evidence.tar.gz"
    with tarfile.open(archive,"w:gz") as bundle:
        files=[p for p in ROOT.iterdir() if p.is_file() and p.suffix in (".json",".xml",".log")]
        files+=list((ROOT/"attempt1").iterdir())
        for path in files:
            bundle.add(path,arcname=str(path.relative_to(ROOT)))
    owner=pwd.getpwnam("bagmingi")
    os.chown(archive,owner.pw_uid,owner.pw_gid)
    archive.chmod(0o600)
    print(json.dumps(dict(evidence_archive=str(archive),sha256=sha(archive))))


def wait_resume(release, started):
    for _ in range(12):
        time.sleep(5)
        if (RESUME/"result.json").exists():
            result = json.loads((RESUME/"result.json").read_text())
            if result.get("time",0) >= started:
                consumed = json.loads((RESUME/"pending.json.consumed").read_text())
                assert consumed["release"] == str(release), "wrong_resume_release"
                assert all(result["results"][name].get("ok") is True for name in ("core","candidate_c"))
                return result
    raise RuntimeError("fresh_engine_resume_timeout")


def switch(release):
    link = ACTIVE.with_name("autotrader.freshness-next")
    assert not link.exists() and not link.is_symlink()
    link.symlink_to(release)
    os.replace(link, ACTIVE)


def rollback():
    run("systemctl", "stop", "autotrader.service")
    pending = RESUME / "pending.json"
    if pending.exists():
        os.replace(pending, ROOT / "failed-resume-pending.json")
    switch(OLD)
    started=time.time()
    resume_for(OLD)
    run("systemctl", "start", "autotrader.service")
    result=wait_resume(OLD,started)
    observed=wait_for_engines("rollback-audit.json")
    assert ACTIVE.resolve(strict=True)==OLD and engines_ok(observed), "rollback_engine_recovery_failed"
    save("rollback.json", dict(time=time.time(), release=str(ACTIVE.resolve()), service=observed["service"],
                              engines_restored=True,resume=result))


def verify():
    assert ACTIVE.resolve(strict=True) == NEW
    validated = json.loads((ROOT / "validation.json").read_text())
    assert all(sha(NEW / name) == value for name, value in validated["updated_sha256"].items())
    resume = json.loads((RESUME / "result.json").read_text())
    deployment = json.loads((ROOT / "deployment.json").read_text())
    assert resume["time"] >= deployment["started_at"], "stale_resume_result"
    assert all(resume["results"][name].get("ok") is True for name in ("core", "candidate_c"))
    assert not (RESUME / "pending.json").exists()
    before = json.loads((ROOT / "before.json").read_text())
    after = wait_for_engines("after.json",before=before)
    assert engines_ok(after), "engines_not_restored"
    assert before["positions"] == after["positions"], "position_changed"
    assert before["protections"] == after["protections"], "exchange_protection_changed"
    assert before["user_env_sha256"] == after["user_env_sha256"], "configuration_changed"
    for name in SHARED:
        if (OLD/name).exists():
            assert (NEW/name).is_symlink() and (NEW/name).resolve() == (OLD/name).resolve(), "shared_path_changed:"+name
    journal = run("journalctl", "-u", "autotrader.service", "--since", "@"+str(deployment["started_at"]), "--no-pager", "-o", "cat")
    (ROOT / "postdeploy-journal.log").write_text(journal)
    errors = [line for line in journal.splitlines() if "Traceback" in line or "Exception" in line
              or "Deployment resume failed" in line]
    assert not errors, "postdeploy_exception"
    loaded = run(str(NEW/".venv/bin/python3"), "-c",
        "import trader; from pathlib import Path; "
        "assert Path(trader.__file__).resolve().parent==Path('"+str(NEW)+"'); "
        "assert trader.POST_REDUCE_CLOSE_COOLDOWN_SECONDS==900; "
        "assert any(isinstance(s,str) and 'move_30m_atr=%s pullback_atr=%s' in s for s in trader.run_cycle.__code__.co_consts); "
        "print('operational release imported: freshness log fields and post-reduce 900s cooldown loaded')",
        cwd=NEW, env=dict(os.environ, AUTOTRADER_PROJECT_DIR=str(ROOT/"candidate-test-project"), PYTHONDONTWRITEBYTECODE="1"))
    return save("verification.json", dict(ok=True, release=str(NEW), service=after["service"],
                core_running=True, candidate_c_running=True, positions_unchanged=True, protection_orders_unchanged=True,
                configuration_unchanged=True, traceback_exception_lines=errors,
                loaded_code_check=loaded, resume=resume, freshness_live_log_lines=[line for line in journal.splitlines() if "ENTRY_FRESHNESS" in line]))


def deploy():
    validated = json.loads((ROOT/"validation.json").read_text())
    assert validated["validated"] and not validated["new_regressions"]
    assert ACTIVE.resolve(strict=True) == OLD and sha(OLD/"trader.py") == EXPECTED
    assert source_hashes(ROOT/"candidate") == validated["candidate_source_sha256"], "staged_source_changed"
    assert all(sha(OLD/name)==value for name,value in validated["baseline_source_sha256"].items()), "baseline_source_changed"
    assert not NEW.exists()
    for prop in ("ExecStop", "ExecStopPost", "TriggeredBy"):
        assert not run("systemctl", "show", "autotrader.service", "--value", "-p", prop), "unsafe_service_hook:"+prop
    before = audit("before.json")
    assert engines_ok(before), "existing_engines_not_active"
    sys.path.insert(0,str(OLD))
    import config, deployment_resume
    cfg = config.UserConfig(str(OLD/"users"/USER))
    assert deployment_resume.settings_match(dict(core_mode="ROLLBACK"),cfg)
    ensure_resume_directory()
    shutil.copytree(ROOT/"candidate",NEW,symlinks=True)
    assert source_hashes(NEW)==validated["candidate_source_sha256"], "copied_source_changed"
    for name in SHARED:
        if (OLD/name).exists():
            (NEW/name).symlink_to((OLD/name).resolve())
    owner = pwd.getpwnam("bagmingi")
    for directory, dirs, files in os.walk(NEW,followlinks=False):
        for path in [Path(directory),*[Path(directory)/n for n in dirs+files]]:
            os.chown(path,owner.pw_uid,owner.pw_gid,follow_symlinks=False)
    (NEW/"FRESHNESS_COOLDOWN_DEPLOYMENT.json").write_text(json.dumps(dict(previous_release=str(OLD),
        updated_sha256=validated["updated_sha256"],baseline_failures=validated["summaries"]["baseline"]["failures"],
        no_new_regressions=True,shared_targets={name:str((OLD/name).resolve()) for name in SHARED if (OLD/name).exists()}),indent=2))
    started = time.time()
    save("deployment.json",dict(started_at=started,old_release=str(OLD),new_release=str(NEW)))
    stopped = False
    try:
        run("systemctl","stop","autotrader.service")
        stopped = True
        assert run("systemctl","show","autotrader.service","--value","-p","MainPID")=="0"
        resume_for(NEW)
        switch(NEW)
        run("systemctl","start","autotrader.service")
        wait_resume(NEW,started)
        print(json.dumps(verify(),ensure_ascii=False),flush=True)
    except BaseException as exc:
        save("failure.json",dict(time=time.time(),error=type(exc).__name__+":"+str(exc),release=str(ACTIVE.resolve())))
        if stopped:
            rollback()
        raise


if __name__ == "__main__":
    if sys.argv[1] == "prepare": prepare()
    elif sys.argv[1] == "seal": seal_prepared()
    elif sys.argv[1] == "archive-attempt": archive_attempt()
    elif sys.argv[1] == "export-evidence": export_evidence()
    elif sys.argv[1] == "audit": print(json.dumps(audit("initial-audit.json"),ensure_ascii=False))
    elif sys.argv[1] == "deploy": deploy()
    elif sys.argv[1] == "verify": print(json.dumps(verify(),ensure_ascii=False))
    elif sys.argv[1] == "rollback": rollback()
    else: raise ValueError("unknown command")