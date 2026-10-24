"""Content-verified cache for read-only dashboard and analytics projections.

Never used for order execution, risk checks, or exchange observations.
Every caller receives an isolated value; every lookup verifies the whole input.
"""
import copy
import hashlib
import os
import threading
from collections import OrderedDict
from contextlib import contextmanager

_LOCK = threading.Lock()
_CACHE = OrderedDict()
_FLIGHTS = {}
_MAX_ENTRIES = 32


def _signature(path):
    try:
        s = os.stat(path)
        digest = hashlib.sha256()
        with open(path, "rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except FileNotFoundError:
        return None
    return s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns, digest.digest()


@contextmanager
def _single_flight(key):
    with _LOCK:
        flight = _FLIGHTS.setdefault(key, [threading.Lock(), 0])
        flight[1] += 1
    try:
        with flight[0]:
            yield
    finally:
        with _LOCK:
            flight[1] -= 1
            if flight[1] == 0:
                _FLIGHTS.pop(key, None)


def get(path, loader, *, namespace):
    path = os.path.abspath(os.fspath(path))
    key = (namespace, path)
    with _single_flight(key):
        before = _signature(path)
        with _LOCK:
            item = _CACHE.get(key)
            if before is not None and item is not None and item[0] == before:
                _CACHE.move_to_end(key)
            else:
                _CACHE.pop(key, None)
                item = None
        if item is not None:
            return copy.deepcopy(item[1])
        value = loader()
        if before is not None and before == _signature(path):
            saved = copy.deepcopy(value)
            with _LOCK:
                _CACHE[key] = (before, saved)
                while len(_CACHE) > _MAX_ENTRIES:
                    _CACHE.popitem(last=False)
        return value
