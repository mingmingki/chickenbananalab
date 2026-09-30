import datetime
import json

import pnl_store


def test_save_baseline_records_exact_reset_timestamp_and_preserves_unknown_fields(tmp_path):
    path = tmp_path / "pnl_state.json"
    path.write_text(json.dumps({
        "schema_version": 2,
        "baseline_equity": 100.0,
        "baseline_set_at": "2026-08-30",
        "account_binding_sha256": "keep-me",
    }), encoding="utf-8")
    now = datetime.datetime(
        2026, 9, 19, 7, 30, 15, 123000,
        tzinfo=datetime.timezone(datetime.timedelta(hours=9)),
    )
    pnl_store.save_baseline(str(tmp_path), 200.0, now=now)
    rec = pnl_store.load_baseline_metadata(str(tmp_path))
    assert rec["baseline_equity"] == 200.0
    assert rec["baseline_set_at"] == "2026-09-19"
    assert rec["baseline_set_at_ms"] == int(now.timestamp() * 1000)
    assert rec["baseline_time_source"] == "reset_event"
    assert rec["account_binding_sha256"] == "keep-me"
    assert rec["schema_version"] >= 3


def test_legacy_metadata_is_not_promoted_to_exact(tmp_path):
    (tmp_path / "pnl_state.json").write_text(json.dumps({
        "schema_version": 2,
        "baseline_equity": 1293.65,
        "baseline_set_at": "2026-08-30",
        "baseline_set_at_utc": "2026-09-10T21:46:39Z",
    }), encoding="utf-8")
    rec = pnl_store.load_baseline_metadata(str(tmp_path))
    assert rec.get("baseline_time_source") is None
    assert rec.get("baseline_set_at_ms") is None
