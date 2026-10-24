"""v33-69 fail-closed deployment intent and loop state tests; no live orders."""
import copy
import json
import os
import stat
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import deployment_runtime_v33_69 as m


class DummyThread:
    def __init__(self, alive=True):
        self.alive = alive
    def is_alive(self):
        return self.alive


def context(tmp_path, *, core=True, candidate=True):
    (tmp_path / ".env").write_text("MOCK=1\n")
    state = SimpleNamespace(snapshot=lambda: dict(running=core))
    cfg = SimpleNamespace(
        CANDIDATE_C_SYMBOLS=['DOGE/USDT:USDT','SOL/USDT:USDT'],
        CANDIDATE_C_LIVE_EXECUTE=True,
        ENABLED_SYMBOLS=['BTC/USDT:USDT','ETH/USDT:USDT',
                         'XRP/USDT:USDT','ADA/USDT:USDT'])
    return SimpleNamespace(username="chickenbananalab",
        dir=str(tmp_path),cfg=cfg,state=state,
        run_thread=DummyThread(core),stop_event=threading.Event(),
        candidate_c_threads=[DummyThread(candidate),DummyThread(candidate),
                             DummyThread(candidate)],
        candidate_c_stop_event=threading.Event(),
        candidate_c_run_mode="live")


@pytest.fixture(autouse=True)
def mocked_observations(monkeypatch):
    import candidate_c_runtime
    monkeypatch.setattr(candidate_c_runtime,"snapshot",
        lambda u,s:dict(running=True,observation_fresh=True,status="RUNNING"))


def test_captured_running_modes_and_config_hash(tmp_path):
    ctx=context(tmp_path)
    info=m.runtime_state(ctx)
    assert info["core_requested"] and info["core_running"]
    assert info["candidate_requested"] and info["candidate_running"]
    assert info["candidate_mode"]=="live"
    assert len(info["config_sha256"])==64
    assert "MOCK" not in json.dumps(info)
    assert m.validate_checkpoint(info,current_pid=os.getpid(),
                                 current_release=Path(m.__file__).parent)


@pytest.mark.parametrize("edit,reason",[
    ({"pid":-999},"runtime_identity_mismatch"),
    ({"release":"/tmp/wrong-release"},"runtime_identity_mismatch"),
    ({"username":"unapproved"},"unauthorized_account"),
    ({"observed_at":0},"runtime_stale"),
    ({"version":"unknown"},"invalid_runtime_version"),
    ({"config_sha256":""},"missing_config_hash"),
    ({"core_running":False},"core_not_healthy_at_cutover"),
    ({"candidate_running":False},"candidate_not_healthy_at_cutover"),
    ({"candidate_mode":"unknown"},"unknown_candidate_mode")])
def test_reject_bad_checkpoint(tmp_path,edit,reason):
    info=m.runtime_state(context(tmp_path))
    info.update(edit)
    with pytest.raises(ValueError,match=reason):
        m.validate_checkpoint(info,current_pid=os.getpid(),
                              current_release=Path(m.__file__).parent)


def test_fails_closed_if_not_actively_running(tmp_path):
    ctx=context(tmp_path)
    ctx.stop_event.set()
    ctx.candidate_c_stop_event.set()
    info=m.runtime_state(ctx)
    assert not info["core_requested"] and not info["candidate_requested"]
    with pytest.raises(ValueError,match="no_active_engines_to_resume"):
        m.validate_checkpoint(info,current_pid=os.getpid(),
                              current_release=Path(m.__file__).parent)


def test_detect_ended_core_thread(tmp_path):
    ctx=context(tmp_path)
    ctx.run_thread.alive=False
    info=m.runtime_state(ctx)
    assert info["core_requested"] and not info["core_running"]


def test_detect_stale_candidate_heartbeat(tmp_path,monkeypatch):
    import candidate_c_runtime
    monkeypatch.setattr(candidate_c_runtime,"snapshot",
        lambda u,s:dict(running=True,observation_fresh=False,status="STALE"))
    info=m.runtime_state(context(tmp_path))
    assert info["candidate_requested"] and not info["candidate_running"]


def test_atomic_runtime_snapshot_private_and_readable(tmp_path):
    dest=tmp_path/"secure"/"runtime.json"
    m.atomic_json(dest,{"version":"v33-69","running":True})
    assert json.loads(dest.read_text())["running"]
    assert stat.S_IMODE(dest.stat().st_mode)==0o600
    m.atomic_json(dest,{"version":"v33-69","running":False})
    assert not json.loads(dest.read_text())["running"]


def test_observation_cannot_trade():
    code=Path(m.__file__).read_text()
    for danger in ["create_order(", "close_position(", "cancel_order(",
                   "systemctl restart", "os.system("]:
        assert danger not in code


def test_staged_web_resumes_only_from_consumed_intent():
    code=(Path(m.__file__).parent/"web_app.py").read_text()
    assert "deployment_resume.consume(" in code
    assert 'data.get("config_sha256")' in code
    assert "engine_loop_health_verification_timeout" in code
    assert 'threading.Thread(target=runtime.monitor' in code
    assert 'ctx.candidate_c_run_mode = mode' in code

@pytest.mark.parametrize("mutation,expected", [
    ({},True),
    ({"pid":9090},True),
    ({"observed_at":100},False),
    ({"version":"other"},False),
    ({"core_requested":False,"candidate_requested":False},False),
    ({"core_running":False,"candidate_running":False},False),
    ({"username":"other"},False),
])
def test_previous_service_interruption_alert_is_observation_only(tmp_path,mutation,expected):
    ctx=context(tmp_path)
    checkpoint=m.runtime_state(ctx)
    current_now=checkpoint["observed_at"]+10
    checkpoint.update(mutation)
    assert m.interrupted_prior_owner(checkpoint,current_pid=os.getpid()+100,
        now=current_now) is expected

def test_no_startup_alert_for_same_process(tmp_path):
    checkpoint=m.runtime_state(context(tmp_path))
    assert not m.interrupted_prior_owner(checkpoint,current_pid=os.getpid(),
        now=checkpoint["observed_at"]+10)

def test_candidate_missing_display_refresh_thread_is_unhealthy(tmp_path):
    ctx=context(tmp_path)
    ctx.candidate_c_threads=ctx.candidate_c_threads[:2]
    observed=m.runtime_state(ctx)
    assert observed["candidate_requested"]
    assert not observed["candidate_running"]


def test_candidate_three_live_threads_is_healthy(tmp_path):
    observed=m.runtime_state(context(tmp_path))
    assert observed["candidate_requested"] and observed["candidate_running"]
    assert len(observed["candidate_checks"])==2
