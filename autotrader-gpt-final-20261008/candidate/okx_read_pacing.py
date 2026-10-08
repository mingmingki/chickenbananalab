"""Process-wide pacing for OKX pending protection reads only."""
from __future__ import annotations

import threading
import time

_LOCK = threading.Lock()
_LAST_STARTED = None


def paced_pending_algo_read(callable_, *, min_spacing_seconds):
    """Serialize a pending-algo read window and preserve underlying exceptions."""
    global _LAST_STARTED
    spacing = max(0.0, float(min_spacing_seconds or 0.0))
    with _LOCK:
        now = time.monotonic()
        if _LAST_STARTED is not None:
            remaining = spacing - (now - _LAST_STARTED)
            if remaining > 0:
                time.sleep(remaining)
        _LAST_STARTED = time.monotonic()
        return callable_()


def _reset_for_tests():
    global _LAST_STARTED
    with _LOCK:
        _LAST_STARTED = None
