"""Captured fills must all contribute once to completed lifecycle statistics."""
import json
from pathlib import Path
import sqlite3

import pytest

import core_unified_history
from trade_learning_lifecycle import build_completed_lifecycles


def captured_journal(tmp_path):
    capture = json.loads((Path(__file__).parent / 'fixtures' /
                          'pnl_projected_reductions_capture.json').read_text())
    with sqlite3.connect(tmp_path / 'core_unified.sqlite3') as db:
        db.execute('CREATE TABLE accounting(payload TEXT)')
        db.executemany('INSERT INTO accounting VALUES (?)',
                       [(json.dumps(r),) for r in capture['accounting']])
    (tmp_path / 'okx_margin_return_state.json').write_text(json.dumps({'orders': capture['orders']}))
    return core_unified_history.load_records(str(tmp_path))


def test_captured_projected_reductions_belong_to_completed_position(tmp_path):
    captured_journal(tmp_path)
    lives = build_completed_lifecycles(str(tmp_path))
    assert len(lives) == 2
    assert lives[0]['lifecycle_net'] == pytest.approx(-1.73477065)
    assert lives[0]['reduce_net'] == pytest.approx(-0.4232172194222337)
    assert lives[0]['partial_reduction_count'] == 1
    assert lives[1]['lifecycle_net'] == pytest.approx(-0.83981190)
    assert lives[1]['reduce_net'] == pytest.approx(-0.33145020)
    assert lives[1]['partial_reduction_count'] == 2


def test_raw_copy_of_projected_execution_is_counted_once(tmp_path):
    rows = captured_journal(tmp_path)
    (tmp_path / 'trades_log.jsonl').write_text('\n'.join(json.dumps(r) for r in rows) + '\n')
    lives = build_completed_lifecycles(str(tmp_path))
    assert len(lives) == 2
    assert sum(r['lifecycle_net'] for r in lives) == pytest.approx(-2.57458255)
    assert sum(r['partial_reduction_count'] for r in lives) == 3
