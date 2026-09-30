"""프로세스 간 원자성 보장(2026-09-01, Phase 1.1) - exit_coordinator.py/
portfolio_risk.py/protective_exit.py가 공유하는 락 유틸리티.

production 실측(읽기 전용 재확인, 2026-09-01): waitress(threads=8) 단일 프로세스,
systemd Type=simple, cgroup.procs에 PID 하나뿐 - 현재 배포는 순수 멀티스레드
단일 프로세스라 threading.RLock만으로도 실제로는 충분하다. 그래도 배포 구조가
바뀌어도(예: gunicorn 멀티워커) 안전하도록 fcntl.flock 기반 프로세스 간 락을
추가로 건다 - 비용이 낮고, 사용자가 명시적으로 요구했다.

flock은 파일서술자 단위라 threading.RLock처럼 자동 재진입(같은 스레드가 같은
락을 중첩 호출)이 안 된다 - run_exit()이 내부에서 request_exit()/mark_submitted()
등을 중첩 호출하는 이 코드베이스 특성상, 재진입을 직접 처리하지 않으면 가장
안쪽 with 블록이 끝날 때 바깥쪽이 아직 쓰고 있는 락을 조기에 풀어버린다. 그래서
스레드+락키별 재진입 깊이를 직접 세어, 깊이가 0->1일 때만 실제로 flock을 걸고
1->0일 때만 실제로 푼다."""
import contextlib
import fcntl
import json
import os
import tempfile
import threading

_registry_lock = threading.Lock()
_rlocks: dict[str, threading.RLock] = {}
_rlock_refs: dict[str, int] = {}
_lock_fds: dict[str, int] = {}
_lock_depth: dict[str, int] = {}
_registry_pid = os.getpid()


def _reset_after_fork() -> None:
    """A forked child must not reuse the parent's open-file-description.

    ``flock`` locks are associated with that shared description, so retaining an
    inherited fd makes parent and child appear to be the same lock owner.  Thread
    locks/depth are process-local as well and may have been inherited while held.
    """
    global _registry_lock, _rlocks, _rlock_refs, _lock_fds, _lock_depth, _registry_pid
    inherited_fds = list(_lock_fds.values())
    _registry_lock = threading.Lock()
    _rlocks = {}
    _rlock_refs = {}
    _lock_fds = {}
    _lock_depth = {}
    _registry_pid = os.getpid()
    for fd in inherited_fds:
        try:
            os.close(fd)
        except OSError:
            pass


def _ensure_current_process() -> None:
    if _registry_pid != os.getpid():
        _reset_after_fork()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_after_fork)


def _rlock_for(lock_id: str) -> threading.RLock:
    _ensure_current_process()
    with _registry_lock:
        rlock = _rlocks.get(lock_id)
        if rlock is None:
            rlock = threading.RLock()
            _rlocks[lock_id] = rlock
        _rlock_refs[lock_id] = _rlock_refs.get(lock_id, 0) + 1
        return rlock


def _release_rlock_ref(lock_id: str, rlock: threading.RLock) -> None:
    with _registry_lock:
        refs = _rlock_refs.get(lock_id, 1) - 1
        if refs <= 0 and _rlocks.get(lock_id) is rlock:
            _rlock_refs.pop(lock_id, None)
            _rlocks.pop(lock_id, None)
        else:
            _rlock_refs[lock_id] = refs


def _fd_for(lock_path: str) -> int:
    _ensure_current_process()
    with _registry_lock:
        fd = _lock_fds.get(lock_path)
        if fd is None:
            os.makedirs(os.path.dirname(lock_path), exist_ok=True)
            fd = os.open(lock_path, os.O_CREAT | os.O_RDWR)
            _lock_fds[lock_path] = fd
        return fd


