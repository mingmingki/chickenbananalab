"""Actual OCO cause survives reductions; foreign or incomplete evidence stays unknown."""
import copy
import json
from pathlib import Path

import pytest
from types import SimpleNamespace
import logging
import sqlite3
import trader
import trade_log
import candidate_c_cycle_reconciliation as cycle_recon

import okx_client


CAPTURE = json.loads((Path(__file__).parent / 'fixtures' / 'pnl_owned_close_capture.json').read_text())


@pytest.mark.parametrize('case', CAPTURE, ids=lambda c: c['entry_order']['ordId'])
def test_captured_oco_fill_has_exact_owned_cause(case):
    evidence = okx_client.match_owned_protection_close(
        case['position'], case['entry_order'], case['algo_history'],
        case['exit_order'], case['fills'], **case['owner'])
    assert evidence['reason'] == case['expected']
    assert evidence['order_id'] == case['exit_order']['ordId']
    assert evidence['algo_id'] == case['algo_history']['algoId']
    assert evidence['source'] == 'owned_algo_order_fills'


@pytest.mark.parametrize('path,value', [
    (('owner', 'bound_lifecycle'), '3853548417900597249:1:short'),
    (('position', 'entry_timestamp_ms'), 1),
    (('entry_order', 'ordId'), 'foreign'),
    (('entry_order', 'clOrdId'), 'foreign'),
    (('algo_history', 'algoClOrdId'), 'foreign'),
    (('algo_history', 'state'), 'canceled'),
    (('algo_history', 'actualSide'), ''),
    (('algo_history', 'triggerTime'), '1'),
    (('exit_order', 'algoId'), 'foreign'),
    (('exit_order', 'accFillSz'), '0'),
    (('position', 'contracts'), 99),
    (('fills',), []),
])
def test_incomplete_or_mismatched_evidence_cannot_assign_owned_cause(path, value):
    case = copy.deepcopy(CAPTURE[-1])
    target = case
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    evidence = okx_client.match_owned_protection_close(
        case['position'], case['entry_order'], case['algo_history'],
        case['exit_order'], case['fills'], **case['owner'])
    assert evidence is None


def test_fill_from_another_order_cannot_complete_proof():
    case = copy.deepcopy(CAPTURE[-1])
    case['fills'][0]['ordId'] = 'foreign'
    assert okx_client.match_owned_protection_close(
        case['position'], case['entry_order'], case['algo_history'],
        case['exit_order'], case['fills'], **case['owner']) is None


def test_read_only_client_collects_owned_historical_order_and_fills():
    case = copy.deepcopy(CAPTURE[-1])
    orders = {r['ordId']: {'info': r} for r in (case['entry_order'], case['exit_order'])}
    exchange = SimpleNamespace(
        fetch_order=lambda order_id, symbol: orders[order_id],
        private_get_trade_orders_algo_history=lambda params: {'data': [case['algo_history']]},
        fetch_my_trades=lambda symbol, since, limit, params: [{'info': r} for r in case['fills']],
    )
    client = okx_client.OkxClient.__new__(okx_client.OkxClient)
    client.symbol = case['position']['symbol']
    client.cfg = SimpleNamespace(logger=logging.getLogger('test'))
    client.exchange = exchange
    evidence = client.fetch_owned_protection_close(case['position'], **case['owner'])
    assert evidence['reason'] == 'stop_loss'
    assert evidence['order_id'] == '4004783766252539904'


