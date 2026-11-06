"""Smoke tests for AI Lab isolated Django routes and owner data."""
import io
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("DJANGO_DEBUG", "True")
import django
django.setup()

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory
from django.urls import resolve
from core import ai_lab_views as lab


def user(uid=1, staff=True, authed=True):
    return SimpleNamespace(pk=uid, is_staff=staff, is_authenticated=authed, username="tester")


def req(method, path, account=None, data=None, as_json=True):
    factory = RequestFactory()
    if method == "GET":
        r = factory.get(path)
    elif as_json:
        r = factory.post(path, data=json.dumps(data or {}), content_type="application/json")
    else:
        r = factory.post(path, data=data or {})
    r.user = account if account is not None else user(authed=False)
    return r


def decode(response):
    return json.loads(response.content)


with TemporaryDirectory(prefix="cbl-ai-test-") as private:
    with patch.dict(os.environ, {"CBL_AI_DATA_DIR": private, "CBL_AI_MODEL": "", "CBL_AI_API_BASE_URL": ""}):
        assert resolve("/ai-lab/").func == lab.page
        assert resolve("/api/ai-lab/state/").func == lab.state
        assert lab.state(req("GET","/api/ai-lab/state/")).status_code == 401
        assert lab.state(req("GET","/api/ai-lab/state/",user(2,staff=False))).status_code == 403
        assert lab.page(req("GET","/ai-lab/",user())).status_code == 200
        state = decode(lab.state(req("GET","/api/ai-lab/state/",user())))
        assert state["model_configured"] is False and state["trading_access"] is False
        created = lab.conversations(req("POST","/api/ai-lab/conversations/",user(),{"title":"BIM research"}))
        assert created.status_code == 201, created.content
        cid = decode(created)["id"]
        assert len(decode(lab.conversations(req("GET","/api/ai-lab/conversations/",user())))["conversations"]) == 1
        assert len(decode(lab.conversations(req("GET","/api/ai-lab/conversations/",user(2))))["conversations"]) == 0
        assert lab.messages(req("GET","/api/ai-lab/conversations/"+cid+"/",user(2)), cid).status_code == 404
        assert lab.chat(req("POST","/api/ai-lab/chat/",user(),{"conversation_id":cid,"message":"hello"})).status_code == 503
        assert decode(lab.messages(req("GET","/api/ai-lab/conversations/"+cid+"/",user()),cid))["messages"] == []
        file = SimpleUploadedFile("sample.ifc", b"IFC demo",content_type="application/octet-stream")
        upload = lab.uploads(req("POST","/api/ai-lab/uploads/",user(),{"file":file},as_json=False))
        assert upload.status_code == 201, upload.content
        info = decode(upload)
        assert info["filename"] == "sample.ifc"
        assert len(decode(lab.uploads(req("GET","/api/ai-lab/uploads/",user(2))))["uploads"]) == 0
        assert (Path(private) / "uploads" / (info["id"] + ".ifc")).exists()
        invalid = SimpleUploadedFile("danger.py", b"print('hi')")
        assert lab.uploads(req("POST","/api/ai-lab/uploads/",user(),{"file":invalid},as_json=False)).status_code == 415
        assert lab.conversations(req("POST","/api/ai-lab/conversations/",user(),{},as_json=False)).status_code == 415
print("AI Lab smoke tests passed: routes, Django rendering, staff gate, owner isolation, no-model chat, uploads, formats.")