@contextlib.contextmanager
def locked(user_dir: str, key: str):
    """스레드 재진입 안전(RLock, 같은 스레드의 중첩 호출 허용) + 프로세스 간
    배제(flock, 다른 OS 프로세스가 같은 account/key를 동시에 처리하는 것 방지)를
    함께 제공하는 단일 critical section. lock 파일은 user_dir 아래
    .<key>.lock이다 - account(=user_dir)별로 완전히 격리된다."""
    lock_id = f"{user_dir}::{key}"
    lock_path = os.path.join(user_dir, f".{key}.lock")
    owner_pid = os.getpid()
    rlock = _rlock_for(lock_id)
    try:
        with rlock:
            depth = _lock_depth.get(lock_id, 0)
            fd = _lock_fds.get(lock_path)
            if depth == 0:
                fd = _fd_for(lock_path)
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX)
                except Exception:
                    with _registry_lock:
                        _lock_fds.pop(lock_path, None)
                    os.close(fd)
                    raise
            _lock_depth[lock_id] = depth + 1
            try:
                yield
            finally:
                if os.getpid() == owner_pid:
                    new_depth = _lock_depth[lock_id] - 1
                    if new_depth == 0:
                        try:
                            fcntl.flock(fd, fcntl.LOCK_UN)
                        finally:
                            os.close(fd)
                            with _registry_lock:
                                _lock_fds.pop(lock_path, None)
                                _lock_depth.pop(lock_id, None)
                    else:
                        _lock_depth[lock_id] = new_depth
                # A direct fork may occur inside ``yield``. The at-fork hook
                # has already closed/reset the child's inherited registry; its
                # inherited context must not touch the new registry or the
                # parent's open-file-description while unwinding.
    finally:
        if os.getpid() == owner_pid:
            _release_rlock_ref(lock_id, rlock)


def save_json_atomic(path: str, data: dict) -> None:
    """temp file 작성 -> flush -> fsync -> atomic rename. fsync 없이 os.replace만
    하면, rename 자체는 원자적이어도 그 내용이 아직 OS 버퍼에만 있고 디스크에
    반영되기 전에 크래시가 나면(정전/kill -9/VM 재부팅) 파일 내용이 유실되거나
    잘릴 수 있다."""
    encoded = json.dumps(
        data, ensure_ascii=False, allow_nan=False,
    ).encode("utf-8")
    save_bytes_atomic(path, encoded)


def _fsync_parent_directory(path: str) -> None:
    directory = os.path.dirname(path) or "."
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    fd = os.open(directory, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def save_bytes_atomic(path: str, data: bytes) -> None:
    """Durable replacement: file fsync -> rename -> parent-directory fsync."""
    if not isinstance(data, bytes):
        raise TypeError("atomic payload must be bytes")
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=f".{os.path.basename(path)}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
        _fsync_parent_directory(path)
    except Exception:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


def append_jsonl_atomic(path: str, record: dict) -> None:
    """Append one JSON row through a durable whole-file replacement.

    Callers must hold their state lock.  Runtime config logs are small control
    records; replacing their complete bytes avoids a valid-looking partial
    append while preserving append-only logical semantics.
    """
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    previous = b""
    if os.path.exists(path):
        with open(path, "rb") as f:
            previous = f.read()
    row = json.dumps(
        record, ensure_ascii=False, sort_keys=True, allow_nan=False,
    ).encode("utf-8") + b"\n"
    save_bytes_atomic(path, previous + row)


def load_json_or_default(path: str, default_factory, *, corrupted_error_prefix: str):
    """손상되거나 잘린 파일이면 조용히 빈 상태로 시작하지 않고 예외를 던진다
    (fail-closed) - 어떤 상태 파일이었는지는 호출부가 메시지 접두어로 구분한다."""
    if not os.path.exists(path):
        return default_factory()
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(
                f,
                parse_constant=lambda value: (_ for _ in ()).throw(
                    ValueError(f"non-standard JSON constant: {value}")
                ),
            )
    except (json.JSONDecodeError, OSError, ValueError) as exc:
        raise RuntimeError(f"{corrupted_error_prefix} 손상 - 수동 확인 필요: {path} ({exc})") from exc


def append_jsonl(path: str, record: dict, *, fsync: bool = True) -> None:
    """append-only 로그에 한 줄을 쓰고 필요하면 즉시 fsync한다. 상태 파일(state)만큼
    치명적이지는 않지만(진짜 source of truth는 state 파일), 감사 추적 유실을
    줄이기 위해 기본적으로 fsync한다."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
        f.flush()
        if fsync:
            os.fsync(f.fileno())


def load_jsonl_skip_corrupted(path: str) -> list:
    """줄 단위 JSONL을 읽되, 손상되거나 잘린 마지막 줄(예: 쓰는 도중 크래시)은
    조용히 건너뛴다 - 로그는 append-only 감사 추적이라 한 줄 손상이 전체를
    막으면 안 된다(state 파일과 다른 정책 - 그쪽은 fail-closed)."""
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows
