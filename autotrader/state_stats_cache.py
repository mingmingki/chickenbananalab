"""Bounded per-user cache for expensive display-only summaries.

No live positions, risk guards, orders, balances or execution state are cached.
Stale-while-revalidate prevents bursts of UI readers from triggering parallel
ledger scans. Initial cold miss runs synchronously, subsequent refresh in daemon.
"""
import threading
import time

_LOCK = threading.Lock()
_DATA = {}
_INFLIGHT = set()
_TTL_SECONDS = 25
_MAX_STALE_SECONDS = 120


def get(user_dir, factory, *, now=None):
    key = str(user_dir)
    when = time.monotonic() if now is None else float(now)
    with _LOCK:
        item = _DATA.get(key)
        if item and when < item[0]:
            return item[1]
        if item and when < item[2] and key not in _INFLIGHT:
            _INFLIGHT.add(key)
            threading.Thread(target=_refresh, args=(key, factory), daemon=True,
                             name="state-stat-refresh").start()
            return item[1]
        if item and when < item[2]:
            return item[1]
        # At cold boot/stale beyond limit compute once instead of returning
        # fabricated accounting figures. Repeated callers still wait on a lock
        # rather than concurrently calling the factory.
    with _initial_lock(key):
        when = time.monotonic()
        with _LOCK:
            item = _DATA.get(key)
            if item and when < item[2]:
                return item[1]
        value = factory()
        with _LOCK:
            when = time.monotonic()
            _DATA[key] = (when + _TTL_SECONDS, value, when + _MAX_STALE_SECONDS)
        return value


_KEY_LOCKS = {}
class _initial_lock:
    def __init__(self, key):
        with _LOCK:
            self.lock = _KEY_LOCKS.setdefault(key, threading.Lock())
    def __enter__(self):
        self.lock.acquire()
    def __exit__(self, *_):
        self.lock.release()


def _refresh(key, factory):
    try:
        value = factory()
        with _LOCK:
            when = time.monotonic()
            _DATA[key] = (when + _TTL_SECONDS, value, when + _MAX_STALE_SECONDS)
    except Exception:
        # Never publish partial, invalid summary; the previous value remains.
        pass
    finally:
        with _LOCK:
            _INFLIGHT.discard(key)


def clear(user_dir=None):
    with _LOCK:
        if user_dir is None:
            _DATA.clear()
        else:
            _DATA.pop(str(user_dir), None)
