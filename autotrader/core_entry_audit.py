"""Read-only execution projection; original AI opinions and journal rows stay intact."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3


_OUTCOME = ('status', 'reason', 'order_success', 'order_executed',
            'exchange_verified', 'exchange_verification_reason')
_EXECUTION = ('order_id', 'client_order_id', 'lifecycle_id', 'protection_ids')


def _object(payload):
    try:
        value = json.loads(payload)
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError):
        return {}


def _evidence(user_dir, decision_ids):
    """Never use the writer's DB helper: it creates tables and changes pragmas."""
    path = Path(user_dir) / 'core_entry_events.sqlite3'
    if not decision_ids or not path.is_file():
        return {}, {}
    receipts, events = {}, {}
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=.2)) as db:
            # Bound each query below SQLite's variable ceiling, including large summaries.
            ids = list(decision_ids)
            for offset in range(0, len(ids), 400):
                batch = ids[offset:offset + 400]
                marks = ','.join('?' for _ in batch)
                for did, symbol, status, payload in db.execute(
                        f'SELECT decision_id,symbol,status,payload FROM entry_orders WHERE decision_id IN ({marks})', batch):
                    receipts[did] = dict(_object(payload), decision_id=did, symbol=symbol, status=status)
                for (payload,) in db.execute(
                        f"SELECT payload FROM events WHERE json_valid(payload) AND json_extract(payload,'$.decision_id') IN ({marks}) ORDER BY seq DESC", batch):
                    event = _object(payload)
                    did = event.get('decision_id')
                    if did not in events and (event.get('engine') or 'CORE') == 'CORE':
                        events[did] = event
    except (OSError, sqlite3.Error):
        # Missing, malformed or locked evidence cannot upgrade an unconfirmed outcome.
        return {}, {}
    return receipts, events


def _protected_fill(row, proof):
    return (proof.get('decision_id') == row.get('decision_id')
            and proof.get('symbol') == row.get('symbol')
            and row.get('side') in ('long', 'short') and proof.get('side') == row.get('side')
            and proof.get('status') == 'FILLED' and proof.get('reason') == 'protected_fill_confirmed'
            and bool(proof.get('lifecycle_id')) and bool(proof.get('order_id'))
            and all(not row.get(key) or row[key] == proof.get(key)
                    for key in ('order_id', 'lifecycle_id')))


def project_rows(user_dir, records):
    rows = [dict(row) for row in records]
    ids = {row['decision_id'] for row in rows if row.get('decision_id')
           and (row.get('engine') or 'CORE') == 'CORE'}
    receipts, events = _evidence(user_dir, ids)
    for row in rows:
        row['original_outcome'] = {key: row[key] for key in _OUTCOME + _EXECUTION if key in row}
        row['execution_evidence_source'] = 'initial_audit'
        if (row.get('engine') or 'CORE') != 'CORE':
            continue
        did = row.get('decision_id')
        for source, proof in (('durable_event', events.get(did, {})),
                              ('durable_receipt', receipts.get(did, {}))):
            if not _protected_fill(row, proof):
                continue
            row.update(status='FILLED', reason='protected_fill_confirmed', order_executed=True,
                       protected_fill_confirmed=True, execution_evidence_source=source,
                       execution_evidence_time=proof.get('time'))
            if row.get('mode') == 'entry_gate' or 'order_success' in row:
                row['order_success'] = True
            row.update({key: proof[key] for key in _EXECUTION if proof.get(key) is not None})
            # This is historical durable protection proof, not a fresh exchange price read.
            # Preserve exchange_verified and actual_* fields from the original observation.
            break
    return rows
