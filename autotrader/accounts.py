import datetime
import json
import os
import re
import threading

from werkzeug.security import check_password_hash, generate_password_hash

import config

_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,20}$")
_lock = threading.Lock()


def is_valid_username(username: str) -> bool:
    # 디렉터리 이름으로도 그대로 쓰이므로(경로 조작 방지) 영문/숫자/밑줄만 허용한다.
    return bool(_USERNAME_RE.match(username or ""))


def user_dir(username: str) -> str:
    return os.path.join(config.USERS_DIR, username)


def _load() -> dict:
    if not os.path.exists(config.ACCOUNTS_PATH):
        return {}
    try:
        with open(config.ACCOUNTS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save(accounts: dict) -> None:
    os.makedirs(config.PROJECT_DIR, exist_ok=True)
    with open(config.ACCOUNTS_PATH, "w", encoding="utf-8") as f:
        json.dump(accounts, f, ensure_ascii=False, indent=2)


def exists(username: str) -> bool:
    return username in _load()


def create(username: str, password: str) -> bool:
    """계정을 새로 만든다. 이미 있는 아이디면 False를 반환한다 (동시 가입 경합에도 안전).
    회원가입은 완전 공개지만, 아무나 가입 즉시 실거래를 시작할 수 있으면 안 되므로
    approved는 기본 False로 시작한다 - 관리자가 회원관리에서 승인해야 "시작" 버튼이
    풀린다 (로그인 자체는 승인과 무관하게 바로 가능 - 승인 대기 상태도 대시보드는 보여야
    본인이 상태를 확인할 수 있다)."""
    with _lock:
        accounts = _load()
        if username in accounts:
            return False
        accounts[username] = {
            "password_hash": generate_password_hash(password),
            "is_admin": False,
            "approved": False,
            "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
        }
        _save(accounts)
    os.makedirs(user_dir(username), exist_ok=True)
    return True


def verify(username: str, password: str) -> bool:
    entry = _load().get(username)
    if not entry:
        return False
    return check_password_hash(entry["password_hash"], password)


def is_admin(username: str) -> bool:
    entry = _load().get(username)
    return bool(entry and entry.get("is_admin"))


def is_approved(username: str) -> bool:
    """관리자는 승인 절차 자체가 필요 없으므로 항상 승인된 것으로 취급한다.
    일반회원은 회원가입 시 approved=False로 시작해서, 관리자가 회원관리에서
    approve()를 호출해야 True가 된다."""
    entry = _load().get(username)
    if not entry:
        return False
    if entry.get("is_admin"):
        return True
    return bool(entry.get("approved"))


def approve(username: str) -> bool:
    """관리자가 회원관리 목록에서 승인 버튼을 눌렀을 때 호출한다. 존재하지 않는 계정이면
    False."""
    with _lock:
        accounts = _load()
        if username not in accounts:
            return False
        accounts[username]["approved"] = True
        _save(accounts)
    return True


def list_accounts() -> list:
    """관리자 페이지용 - 아이디/가입일/관리자여부/승인여부만 반환한다 (비밀번호 해시 등은 제외)."""
    accounts = _load()
    out = []
    for username, entry in accounts.items():
        is_admin_ = bool(entry.get("is_admin"))
        out.append(
            {
                "username": username,
                "is_admin": is_admin_,
                "approved": is_admin_ or bool(entry.get("approved")),
                "created_at": entry.get("created_at"),
            }
        )
    out.sort(key=lambda a: a.get("created_at") or "")
    return out
