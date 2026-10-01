from pathlib import Path
from types import SimpleNamespace
import logging

import web_app


def _client():
    client = web_app.app.test_client()
    with client.session_transaction() as sess:
        sess['authenticated'] = True
        sess['username'] = 'tester'
    return client


def test_log_ring_buffer_delta_survives_rotation():
    handler = web_app.LogRingBuffer(maxlen=2)
    for message in ['one', 'two', 'three']:
        rec = logging.LogRecord('t', logging.INFO, __file__, 1, message, (), None)
        handler.emit(rec)
    logs, cursor, reset = handler.get_since(2)
    assert logs == ['three']
    assert cursor == 3
    assert reset is False
    logs, cursor, reset = handler.get_since(0)
    assert logs == ['two', 'three']
    assert cursor == 3
    assert reset is True


def test_logs_api_returns_only_delta_after_cursor(monkeypatch, tmp_path):
    handler = web_app.LogRingBuffer(maxlen=10)
    handler.setFormatter(logging.Formatter('%(message)s'))
    for message in ['one', 'two']:
        handler.emit(logging.LogRecord('t', logging.INFO, __file__, 1, message, (), None))
    cfg = SimpleNamespace(user_dir=str(tmp_path))
    ctx = SimpleNamespace(log_handler=handler, cfg=cfg)
    monkeypatch.setattr(web_app, 'get_context', lambda _u: ctx)
    client = _client()
    first = client.get('/api/logs')
    assert first.status_code == 200
    assert first.get_json()['logs'] == ['one', 'two']
    cursor = first.get_json()['next_cursor']
    handler.emit(logging.LogRecord('t', logging.INFO, __file__, 1, 'three', (), None))
    second = client.get(f'/api/logs?cursor={cursor}')
    assert second.status_code == 200
    data = second.get_json()
    assert data['logs'] == ['three']
    assert data['next_cursor'] > cursor
    assert data['reset'] is False


def test_dashboard_uses_delta_logs_conditional_fetch_and_slower_heavy_polls():
    text = Path('templates/dashboard.html').read_text(encoding='utf-8')
    assert 'let logCursor = null;' in text
    assert 'If-None-Match' in text
    assert 'response.status === 304' in text
    assert 'guardedPoll(refreshState, 2000);' in text
    assert 'guardedPoll(refreshLogs, 10000);' in text
    assert 'guardedPoll(refreshTradesFiltered, 60000);' in text
    assert 'guardedPoll(refreshShadow, 30000);' in text
    assert 'guardedPoll(refreshPnlSummary, 15000);' in text
    assert 'guardedPoll(refreshCandidateC, 5000);' in text


def test_shadow_api_returns_304_for_matching_etag(monkeypatch, tmp_path):
    ctx = SimpleNamespace(dir=str(tmp_path))
    monkeypatch.setattr(web_app, 'get_context', lambda _u: ctx)
    monkeypatch.setattr(web_app.gpt_shadow_log, 'summary', lambda _u: {
        'gate_count': 1, 'shadow_mode_count': 2, 'no_mode_count': 3,
    })
    monkeypatch.setattr(web_app.gpt_shadow_log, 'recent_by_mode', lambda *_a, **_k: [
        {'time': '2026-10-02T00:00:00', 'gpt_decision': 'approve_now'}
    ])
    client = _client()
    first = client.get('/api/shadow')
    assert first.status_code == 200
    etag = first.headers.get('ETag')
    assert etag
    second = client.get('/api/shadow', headers={'If-None-Match': etag})
    assert second.status_code == 304
    assert second.data == b''


def test_trades_filtered_returns_304_for_matching_etag(monkeypatch, tmp_path):
    state = SimpleNamespace(snapshot=lambda: {'baseline_equity': 1000.0})
    ctx = SimpleNamespace(dir=str(tmp_path), state=state)
    monkeypatch.setattr(web_app, 'get_context', lambda _u: ctx)
    rows = [{'time': '2026-10-02T00:00:00', 'pnl': 1.0, 'type': 'close'}]
    monkeypatch.setattr(web_app.pnl_reconciliation, 'load_all_records_with_reduces', lambda _u: rows)
    monkeypatch.setattr(web_app.pnl_reconciliation, 'filter_records', lambda records, **_k: list(records))
    monkeypatch.setattr(web_app.pnl_reconciliation, 'period_breakdown_full', lambda *_a, **_k: {
        'daily': [], 'monthly': [], 'yearly': []
    })
    match = {'record_count': 1, 'matched_count': 0, 'unmatched_count': 1}
    monkeypatch.setattr(web_app.okx_margin_return, 'enrich_trade_records', lambda _u, records: (list(records), match))
    monkeypatch.setattr(web_app.okx_margin_return, 'period_fixed_returns_from_records', lambda _r: {'daily': {}, 'monthly': {}, 'yearly': {}})
    monkeypatch.setattr(web_app.okx_margin_return, 'fixed_return_summary_from_records', lambda _r: {'fixed_return_pct': None})
    monkeypatch.setattr(web_app.okx_margin_return, 'cached_summary', lambda _u: {'complete': True})
    client = _client()
    first = client.get('/api/trades_filtered?group=all&symbol=all&variant=all')
    assert first.status_code == 200
    etag = first.headers.get('ETag')
    assert etag
    second = client.get('/api/trades_filtered?group=all&symbol=all&variant=all', headers={'If-None-Match': etag})
    assert second.status_code == 304
    assert second.data == b''
