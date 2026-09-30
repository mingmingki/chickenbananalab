import time
from types import SimpleNamespace

import pytest

import candidate_c_manual_close as cmc


def test_reserve_confirm_cooldown_and_new_request_after_release(tmp_path):
    u = str(tmp_path)
    first = cmc.reserve(u, "SOL/USDT:USDT", position_epoch="entry-1", now=100.0)
    assert first["status"] == "reserved"
    assert first["position_epoch"] == "entry-1"
    assert cmc.block_reason(first, now=100.1) == "candidate_manual_close_unresolved"

    confirmed = cmc.confirm(u, "SOL/USDT:USDT", first["close_id"], cooldown_seconds=900, now=110.0)
    assert confirmed["status"] == "confirmed"
    assert confirmed["release_at"] == 1010.0
    assert cmc.block_reason(confirmed, now=1009.9) == "candidate_manual_close_cooldown"
    assert cmc.block_reason(confirmed, now=1010.0) is None

    second = cmc.reserve(u, "SOL/USDT:USDT", position_epoch="entry-2", now=1011.0)
    assert second["close_id"] != first["close_id"]
    assert second["position_epoch"] == "entry-2"


def test_reserve_is_idempotent_for_same_active_position_and_rejects_other_position(tmp_path):
    u = str(tmp_path)
    first = cmc.reserve(u, "DOGE/USDT:USDT", position_epoch="entry-a", now=200.0)
    same = cmc.reserve(u, "DOGE/USDT:USDT", position_epoch="entry-a", now=201.0)
    assert same["close_id"] == first["close_id"]
    with pytest.raises(ValueError, match="different position"):
        cmc.reserve(u, "DOGE/USDT:USDT", position_epoch="entry-b", now=202.0)


def test_failed_request_does_not_create_cooldown_but_is_auditable(tmp_path):
    u = str(tmp_path)
    rec = cmc.reserve(u, "SOL/USDT:USDT", position_epoch="entry-x", now=1.0)
    failed = cmc.fail(u, "SOL/USDT:USDT", rec["close_id"], "definite_rejection", now=2.0)
    assert failed["status"] == "failed"
    assert failed["error"] == "definite_rejection"
    assert cmc.block_reason(failed, now=3.0) is None
