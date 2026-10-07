import datetime, hashlib, json, os, pwd, re, shutil, subprocess, sys, tarfile, time
from pathlib import Path
import audit_base as a
ROOT=Path("/tmp/autotrader-entry-final-20261008")
OLD=Path("/opt/autotrader-releases/early_entry_risk_rr_repair_v3_20261007T2219KST")
a.ROOT=ROOT
a.OLD=OLD
a.ACTIVE=Path("/opt/autotrader")
os.environ["AUTOTRADER_PROJECT_DIR"]=str(a.ACTIVE)
MANIFEST="EARLY_ENTRY_RISK_RR_REPAIR_V4_DEPLOYMENT.json"

def prepare():
    assert a.ACTIVE.resolve()==OLD,"active_release_changed"
    patch=json.loads((ROOT/"patch_manifest.json").read_text())
    for name,value in patch["baseline_sha256"].items():
        if value is None: assert not (OLD/name).exists(),"unexpected_existing_file:"+name
        else: assert a.sha(OLD/name)==value,"production_source_changed:"+name
    baseline=ROOT/"baseline";candidate=ROOT/"candidate"
    shutil.copytree(OLD,baseline,symlinks=True,ignore=shutil.ignore_patterns(*a.SHARED,"__pycache__",".pytest_cache",".git"))
    shutil.copytree(baseline,candidate,symlinks=True)
    with tarfile.open(ROOT/"patch.tar.gz") as bundle:
        members=bundle.getmembers()
        assert {m.name for m in members}==set(patch["updated_sha256"])
        assert all(m.isfile() and not Path(m.name).is_absolute() and ".." not in Path(m.name).parts for m in members)
        bundle.extractall(candidate)
    assert all(a.sha(candidate/name)==value for name,value in patch["updated_sha256"].items())
    (ROOT/"test-runtime").mkdir(exist_ok=True)
    env=dict(os.environ,AUTOTRADER_PROJECT_DIR=str(ROOT/"test-runtime"),PYTHONPATH=os.pathsep.join((str(ROOT/"network-guard"),str(candidate))),PYTHONDONTWRITEBYTECODE="1",PYTHONPYCACHEPREFIX=str(ROOT/"isolated-pycache"))
    baseline_env=dict(env,PYTHONPATH=os.pathsep.join((str(ROOT/"network-guard"),str(baseline))),
                      AUTOTRADER_PROJECT_DIR=str(ROOT/"baseline-test-runtime"))
    (ROOT/"baseline-test-runtime").mkdir()
    with (ROOT/"pytest-baseline-server.log").open("w") as output:
        baseline_result=subprocess.run([str(OLD/".venv/bin/python3"),"-m","pytest","tests","-q","--tb=short",
            "--basetemp="+str(ROOT/"baseline-pytest-temp"),"--junitxml="+str(ROOT/"pytest-baseline-server.xml")],
            cwd=baseline,env=baseline_env,stdout=output,stderr=subprocess.STDOUT)
    baseline_failures,baseline_suites=a.results(ROOT/"pytest-baseline-server.xml")
    assert baseline_result.returncode==0 and not baseline_failures,"baseline_tests_failed"
    with (ROOT/"pytest-server.log").open("w") as output:
        result=subprocess.run([str(OLD/".venv/bin/python3"),"-m","pytest","tests","-q","--tb=short","--basetemp="+str(ROOT/"pytest-temp"),"--junitxml="+str(ROOT/"pytest-server.xml")],
                              cwd=candidate,env=env,stdout=output,stderr=subprocess.STDOUT)
    failures,suites=a.results(ROOT/"pytest-server.xml")
    assert result.returncode==0 and not failures,"server_tests_failed"
    server_xml=a.ET.parse(ROOT/"pytest-server.xml").getroot()
    server_ids=sorted(c.get("classname","")+"::"+c.get("name","") for c in server_xml.iter("testcase"))
    assert server_ids==patch["canonical_testcase_ids"],"server_test_collection_differs"
    assert all(int(s.get("skipped",0))==0 for s in suites),"unexpected_server_test_skips"
    validated=dict(validated=True,updated_sha256=patch["updated_sha256"],
                   baseline_source_sha256=a.source_hashes(baseline),
                   candidate_source_sha256=a.source_hashes(candidate),suites=suites,baseline_suites=baseline_suites)
    a.save("validation.json",validated)
    stamp=datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).strftime("%Y%m%dT%H%MKST")
    new=Path("/opt/autotrader-releases")/("early_entry_risk_rr_repair_v4_"+stamp)
    assert not new.exists()
    shutil.copytree(candidate,new,symlinks=True,ignore=shutil.ignore_patterns("__pycache__",".pytest_cache"))
    shared={}
    for name in a.SHARED:
        if (OLD/name).exists():
            assert not (new/name).exists()
            target=(OLD/name).resolve(strict=True)
            (new/name).symlink_to(target)
            shared[name]=str(target)
    (new/MANIFEST).write_text(json.dumps(dict(created_at=time.time(),release=str(new),previous_release=str(OLD),
         changed_files=list(patch["updated_sha256"]),updated_sha256=patch["updated_sha256"],
         shared_targets=shared,tests=suites,scope='V4 completes existing early-entry repair: CORE .001 established round-trip costs and full OCO TP2 economics, actual verified plan state, exact latest quote/net RR/loss budget/lot size/candle-review clocks under account lock for gate on/off and before reversal close. Candidate exact-quote chase/expiry and net AI envelope. V3 first-eligible edge structural stops and quantity downsizing retained. No operator/test exchange orders or protection mutations. Existing early-entry repair: first actual eligibility edge risk-capped structural stops, no wide-stop clamping on late rechecks, bounded cost-aware TP2, consistent CORE TP2 rescue plan audit, regenerated technical parity after full tests. Existing settings, source freshness, setup age, AI agreement, account/position/OCO shared state preserved. Verification sends no exchange orders.'),indent=2))
    assert a.source_hashes(new)==validated["candidate_source_sha256"]
    assert all(a.sha(new/name)==value for name,value in validated["updated_sha256"].items())
    for directory,dirs,files in os.walk(new,followlinks=False):
        for path in [Path(directory),*[Path(directory)/n for n in dirs+files]]:
            if path.is_symlink(): continue
            os.chown(path,0,0)
            path.chmod(0o555 if path.is_dir() or path.stat().st_mode&0o111 else 0o444)
    a.save("release-plan.json",dict(release=str(new),shared_targets=shared))
    print(json.dumps(dict(prepared=True,release=str(new),tests=suites)),flush=True)

