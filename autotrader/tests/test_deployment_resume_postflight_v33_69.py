"""v33-69 deployment-resume and postflight acceptance tests (no live API)."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import deployment_resume
from deployment_postflight_v33_69 import validate_result


def cfg():
    return SimpleNamespace(CORE_UNIFIED_MODE="ROLLBACK",EXECUTION_MODE="LIVE",
        GPT_ENTRY_GATE_ENABLED=True,CORE_EVENT_AI_ENABLED=False,HOLD_AUDIT_ENABLED=False,
        CANDIDATE_C_ENABLED=True,CANDIDATE_C_LIVE_EXECUTE=True)


def manifest(tmp_path, *, core=True,candidate="live",now=1000):
    return dict(release=str(tmp_path.resolve()),created_at=now,expires_at=now+240,
        username="chickenbananalab",core=core,core_mode="ROLLBACK",candidate_c=candidate)


@pytest.mark.parametrize("core,candidate",[(True,None),(True,"live"),(False,"live"),(False,"shadow")])
def test_consumes_only_authorized_modes(tmp_path,core,candidate):
    data=manifest(tmp_path,core=core,candidate=candidate)
    path=tmp_path/"pending.json";path.write_text(json.dumps(data))
    assert deployment_resume.consume(path,tmp_path,1001)==data
    assert not path.exists()
    assert (tmp_path/"pending.json.consumed").exists()
    assert deployment_resume.settings_match(data,cfg())


def test_rejects_no_requested_engines(tmp_path):
    data=manifest(tmp_path,core=False,candidate=None)
    path=tmp_path/"pending.json";path.write_text(json.dumps(data))
    with pytest.raises(ValueError,match="invalid_deployment_resume"):
        deployment_resume.consume(path,tmp_path,1001)
    assert path.exists()


@pytest.mark.parametrize("mutation",[
    {"created_at":10000,"expires_at":10200},
    {"expires_at":2000},
    {"username":"other"},
    {"core":1},
    {"candidate_c":"unknown"},
    {"release":"/tmp/wrong"}])
def test_rejects_unauthorized_replay(tmp_path,mutation):
    data=manifest(tmp_path);data.update(mutation)
    path=tmp_path/"pending.json";path.write_text(json.dumps(data))
    with pytest.raises(ValueError,match="invalid_deployment_resume"):
        deployment_resume.consume(path,tmp_path,1001)
    assert path.exists()


@pytest.mark.parametrize("core,candidate",[(True,False),(False,True),(True,True)])
def test_postflight_accepts_requested_engine_states(tmp_path,core,candidate):
    result=dict(version="v33-69",release=str(tmp_path.resolve()),success=True,
        pid=1012,requested_core=core,requested_candidate=candidate,
        core_running=core,candidate_running=candidate)
    assert validate_result(result,release=tmp_path,pid=1012)==(core,candidate)


@pytest.mark.parametrize("overrides",[
    {"pid":1013},
    {"success":False},
    {"core_running":False},
    {"requested_candidate":"false"},
    {"version":"v33-68"}])
def test_postflight_fails_closed(tmp_path,overrides):
    result=dict(version="v33-69",release=str(tmp_path.resolve()),success=True,
        pid=1012,requested_core=True,requested_candidate=True,
        core_running=True,candidate_running=True)
    result.update(overrides)
    with pytest.raises(ValueError):
        validate_result(result,release=tmp_path,pid=1012)
