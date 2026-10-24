import json
import sqlite3

import pytest

import ai_exit_plan_audit
import gpt_shadow_log

REAL_CASES = [
    ('ADA/USDT:USDT-20261013063522517529', 'ADA/USDT:USDT',
     '3996649734486609921:1791840938288:short', '4004336867993178112'),
    ('ETH/USDT:USDT-20261013083009791467', 'ETH/USDT:USDT',
     '3853548374883815424:1791847828115:short', '4004568052224741376'),
]


def receipt_db(path, decision_id, symbol, *, status='FILLED', reason='protected_fill_confirmed',
               side='short', lifecycle_id='life', order_id='order', event=None):
    with sqlite3.connect(path / 'core_entry_events.sqlite3') as db:
        db.execute('CREATE TABLE IF NOT EXISTS entry_orders (decision_id TEXT PRIMARY KEY,symbol TEXT,status TEXT,payload TEXT,updated REAL)')
        db.execute('CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY,payload TEXT)')
        payload = dict(side=side, reason=reason, lifecycle_id=lifecycle_id, order_id=order_id)
        db.execute('INSERT OR REPLACE INTO entry_orders VALUES (?,?,?,?,?)',
                   (decision_id, symbol, status, json.dumps(payload), 100.))
        if event:
            db.execute('INSERT INTO events(payload) VALUES (?)', (json.dumps(event),))


def pending_rows(path, decision_id, symbol):
    gate = dict(decision_id=decision_id, symbol=symbol, side='short',
                mode='entry_gate', status='ORDER_PENDING', reason='original_entry_lifecycle_unconfirmed',
                order_success=False, order_executed=False, gpt_decision='approve_now',
                gate_result='approved', time='2026-10-13T08:30:29')
    (path / 'gpt_shadow_log.jsonl').write_text(json.dumps(gate) + '\n')
    price = dict(engine='CORE', decision_id=decision_id, symbol=symbol, side='short',
                 result='ai_exit_plan_applied', gpt_exit_plan_decision='approve',
                 order_executed=False, exchange_verified=False,
                 exchange_verification_reason='order_not_executed', actual_sl=None, actual_tp=None)
    ai_exit_plan_audit.append_record(str(path), price)
    return gate, price