def verify_exchange_resume(before,after,started):
    """Read-only proof; normal resumed trading may create a protected position."""
    old_ids={p["posId"] for p in before["positions"]}
    opened=[p for p in after["positions"] if p["posId"] not in old_ids]
    release=Path(after["release"])
    log=release/"users"/a.USER/"gpt_shadow_log.jsonl"
    entries=[]
    for line in log.read_text().splitlines()[-250:]:
        try:
            row=json.loads(line)
            at=datetime.datetime.fromisoformat(row.get("time","")).timestamp()
        except (ValueError,TypeError):
            continue
        if at>=started and row.get("mode")=="entry_gate":
            entries.append({k:row.get(k) for k in (
                "symbol","gemini_action","gpt_decision","gpt_confidence",
                "order_success","gate_result","time","decision_id")})
    for p in opened:
        assert float(p["cTime"])/1000>=started,"unexplained_new_position"
        symbol=p["instId"].split("-")[0]+"/USDT:USDT"
        if p["instId"].split("-")[0] in ("BTC","ETH","XRP","PI"):
            assert any(r["symbol"]==symbol and r["gpt_decision"]=="approve_now"
                       and r["gate_result"]=="approved" and r["order_success"] is True
                       for r in entries),"new_core_position_without_consensus_receipt"
        else:
            assert p["instId"].split("-")[0] in ("DOGE","SOL"),"unexpected_position_owner"
        assert any(x["instId"]==p["instId"] and float(x.get("slTriggerPx") or 0)>0
                   for x in after["protections"]),"new_position_missing_exchange_stop"
    return dict(positions_unchanged=before["positions"]==after["positions"],
                protections_unchanged=before["protections"]==after["protections"],
                new_positions=opened,entry_gate_records=entries,
                verification_orders_sent=False)


CORE_MODE=None
def resume_for(release):
    user=pwd.getpwnam("bagmingi")
    pending=a.RESUME/"pending.json"
    assert not pending.exists(),"resume_already_pending"
    now=time.time();temporary=pending.with_name("pending.json.staged")
    temporary.write_text(json.dumps(dict(release=str(release),created_at=now,expires_at=now+600,
           username=a.USER,core=True,core_mode=CORE_MODE,candidate_c="live")))
    os.chown(temporary,user.pw_uid,user.pw_gid);temporary.chmod(0o600);os.replace(temporary,pending)
