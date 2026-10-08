import threading
import time
from types import SimpleNamespace

import pytest

import okx_read_pacing as pacing
import okx_client


def setup_function():
    pacing._reset_for_tests()


def test_three_concurrent_protection_reads_are_serialized():
    starts = []
    guard = threading.Lock()
    barrier = threading.Barrier(3)

    def worker():
        barrier.wait()
        def network():
            with guard:
                starts.append(time.monotonic())
            time.sleep(0.01)
            return 'ok'
        assert pacing.paced_pending_algo_read(network, min_spacing_seconds=0.05) == 'ok'

    threads = [threading.Thread(target=worker) for _ in range(3)]
    for t in threads: t.start()
    for t in threads: t.join(timeout=2)
    assert len(starts) == 3
    starts = sorted(starts)
    gaps = [b-a for a,b in zip(starts, starts[1:])]
    assert min(gaps) >= 0.045


def test_exception_releases_lock_and_next_caller_runs():
    calls = []
    def boom():
        calls.append('boom')
        raise RuntimeError('429')
    with pytest.raises(RuntimeError, match='429'):
        pacing.paced_pending_algo_read(boom, min_spacing_seconds=0.0)
    assert pacing.paced_pending_algo_read(lambda: calls.append('next') or 7, min_spacing_seconds=0.0) == 7
    assert calls == ['boom', 'next']


def _client_with_exchange(exchange):
    client = okx_client.OkxClient.__new__(okx_client.OkxClient)
    client.symbol = 'XRP/USDT:USDT'
    client.exchange = exchange
    client.cfg = SimpleNamespace(logger=None)
    client.ensure_markets_loaded = lambda: None
    return client


def test_fetch_pending_protection_wraps_oco_and_conditional_in_one_paced_window(monkeypatch):
    calls = []
    class Exchange:
        def market(self, symbol): return {'id':'XRP-USDT-SWAP'}
        def private_get_trade_orders_algo_pending(self, params):
            calls.append(params['ordType'])
            return {'data':[{'ordType':params['ordType']}]}
    windows = []
    def paced(fn, *, min_spacing_seconds):
        windows.append(min_spacing_seconds)
        return fn()
    monkeypatch.setattr(okx_client.okx_read_pacing, 'paced_pending_algo_read', paced)
    client = _client_with_exchange(Exchange())
    rows = client.fetch_pending_protection_orders()
    assert calls == ['oco','conditional']
    assert len(windows) == 1
    assert [r['ordType'] for r in rows] == ['oco','conditional']


def test_rate_limit_exception_is_not_converted_to_verified(monkeypatch):
    class RateLimitError(Exception): pass
    class Exchange:
        def market(self, symbol): return {'id':'XRP-USDT-SWAP'}
        def private_get_trade_orders_algo_pending(self, params):
            raise RateLimitError('50011 Too Many Requests')
    monkeypatch.setattr(okx_client.okx_read_pacing, 'paced_pending_algo_read', lambda fn, **k: fn())
    client = _client_with_exchange(Exchange())
    with pytest.raises(RateLimitError, match='50011'):
        client.fetch_pending_protection_orders()