@pytest.mark.parametrize('decision_id,symbol,lifecycle_id,order_id', REAL_CASES)
def test_real_pending_entries_project_protected_fill_without_rewriting_history(
        tmp_path, decision_id, symbol, lifecycle_id, order_id):
    gate, price = pending_rows(tmp_path, decision_id, symbol)
    receipt_db(tmp_path, decision_id, symbol, lifecycle_id=lifecycle_id, order_id=order_id)
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    row = gpt_shadow_log.recent_by_mode(str(tmp_path), 'entry_gate')[0]
    audit = ai_exit_plan_audit.summarize(str(tmp_path))['recent'][0]
    assert row['status'] == 'FILLED'
    assert row['order_success'] is True
    assert row['gpt_decision'] == 'approve_now' and row['gate_result'] == 'approved'
    assert row['time'] == gate['time'] and row['lifecycle_id'] == lifecycle_id
    assert row['execution_evidence_source'] == 'durable_receipt'
    assert row['original_outcome']['status'] == 'ORDER_PENDING'
    assert audit['order_executed'] is True and audit['protected_fill_confirmed'] is True
    assert audit['actual_sl'] is None and audit['actual_tp'] is None
    assert audit['exchange_verified'] is False
    assert audit['original_outcome']['order_executed'] is False
    assert before == {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    assert gpt_shadow_log.summary(str(tmp_path))['gate_count'] == 1


@pytest.mark.parametrize('changes', [
    dict(status='FILLED_UNJOURNALED'), dict(status='ORDER_PENDING'),
    dict(reason='exchange_fill_confirmed'), dict(side='long'),
    dict(lifecycle_id=None), dict(order_id=None),
])
def test_incomplete_or_different_lifecycle_receipts_do_not_claim_protected_fill(tmp_path, changes):
    did, symbol, _, _ = REAL_CASES[0]
    pending_rows(tmp_path, did, symbol)
    receipt_db(tmp_path, did, symbol, **changes)
    row = gpt_shadow_log.recent_by_mode(str(tmp_path), 'entry_gate')[0]
    assert row['status'] == 'ORDER_PENDING'
    assert row['order_success'] is False
    assert row['execution_evidence_source'] == 'initial_audit'
    assert ai_exit_plan_audit.summarize(str(tmp_path))['recent'][0]['order_executed'] is False


def test_unmatched_same_symbol_receipt_cannot_confirm_another_decision(tmp_path):
    did, symbol, _, _ = REAL_CASES[0]
    pending_rows(tmp_path, did, symbol)
    receipt_db(tmp_path, did + '-new', symbol)
    row = gpt_shadow_log.recent_by_mode(str(tmp_path), 'entry_gate')[0]
    assert row['status'] == 'ORDER_PENDING' and row['order_success'] is False
    assert row['execution_evidence_source'] == 'initial_audit'


def test_terminal_event_can_supply_protection_and_cannot_overwrite_direction_verdict(tmp_path):
    did, symbol, life, order = REAL_CASES[0]
    pending_rows(tmp_path, did, symbol)
    event = dict(decision_id=did, symbol=symbol, side='short', engine='CORE',
                 status='FILLED', reason='protected_fill_confirmed', lifecycle_id=life,
                 order_id=order, protection_ids=['algo'], gpt_decision=None, time='later')
    receipt_db(tmp_path, did, symbol, status='ORDER_PENDING', event=event)
    row = gpt_shadow_log.recent_by_mode(str(tmp_path), 'entry_gate')[0]
    assert row['status'] == 'FILLED' and row['execution_evidence_source'] == 'durable_event'
    assert row['gpt_decision'] == 'approve_now' and row['time'] == '2026-10-13T08:30:29'


def test_missing_or_corrupt_database_is_unknown_and_read_does_not_create_it(tmp_path):
    did, symbol, _, _ = REAL_CASES[0]
    pending_rows(tmp_path, did, symbol)
    db = tmp_path / 'core_entry_events.sqlite3'
    row = gpt_shadow_log.recent_by_mode(str(tmp_path), 'entry_gate')[0]
    assert not db.exists()
    assert row['execution_evidence_source'] == 'initial_audit'
    db.write_bytes(b'not a sqlite database')
    row = gpt_shadow_log.recent_by_mode(str(tmp_path), 'entry_gate')[0]
    assert row['status'] == 'ORDER_PENDING' and row['order_success'] is False


def test_projected_fill_performance_links_exact_entry_not_nearby_same_symbol(tmp_path):
    did, symbol, life, order = REAL_CASES[0]
    pending_rows(tmp_path, did, symbol)
    receipt_db(tmp_path, did, symbol, lifecycle_id=life, order_id=order)
    wrong = dict(trade_id='wrong', symbol=symbol, side='short', entry_time='2026-10-13T08:30:29', net_pnl=99.)
    right = dict(trade_id='right', symbol=symbol, side='short', net_pnl=3.,
                 events=[dict(type='open', execution_id='core-entry:' + did)])
    out = ai_exit_plan_audit.summarize(str(tmp_path), lifecycles=[wrong, right])
    assert out['performance']['ai_applied'] == dict(count=1, net_pnl=3.)
    out = ai_exit_plan_audit.summarize(str(tmp_path), lifecycles=[wrong])
    assert out['performance']['ai_applied'] == dict(count=0, net_pnl=0.)


@pytest.mark.parametrize('conflict', [dict(order_id='different-order'), dict(lifecycle_id='different-life')])
def test_conflicting_original_order_identity_never_uses_another_fill(tmp_path, conflict):
    did, symbol, _, _ = REAL_CASES[0]
    gate, _ = pending_rows(tmp_path, did, symbol)
    gate.update(conflict)
    (tmp_path / 'gpt_shadow_log.jsonl').write_text(json.dumps(gate) + '\n')
    receipt_db(tmp_path, did, symbol)
    row = gpt_shadow_log.recent_by_mode(str(tmp_path), 'entry_gate')[0]
    assert row['status'] == 'ORDER_PENDING' and row['order_success'] is False


def test_non_core_terminal_event_does_not_promote_core_gate(tmp_path):
    did, symbol, life, order = REAL_CASES[0]
    pending_rows(tmp_path, did, symbol)
    event = dict(decision_id=did, symbol=symbol, side='short', engine='CANDIDATE_C',
                 status='FILLED', reason='protected_fill_confirmed', lifecycle_id=life, order_id=order)
    receipt_db(tmp_path, did, symbol, status='ORDER_PENDING', event=event)
    row = gpt_shadow_log.recent_by_mode(str(tmp_path), 'entry_gate')[0]
    assert row['status'] == 'ORDER_PENDING' and row['order_success'] is False