def test_core_reduced_close_keeps_residual_pnl_and_recovers_exact_reason(tmp_path):
    case = copy.deepcopy(CAPTURE[-1])
    decision = 'BTC/USDT:USDT-20261013100143004129'
    with sqlite3.connect(tmp_path / 'core_entry_events.sqlite3') as db:
        db.execute('CREATE TABLE entry_orders(decision_id TEXT,symbol TEXT,client_order_id TEXT,payload TEXT,status TEXT)')
        db.execute('INSERT INTO entry_orders VALUES (?,?,?,?,?)',
                   (decision, case['position']['symbol'], case['owner']['entry_client_order_id'],
                    json.dumps({'bound_position_identity': case['owner']['bound_lifecycle'],
                                'order_id': case['owner']['entry_order_id']}), 'FILLED'))
    open_row = {'execution_id': 'core-entry:' + decision, 'time': '2026-10-13T10:02:22',
                'symbol': case['position']['symbol'], 'side': 'short', 'sl_price': 82050., 'tp_price': 80450.}
    reduce = {'type': 'reduce', 'symbol': case['position']['symbol'], 'side': 'short',
              'entry_price': 81516.5, 'pnl': -2.71049, 'fee': 0.392634445,
              'time': '2026-10-13T10:15:39', 'dry_run': False}
    (tmp_path / 'trades_log.jsonl').write_text(json.dumps(reduce) + '\n')
    client = okx_client.OkxClient.__new__(okx_client.OkxClient)
    client.symbol = case['position']['symbol']
    client.cfg = SimpleNamespace(logger=logging.getLogger('test'))
    orders = {r['ordId']: {'info': r} for r in (case['entry_order'], case['exit_order'])}
    client.exchange = SimpleNamespace(
        fetch_order=lambda order_id, symbol: orders[order_id],
        private_get_trade_orders_algo_history=lambda params: {'data': [case['algo_history']]},
        fetch_my_trades=lambda symbol, since, limit, params: [{'info': r} for r in case['fills']],
    )
    client.fetch_last_realized_close = lambda side, entry: {
        'gross_pnl': -13.23478, 'fee': 2.36244424, 'net_pnl': -15.59722424,
        'exit_price': 81900., 'funding_fee': 0., 'source': 'okx_realized'}
    cfg = SimpleNamespace(user_dir=str(tmp_path), logger=client.cfg.logger)
    position = dict(case['position'], entry_price=81516.5)
    resolved = trader._resolve_external_close_pnl(cfg, client, client.symbol, position, open_record=open_row)
    assert resolved['net_pnl'] == pytest.approx(-12.494099795)
    assert resolved['fee'] == pytest.approx(1.969809795)
    assert resolved['exit_price'] == pytest.approx(float(case['fills'][0]['fillPx']))
    assert resolved['close_execution_evidence']['order_id'] == case['exit_order']['ordId']
    assert resolved['close_execution_evidence']['trade_ids'] == [
        item['tradeId'] for item in case['fills']]
    assert resolved['close_reason_evidence']['reason'] == 'stop_loss'
    assert trader._classify_close_reason(open_row, None, resolved['close_reason_evidence']) == 'stop_loss'


def test_close_journal_preserves_exact_attribution_ids(tmp_path):
    evidence = {'reason': 'stop_loss', 'source': 'owned_algo_order_fills',
                'algo_id': '4004753024699179008', 'order_id': '4004783766252539904',
                'trade_ids': ['2990160518'],
                'lifecycle_id': '3853548417900597249:1791853340724:short'}
    trade_log.record_close(str(tmp_path), 'BTC/USDT:USDT', 'short', 81516.5, 1.93,
                           -10.52429, 'stop_loss', False,
                           close_reason_evidence=evidence)
    row = json.loads((tmp_path / 'trades_log.jsonl').read_text())
    assert row['close_reason_evidence'] == evidence


@pytest.mark.parametrize('price', [82050., 80450.])
def test_price_alone_cannot_assign_core_stop_or_target(price):
    assert trader._classify_close_reason({'sl_price': 82050., 'tp_price': 80450.}, price) == 'external_close_unknown'


def test_period_win_rate_counts_net_losses_after_fees(tmp_path):
    rows = [
        {'type': 'close', 'symbol': 'ETH/USDT:USDT', 'side': 'short', 'pnl': 0.99225,
         'fee': 1.667449665, 'okx_net_pnl': -0.675199665, 'dry_run': False, 'time': '2026-10-13T04:53:31'},
        {'type': 'close', 'symbol': 'ADA/USDT:USDT', 'side': 'short', 'pnl': 0.88,
         'fee': 1.60204, 'okx_net_pnl': -0.508904, 'dry_run': False, 'time': '2026-10-13T04:54:16'},
    ]
    (tmp_path / 'trades_log.jsonl').write_text('\n'.join(json.dumps(r) for r in rows) + '\n')
    daily = trade_log.period_breakdown(str(tmp_path), dry_run=False)['daily'][0]
    assert daily['win_rate'] == 0.
    assert daily['net_pnl'] == pytest.approx(-1.184103665)


