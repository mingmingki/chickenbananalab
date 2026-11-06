"""Staff-only AI Research Lab for ChickenBananaLab Django website.

Separate data directory, no trading credentials, no order execution tools.
The optional inference adapter only uses an explicitly configured independent
OpenAI-compatible HTTPS model endpoint.
"""
from contextlib import contextmanager
from functools import wraps
import json
import os
from pathlib import Path
import secrets
import sqlite3
from urllib.parse import urlsplit
import urllib.error
import urllib.request

from django.conf import settings
from django.contrib.auth.views import redirect_to_login
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

MAX_UPLOAD = 8 * 1024 * 1024
EXTENSIONS = {".txt", ".md", ".csv", ".json", ".pdf", ".ifc"}


def _staff(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            if request.path.startswith("/api/"):
                return JsonResponse({"ok": False, "error": "로그인이 필요합니다."}, status=401)
            return redirect_to_login(request.get_full_path())
        if not request.user.is_staff:
            if request.path.startswith("/api/"):
                return JsonResponse({"ok": False, "error": "AI 연구소는 관리자 테스트 단계입니다."}, status=403)
            return HttpResponseForbidden("AI 연구소는 관리자 테스트 단계입니다.")
        return view(request, *args, **kwargs)
    return wrapper


def _root():
    path = Path(os.getenv("CBL_AI_DATA_DIR") or (Path(settings.BASE_DIR).parent / "cbl_ai_lab_private"))
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


@contextmanager
def _db():
    db = sqlite3.connect(_root() / "research.sqlite3", timeout=5)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=5000")
    db.execute("CREATE TABLE IF NOT EXISTS conversations (id TEXT PRIMARY KEY, owner_id INTEGER NOT NULL, title TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
    db.execute("CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
    db.execute("CREATE TABLE IF NOT EXISTS uploads (id TEXT PRIMARY KEY, owner_id INTEGER NOT NULL, filename TEXT NOT NULL, size INTEGER NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
    db.commit()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _owns(db, request, convo_id):
    return db.execute("SELECT 1 FROM conversations WHERE owner_id=? AND id=?", (request.user.pk, convo_id)).fetchone() is not None


def _data(request):
    if request.content_type != "application/json" or len(request.body) > 12000:
        return None
    try:
        result = json.loads(request.body)
        return result if isinstance(result, dict) else None
    except (UnicodeDecodeError, ValueError):
        return None


def _model_config():
    model = os.getenv("CBL_AI_MODEL", "").strip()
    base = os.getenv("CBL_AI_API_BASE_URL", "").strip().rstrip("/")
    token = os.getenv("CBL_AI_API_KEY", "")
    parsed = urlsplit(base)
    enabled = bool(model and parsed.scheme == "https" and parsed.hostname and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment)
    return enabled, model, base, token


def _ask_ai(messages):
    enabled, model, base, token = _model_config()
    if not enabled:
        raise ValueError("model not configured")
    body = json.dumps({"model": model, "messages": messages, "temperature": 0.3, "max_tokens": 700, "stream": False}, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(base + "/chat/completions", data=body, method="POST", headers=headers)
    with urllib.request.urlopen(req, timeout=12) as response:
        payload = response.read(1024 * 1024)
    result = json.loads(payload)
    answer = result["choices"][0]["message"]["content"]
    if not isinstance(answer, str) or not answer.strip():
        raise ValueError("model returned empty content")
    return answer[:16000]


@never_cache
@require_GET
@_staff
def page(request):
    return render(request, "core/ai_lab.html")


@never_cache
@require_GET
@_staff
def state(request):
    enabled, model, _, _ = _model_config()
    with _db() as db:
        conversations = db.execute("SELECT COUNT(*) FROM conversations WHERE owner_id=?", (request.user.pk,)).fetchone()[0]
        uploads = db.execute("SELECT COUNT(*) FROM uploads WHERE owner_id=?", (request.user.pk,)).fetchone()[0]
    return JsonResponse({"ok": True, "model_configured": enabled, "model": model if enabled else None,
                         "conversations": conversations, "uploads": uploads, "trading_access": False, "mode": "separate_ai_only"})


@never_cache
@_staff
def conversations(request):
    if request.method not in ("GET", "POST"):
        return JsonResponse({"ok": False, "error": "허용되지 않은 요청"}, status=405)
    with _db() as db:
        if request.method == "GET":
            rows = db.execute("SELECT id,title,created_at FROM conversations WHERE owner_id=? ORDER BY created_at DESC, rowid DESC LIMIT 100", (request.user.pk,)).fetchall()
            return JsonResponse({"ok": True, "conversations": [dict(x) for x in rows]})
        data = _data(request)
        if data is None:
            return JsonResponse({"ok": False, "error": "JSON 요청이 필요합니다."}, status=415)
        name = str(data.get("title") or "새 대화").strip()[:80] or "새 대화"
        convo_id = secrets.token_hex(16)
        db.execute("INSERT INTO conversations(id,owner_id,title) VALUES (?,?,?)", (convo_id,request.user.pk,name))
    return JsonResponse({"ok": True, "id": convo_id, "title": name}, status=201)


@never_cache
@require_GET
@_staff
def messages(request, conversation_id):
    with _db() as db:
        if not _owns(db, request, conversation_id):
            return JsonResponse({"ok": False, "error": "대화를 찾을 수 없습니다."}, status=404)
        rows = db.execute("SELECT role,content,created_at FROM messages WHERE conversation_id=? ORDER BY id ASC LIMIT 200", (conversation_id,)).fetchall()
    return JsonResponse({"ok": True, "messages": [dict(x) for x in rows]})


@never_cache
@require_POST
@_staff
def chat(request):
    data = _data(request)
    if data is None:
        return JsonResponse({"ok": False, "error": "JSON 요청이 필요합니다."}, status=415)
    convo_id = str(data.get("conversation_id") or "")
    prompt = str(data.get("message") or "").strip()
    if not prompt or len(prompt) > 4000:
        return JsonResponse({"ok": False, "error": "메시지는 1~4,000자로 입력해 주세요."}, status=400)
    if not _model_config()[0]:
        return JsonResponse({"ok": False, "error": "독립 AI 서버가 연결되지 않았습니다. 채팅 기능은 준비 중입니다."}, status=503)
    with _db() as db:
        if not _owns(db, request, convo_id):
            return JsonResponse({"ok": False, "error": "대화를 찾을 수 없습니다."}, status=404)
        history = db.execute("SELECT role,content FROM messages WHERE conversation_id=? ORDER BY id DESC LIMIT 12", (convo_id,)).fetchall()
        ai_messages = [{"role": "system", "content": "You are Chicken Banana AI, an independent research assistant. Never claim to have executed trades or tools. Respond in the user's language."}]
        ai_messages.extend(dict(role=r["role"],content=r["content"]) for r in reversed(history))
        ai_messages.append({"role": "user", "content": prompt})
        try:
            answer = _ask_ai(ai_messages)
        except (ValueError, KeyError, IndexError, urllib.error.URLError, TimeoutError, OSError):
            return JsonResponse({"ok": False, "error": "독립 AI 서버가 응답하지 않습니다. 거래 시스템에는 영향이 없습니다."}, status=502)
        db.execute("INSERT INTO messages(conversation_id,role,content) VALUES (?,?,?)", (convo_id,"user",prompt))
        db.execute("INSERT INTO messages(conversation_id,role,content) VALUES (?,?,?)", (convo_id,"assistant",answer))
    return JsonResponse({"ok": True, "answer": answer})


@never_cache
@_staff
def uploads(request):
    if request.method not in ("GET", "POST"):
        return JsonResponse({"ok": False, "error": "허용되지 않은 요청"}, status=405)
    with _db() as db:
        if request.method == "GET":
            rows = db.execute("SELECT id,filename,size,created_at FROM uploads WHERE owner_id=? ORDER BY created_at DESC LIMIT 100", (request.user.pk,)).fetchall()
            return JsonResponse({"ok": True, "uploads": [dict(x) for x in rows]})
        file = request.FILES.get("file")
        if not file or not file.name:
            return JsonResponse({"ok": False, "error": "파일이 없습니다."}, status=400)
        name = Path(file.name.replace("\\", "/")).name[:120]
        ext = Path(name).suffix.lower()
        if ext not in EXTENSIONS:
            return JsonResponse({"ok": False, "error": "TXT, MD, CSV, JSON, PDF, IFC만 지원합니다."}, status=415)
        if not 0 < file.size <= MAX_UPLOAD:
            return JsonResponse({"ok": False, "error": "파일 크기는 8MB 이하만 허용됩니다."}, status=413)
        uid = secrets.token_hex(16)
        folder = _root() / "uploads"
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        target = folder / (uid + ext)
        with open(target, "xb") as output:
            for chunk in file.chunks():
                output.write(chunk)
        os.chmod(target, 0o600)
        db.execute("INSERT INTO uploads(id,owner_id,filename,size) VALUES (?,?,?,?)", (uid,request.user.pk,name,file.size))
    return JsonResponse({"ok": True, "id": uid, "filename": name, "size": file.size}, status=201)
