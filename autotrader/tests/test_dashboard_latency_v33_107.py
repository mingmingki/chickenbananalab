import importlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import core_pnl_correction_v33_67 as correction


def test_eligible_close_batch_resolves_account_directory_once(tmp_path, monkeypatch):
    calls = []
    original = Path.resolve
    def counted(path, *args, **kwargs):
        calls.append(str(path))
        return original(path, *args, **kwargs)
    correction.load_manifest.cache_clear()
    monkeypatch.setattr(Path, 'resolve', counted)
    row = dict(type='close', strategy_group='core', pnl_source='okx_realized',
               dry_run=False, execution_id='unmatched-close', pnl=4.0)
    for _ in range(40):
        assert correction.adjusted_row(str(tmp_path), row) is row
    assert len(calls) <= 1


def test_unchanged_file_reuses_projection_and_returns_isolated_values(tmp_path):
    cache = importlib.import_module('display_read_cache')
    path = tmp_path / 'rows.jsonl'
    path.write_text('{"value": 1}\n')
    calls = []
    def load():
        calls.append(1)
        return [json.loads(line) for line in path.read_text().splitlines()]
    first = cache.get(path, load, namespace='isolated')
    first[0]['value'] = 99
    assert cache.get(path, load, namespace='isolated') == [{'value': 1}]
    assert len(calls) == 1
    path.write_text('{"value": 2}\n')
    assert cache.get(path, load, namespace='isolated') == [{'value': 2}]
    assert len(calls) == 2
    replacement = tmp_path / 'replacement'
    replacement.write_text('{"value": 3}\n')
    replacement.replace(path)
    assert cache.get(path, load, namespace='isolated') == [{'value': 3}]


def test_concurrent_pollers_build_only_one_projection(tmp_path):
    cache = importlib.import_module('display_read_cache')
    path = tmp_path / 'rows'
    path.write_text('same')
    calls = []
    barrier = threading.Barrier(8)
    def load():
        calls.append(1)
        return {'ready': True}
    def read(_):
        barrier.wait()
        return cache.get(path, load, namespace='concurrent')
    with ThreadPoolExecutor(max_workers=8) as executor:
        assert list(executor.map(read, range(8))) == [{'ready': True}] * 8
    assert len(calls) == 1


def test_changed_during_read_is_not_published_as_current(tmp_path):
    cache = importlib.import_module('display_read_cache')
    path = tmp_path / 'rows'
    path.write_text('old')
    def changed():
        old = path.read_text()
        path.write_text('new')
        return old
    assert cache.get(path, changed, namespace='changing') == 'old'
    assert cache.get(path, path.read_text, namespace='changing') == 'new'
    path.unlink()
    assert cache.get(path, lambda: None, namespace='changing') is None

def test_blocked_projection_does_not_block_other_cached_key(tmp_path):
    cache = importlib.import_module('display_read_cache')
    a, b = tmp_path / 'a', tmp_path / 'b'
    a.write_text('a')
    b.write_text('b')
    assert cache.get(b, lambda: 'ready', namespace='nonblocking') == 'ready'
    entered, release = threading.Event(), threading.Event()
    def slow():
        entered.set()
        assert release.wait(3)
        return 'slow'
    with ThreadPoolExecutor(max_workers=2) as executor:
        pending = executor.submit(cache.get, a, slow, namespace='nonblocking')
        assert entered.wait(1)
        quick = executor.submit(cache.get, b, lambda: 'wrong', namespace='nonblocking')
        try:
            assert quick.result(timeout=0.5) == 'ready'
        finally:
            release.set()
        assert pending.result(timeout=2) == 'slow'


def test_middle_rewrite_with_unchanged_metadata_invalidates_projection(tmp_path, monkeypatch):
    cache = importlib.import_module('display_read_cache')
    path = tmp_path / 'large'
    path.write_bytes(b'A' * 20000)
    original_stat = cache.os.stat
    frozen_stat = original_stat(path)
    def frozen(target, *args, **kwargs):
        if str(target) == str(path):
            return frozen_stat
        return original_stat(target, *args, **kwargs)
    monkeypatch.setattr(cache.os, 'stat', frozen)
    assert cache.get(path, path.read_bytes, namespace='middle') == b'A' * 20000
    with path.open('r+b') as stream:
        stream.seek(10000)
        stream.write(b'B')
    expected = b'A' * 10000 + b'B' + b'A' * 9999
    assert cache.get(path, path.read_bytes, namespace='middle') == expected