@pytest.mark.parametrize('exact', [True, False])
def test_candidate_flat_reconciliation_uses_exact_order_chain(tmp_path, monkeypatch, exact):
    case = copy.deepcopy(CAPTURE[0])
    symbol = case['position']['symbol']
    record = SimpleNamespace(intent_id='candidate-captured', kind='EntryIntent',
        remote_submission_finalized=True, exchange_order_id=case['owner']['entry_order_id'],
        cl_ord_id=case['owner']['entry_client_order_id'],
        protective_algo_ids=[case['algo_history']['algoId']], attach_algo_cl_ord_id='')
    epoch = SimpleNamespace(entry_intent_id=record.intent_id, side='long', raw_entry_price=0.09748,
        exchange_position_id=case['position']['position_id'],
        exchange_entry_timestamp_ms=case['position']['entry_timestamp_ms'],
        original_contracts=12.82, remaining_contracts=6.41,
        realized_partial_pnl_usdt=-19.3582, realized_partial_fee_usdt=0.,
        current_stop_price=0.09358, target_price=0.10858)
    state = SimpleNamespace(
        intent_ledger=SimpleNamespace(find_protected_entry=lambda: (record, False),
            pending_intents=lambda: [], mark_terminal=lambda _: None),
        epoch_store=SimpleNamespace(get=lambda _: epoch, discard=lambda _: None),
        reversal_store=SimpleNamespace(get=lambda _: SimpleNamespace(converge_authoritative_flat=lambda _: None),
            persist=lambda *args: None), notification_events=[], notification_store=None)
    orders = {r['ordId']: {'info': r} for r in (case['entry_order'], case['exit_order'])}
    client = okx_client.OkxClient.__new__(okx_client.OkxClient)
    client.symbol = symbol
    client.cfg = SimpleNamespace(logger=logging.getLogger('test'))
    client.exchange = SimpleNamespace(fetch_order=lambda order_id, symbol: orders[order_id],
        fetch_open_orders=lambda _: [],
        private_get_trade_orders_algo_history=lambda params: {'data': [case['algo_history']]},
        fetch_my_trades=lambda symbol, since, limit, params: [{'info': r} for r in case['fills']] if exact else [])
    client.fetch_position = lambda: None
    client.fetch_pending_protection_algo_ids = lambda: []
    client.fetch_realized_close_for_position = lambda *args: {
        'gross_pnl': -44.6136, 'fee': 1.2273868, 'net_pnl': -45.96318704,
        'exit_price': 0.094 if exact else 0.09358, 'funding_fee': -0.12220024, 'source': 'okx_realized'}
    monkeypatch.setattr(cycle_recon.adapter, 'reconcile_pending_management', lambda *args: {'pending': False})
    monkeypatch.setattr(cycle_recon.notification_delivery, 'trade_event', lambda *args: None)
    cfg = SimpleNamespace(user_dir=str(tmp_path), CANDIDATE_C_LIVE_EXECUTE=True)
    outcome = cycle_recon.reconcile_managed_position(cfg, client, symbol, state, tick_ms=1791854400000)
    assert outcome['flat_confirmed'] is True
    row = json.loads((tmp_path / 'trades_log.jsonl').read_text())
    assert row['reason'] == ('EXTERNAL_SL_FILLED_EXACT' if exact else 'MANUAL_OR_EXTERNAL_CLOSE')
    if exact:
        assert row['close_reason_evidence']['order_id'] == '3969269497792811008'
        assert row['close_price'] == pytest.approx(float(case['fills'][0]['fillPx']))
        assert row['close_execution_evidence']['order_id'] == '3969269497792811008'
    else:
        assert 'close_reason_evidence' not in row
        assert row['close_price'] is None  # partial history is not final fill VWAP
        assert 'close_execution_evidence' not in row
    assert row['okx_net_pnl'] == pytest.approx(-26.60498704)