a.resume_for=resume_for

def deploy():
    global CORE_MODE
    assert a.ACTIVE.resolve()==OLD
    validated=json.loads((ROOT/"validation.json").read_text())
    new=Path(json.loads((ROOT/"release-plan.json").read_text())["release"])
    assert validated["validated"] and a.source_hashes(new)==validated["candidate_source_sha256"]
    assert all(a.sha(new/name)==value for name,value in validated["updated_sha256"].items())
    assert all(a.sha(OLD/name)==value for name,value in validated["baseline_source_sha256"].items()),"baseline_source_changed"
    for prop in ("ExecStop","ExecStopPost","TriggeredBy"):
        assert not a.run("systemctl","show","autotrader.service","--value","-p",prop),"unsafe_service_hook:"+prop
    sys.path.insert(0,str(OLD))
    import config,deployment_resume
    cfg=config.UserConfig(str(OLD/"users"/a.USER))
    CORE_MODE=cfg.CORE_UNIFIED_MODE
    assert deployment_resume.settings_match({"core_mode":CORE_MODE},cfg)
    a.ensure_resume_directory()
    before=a.audit("before.json")
    assert a.engines_ok(before),"existing_engines_not_running"
    print(json.dumps({"before_positions":before["positions"],"before_protections":before["protections"],"core_mode":CORE_MODE}),flush=True)
    started=time.time()
    a.save("deployment.json",dict(started_at=started,old_release=str(OLD),new_release=str(new)))
    stop_attempted=False
    try:
        stop_attempted=True
        a.run("systemctl","stop","autotrader.service")
        assert a.run("systemctl","show","autotrader.service","--value","-p","MainPID")=="0"
        resume_for(new);a.switch(new);a.run("systemctl","start","autotrader.service")
        resumed=a.wait_resume(new,started)
        after=a.wait_for_engines("after.json",before=before)
        assert before["positions"]==after["positions"],"existing_positions_changed"
        assert before["protections"]==after["protections"],"existing_protections_changed"
        exchange_check=verify_exchange_resume(before,after,started)
        assert before["user_env_sha256"]==after["user_env_sha256"],"settings_changed"
        assert all((new/name).is_symlink() and str((new/name).resolve())==target
                   for name,target in json.loads((ROOT/"release-plan.json").read_text())["shared_targets"].items())
        assert a.source_hashes(new)==validated["candidate_source_sha256"]
        assert all(a.sha(new/name)==value for name,value in validated["updated_sha256"].items())
        parity=a.run(str(new/".venv/bin/python3"),"-c",
            "import candidate_c_runtime as r; e=r.backtest_live_parity_evidence(); "
            "assert e['verified'], 'deployed_policy_hash_not_verified'; "
            "print('deployed_candidate_policy_hashes_verified')",
            cwd=new,env=dict(os.environ,AUTOTRADER_PROJECT_DIR=str(ROOT/"test-runtime"),PYTHONDONTWRITEBYTECODE="1"))
        journal=a.run("journalctl","-u","autotrader.service","--since","@"+str(started),"--no-pager","-o","cat")
        errors=[line for line in journal.splitlines() if re.search(r"Traceback|Exception|CRITICAL|Deployment resume failed",line)]
        assert not errors,"postdeploy_exception"
        result=a.save("verification.json",dict(ok=True,release=str(new),pid=after["pid"],
              service=after["service"],core_running=True,candidate_c_live=True,
              positions_unchanged=exchange_check["positions_unchanged"],
              protections_unchanged=exchange_check["protections_unchanged"],
              exchange_resume=exchange_check,configuration_unchanged=True,
              resume=resumed,postdeploy_errors=errors,policy_hash_verification=parity))
        print(json.dumps(result,ensure_ascii=False),flush=True)
    except BaseException as exc:
        a.save("failure.json",dict(error=type(exc).__name__+":"+str(exc),release=str(a.ACTIVE.resolve())))
        if stop_attempted:a.rollback()
        raise

if __name__=="__main__":
    if sys.argv[1]=="prepare":prepare()
    elif sys.argv[1]=="deploy":deploy()
    else:raise ValueError("unknown operation")