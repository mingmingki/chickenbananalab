"""A privileged deployment must leave a resume intent readable by the service."""
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def operator(tmp_path, monkeypatch):
    roots = Path(__file__).resolve().parents
    path = next(p / "deployment_operator_v33_115.py" for p in [roots[1], roots[2]]
                if (p / "deployment_operator_v33_115.py").exists())
    spec = importlib.util.spec_from_file_location("resume_permission_operator", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.PENDING = tmp_path / "pending.json"
    monkeypatch.setattr(module, "run", lambda *args: "trader-service")
    monkeypatch.setattr(module, "pwd", SimpleNamespace(
        getpwnam=lambda name: SimpleNamespace(pw_uid=1234, pw_gid=4321)), raising=False)
    return module


def test_root_intent_is_owned_by_service_before_private_data_is_written(operator, monkeypatch):
    monkeypatch.setattr(operator.os, "geteuid", lambda: 0)
    changes = []
    def chown(fd, uid, gid):
        assert os.fstat(fd).st_size == 0
        changes.append((uid, gid))
    monkeypatch.setattr(operator.os, "fchown", chown)
    operator.write_intent({"core": True, "candidate_c": "live"})
    assert changes == [(1234, 4321)]
    assert operator.PENDING.stat().st_mode & 0o777 == 0o600
    assert json.loads(operator.PENDING.read_text()) == {"core": True, "candidate_c": "live"}


def test_unrelated_user_is_rejected_before_creating_unreadable_intent(operator, monkeypatch):
    monkeypatch.setattr(operator.os, "geteuid", lambda: 5678)
    with pytest.raises(PermissionError):
        operator.write_intent({"core": True})
    assert not operator.PENDING.exists()


def test_service_user_can_write_without_privileged_owner_change(operator, monkeypatch):
    monkeypatch.setattr(operator.os, "geteuid", lambda: 1234)
    def no_privileged_change(*args):
        raise AssertionError("service user must not need chown")
    monkeypatch.setattr(operator.os, "fchown", no_privileged_change)
    operator.write_intent({"core": True})
    assert json.loads(operator.PENDING.read_text()) == {"core": True}


def test_existing_resume_intent_is_never_overwritten(operator, monkeypatch):
    monkeypatch.setattr(operator.os, "geteuid", lambda: 1234)
    operator.PENDING.write_text("existing")
    with pytest.raises(FileExistsError):
        operator.write_intent({"core": True})
    assert operator.PENDING.read_text() == "existing"


def test_owner_change_failure_does_not_leave_unreadable_resume_intent(operator, monkeypatch):
    monkeypatch.setattr(operator.os, "geteuid", lambda: 0)
    def fail(*args):
        raise OSError("simulated owner change failure")
    monkeypatch.setattr(operator.os, "fchown", fail)
    with pytest.raises(OSError):
        operator.write_intent({"core": True})
    assert not operator.PENDING.exists()
