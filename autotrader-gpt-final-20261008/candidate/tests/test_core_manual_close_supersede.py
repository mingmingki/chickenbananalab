import json

import core_manual_close


def _write_record(tmp_path, status="confirmed", **fields):
    record = {"close_id": "cm-old", "status": status, "confirmed_at": 100.0,
              "release_at": 200.0, "position": None, "journaled": True,
              "cleanup_pending": False, **fields}
    (tmp_path / "core_manual_close.json").write_text(
        json.dumps({"BTC/USDT:USDT": record}), encoding="utf-8")
    return record


def test_completed_confirmed_close_is_superseded_by_newer_live_position(tmp_path):
    _write_record(tmp_path)
    position = {"position_id": "new", "entry_timestamp_ms": 300_000}
    assert core_manual_close.complete_if_superseded_by_position(
        str(tmp_path), "BTC/USDT:USDT", position) is True
    assert core_manual_close.get(str(tmp_path), "BTC/USDT:USDT")["status"] == "completed"


def test_unresolved_close_is_not_superseded(tmp_path):
    _write_record(tmp_path, status="submitted", position={"position_id": "old"})
    position = {"position_id": "new", "entry_timestamp_ms": 300_000}
    assert core_manual_close.complete_if_superseded_by_position(
        str(tmp_path), "BTC/USDT:USDT", position) is False
