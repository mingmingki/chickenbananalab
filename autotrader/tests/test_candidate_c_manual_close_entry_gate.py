import time
from types import SimpleNamespace

import candidate_c_hybrid_live_adapter as live
import candidate_c_manual_close as cmc


def _cfg(tmp_path):
    return SimpleNamespace(
        user_dir=str(tmp_path),
        CANDIDATE_C_GPT_ENTRY_GATE_ENABLED=True,
        CANDIDATE_C_LIVE_EXECUTE=True,
    )


def _intent():
    return SimpleNamespace(kind="EntryIntent", symbol="SOL/USDT:USDT", side="long")


def test_unresolved_manual_close_blocks_before_gpt(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    cmc.reserve(cfg.user_dir, "SOL/USDT:USDT", position_epoch="entry-1")
    monkeypatch.setattr(live, "_candidate_c_new_entry_allowed", lambda *a, **k: True)
    monkeypatch.setattr(
        live.gga, "verify_candidate_signal",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("GPT must not run")),
    )
    result = live._execute_entry(
        cfg, object(), _intent(), {}, 1000.0, lambda: True,
        None, ledger=None, epoch_store=None,
    )
    assert result["gate_result"] == "candidate_manual_close_unresolved"


def test_manual_close_cooldown_blocks_before_gpt(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    rec = cmc.reserve(cfg.user_dir, "SOL/USDT:USDT", position_epoch="entry-1")
    cmc.confirm(cfg.user_dir, "SOL/USDT:USDT", rec["close_id"], cooldown_seconds=900)
    monkeypatch.setattr(live, "_candidate_c_new_entry_allowed", lambda *a, **k: True)
    monkeypatch.setattr(
        live.gga, "verify_candidate_signal",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("GPT must not run")),
    )
    result = live._execute_entry(
        cfg, object(), _intent(), {}, 1000.0, lambda: True,
        None, ledger=None, epoch_store=None,
    )
    assert result["gate_result"] == "candidate_manual_close_cooldown"
