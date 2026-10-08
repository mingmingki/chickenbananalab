import pytest
import trade_learning_lifecycle as lifecycle
import web_app


def test_fast_canonical_close_without_type_keeps_economic_values(monkeypatch, tmp_path):
    close = {
        'symbol':'PI/USDT:USDT','side':'long','time':'2026-08-29T11:32:05',
        'pnl':0.22555,'fee':0.249970405,'okx_net_pnl':-0.024420405,
        '_group':'fast','reason':'time_exit',
    }
    monkeypatch.setattr(lifecycle.pnl_reconciliation, 'load_all_records', lambda _u: [close])
    monkeypatch.setattr(lifecycle.trade_log, 'load_realized_trades', lambda _u, dry_run=False: [])
    monkeypatch.setattr(lifecycle, '_journal', lambda _u: [])
    rows = lifecycle.build_completed_lifecycles(str(tmp_path))
    assert len(rows) == 1
    assert rows[0]['strategy_group'] == 'fast'
    assert rows[0]['gross_pnl'] == pytest.approx(0.22555)
    assert rows[0]['fee'] == pytest.approx(0.249970405)
    assert rows[0]['net_pnl'] == pytest.approx(-0.024420405)
    assert rows[0]['events'][0]['type'] == 'close'


def test_hold_separates_reduce_history_from_active_gate():
    assert hasattr(web_app, '_core_reduce_display_payload')
    review = {'action':'HOLD','confidence':0.9}
    rv2 = {
        'initial_contracts':100.0,'current_contracts':75.0,
        'cumulative_reduced_ratio':0.25,'reduce_stage':1,
        'last_reduction_order_time':'2026-09-27T17:45:51',
        'last_block_reason':'same_signal_persisting',
    }
    active, history, diagnostics = web_app._core_reduce_display_payload(
        review, rv2, {'two_macd_weakening':False}
    )
    assert active is None
    assert diagnostics is None
    assert history is not None
    assert history['cumulative_reduced_ratio'] == pytest.approx(0.25)
    assert history.get('last_block_reason') is None


def test_hold_without_real_reduction_exposes_no_reduce_payload():
    assert hasattr(web_app, '_core_reduce_display_payload')
    review = {'action':'HOLD'}
    rv2 = {'initial_contracts':100.0,'current_contracts':100.0,'cumulative_reduced_ratio':0.0,'reduce_stage':0,'last_block_reason':'stage1_5m_or_ai_conditions'}
    active, history, diagnostics = web_app._core_reduce_display_payload(review, rv2, {'two_macd_weakening':False})
    assert active is None
    assert history is None
    assert diagnostics is None


def test_non_hold_preserves_active_reduce_payload():
    assert hasattr(web_app, '_core_reduce_display_payload')
    review = {'action':'REDUCE_50'}
    rv2 = {'cumulative_reduced_ratio':0.25,'last_block_reason':'same_signal_persisting'}
    diag = {'two_macd_weakening':True}
    active, history, diagnostics = web_app._core_reduce_display_payload(review, rv2, diag)
    assert active is rv2
    assert history is None
    assert diagnostics is diag
