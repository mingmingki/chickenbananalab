"""Mock-only reproduction, pending retrieval of the exact production source.

The real AI review handler and durable REDUCE state run. AI providers and
execution boundaries are replaced so no exchange or test order is possible.
"""
import datetime
import json
import logging
from types import SimpleNamespace

import pytest

import reduce_v2_state
import trader
from state import TraderState


SYMBOL = "ETH/USDT:USDT"
NOW = datetime.datetime(2026, 10, 4, 13, 0, 0)


class FixedDateTime(datetime.datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 10, 4, 13, 0, 0, tzinfo=tz)


def review(monkeypatch, tmp_path, *, side="long", elapsed=300,
           path="general", breakdown=False, assessment="weakening",
           position_updates=None, saved_fields=None):
    cfg = SimpleNamespace(
        user_dir=str(tmp_path), OPENAI_API_KEY="test",
        POSITION_AI_REVIEW_ENABLED=True, POSITION_AI_LIVE_EXECUTE=True,
        POSITION_AI_REVIEW_COOLDOWN_MINUTES=15, MIN_CONFIDENCE=0.6,
        EXECUTION_MODE="LIVE", logger=logging.getLogger("cooldown_reproduction"),
    )
    position = {"side": side, "contracts": 3.0, "entry_price": 100.0,
                "mark_price": 98.0 if side == "long" else 102.0}
    protection = {"sl_price": 90.0 if side == "long" else 110.0}
    # Persist a successful reduction, then use a fresh in-memory TraderState:
    # a handler that relies only on memory will fail the restart case.
    reduce_v2_state.load_or_init(str(tmp_path), SYMBOL, side, 100.0, None, 4.0)
    reduce_v2_state.record_stage_executed(str(tmp_path), SYMBOL, 1, 1.0)
    reduce_v2_state.update_fields(
        str(tmp_path), SYMBOL,
        last_reduction_order_time=(NOW - datetime.timedelta(seconds=elapsed)).isoformat(),
    )
    if saved_fields:
        reduce_v2_state.update_fields(str(tmp_path), SYMBOL, **saved_fields)
    if position_updates:
        position.update(position_updates)
    monkeypatch.setattr(trader.datetime, "datetime", FixedDateTime)
    monkeypatch.setattr(trader.gemini_analyzer, "analyze_held_position",
                        lambda *a, **k: {"assessment": assessment, "confidence": .9})
    monkeypatch.setattr(trader.openai_analyzer, "verify_position_management",
                        lambda *a, **k: {"action": "CLOSE_ALL", "confidence": .9})
    monkeypatch.setattr(trader, "_exit_escalation_multitf_invalidated",
                        lambda *a, **k: breakdown)
    monkeypatch.setattr(trader.trade_log, "last_unclosed_open",
                        lambda *a, **k: protection)
    actions = []
    monkeypatch.setattr(trader, "_execute_close",
                        lambda *a, **k: actions.append(("close", k.get("reason"))) or True)
    monkeypatch.setattr(trader, "_execute_position_ai_reduce_50",
                        lambda *a, **k: actions.append(("reduce", None)) or True)
    client = SimpleNamespace(fetch_current_protection=lambda *a, **k: protection)
    trader._handle_position_ai_review(
        cfg, TraderState(), client, SYMBOL, ["5m", "1h"], "mock summary",
        position, {"action": "close" if path == "exit_escalation" else "hold"},
        {}, review_path=path,
    )
    events = [json.loads(line) for line in
              (tmp_path / "position_ai_log.jsonl").read_text().splitlines()]
    return actions, events


@pytest.mark.parametrize("side", ["long", "short"])
@pytest.mark.parametrize("elapsed", [300, 899])
def test_general_close_blocked_until_fifteen_minutes_after_persisted_reduce(
        monkeypatch, tmp_path, caplog, side, elapsed):
    with caplog.at_level(logging.INFO):
        actions, events = review(monkeypatch, tmp_path, side=side, elapsed=elapsed)
    assert actions == [], "general CLOSE_ALL must preserve quantity during post-REDUCE cooldown"
    assert any(event.get("reason") == "post_reduce_close_cooldown" for event in events)
    assert "post_reduce_close_cooldown" in caplog.text


@pytest.mark.parametrize("side", ["long", "short"])
def test_general_close_allowed_at_fifteen_minute_boundary(monkeypatch, tmp_path, side):
    actions, _ = review(monkeypatch, tmp_path, side=side, elapsed=900)
    assert actions == [("close", "position_ai_close_all")]


def test_deterministic_exit_escalation_bypasses_post_reduce_cooldown(monkeypatch, tmp_path):
    actions, _ = review(monkeypatch, tmp_path, path="exit_escalation",
                        breakdown=True, assessment="invalidated")
    assert actions == [("close", "deterministic_exit_escalation")]


def test_high_confidence_exit_escalation_bypasses_post_reduce_cooldown(monkeypatch, tmp_path):
    actions, _ = review(monkeypatch, tmp_path, path="exit_escalation", assessment="invalidated")
    assert actions == [("close", "position_ai_close_all")]


def test_same_exchange_lifecycle_preserves_cooldown_when_add_changes_average_entry(monkeypatch, tmp_path):
    actions, _ = review(
        monkeypatch, tmp_path,
        saved_fields={"lifecycle_id": "pos1:123:long"},
        position_updates={"position_id": "pos1", "entry_timestamp_ms": 123, "entry_price": 100.5},
    )
    assert actions == []


def test_new_exchange_lifecycle_does_not_inherit_old_reduce_cooldown(monkeypatch, tmp_path):
    actions, _ = review(
        monkeypatch, tmp_path,
        saved_fields={"lifecycle_id": "pos1:123:long"},
        position_updates={"position_id": "pos1", "entry_timestamp_ms": 456},
    )
    assert actions == [("close", "position_ai_close_all")]


def test_general_high_confidence_invalidated_still_obeys_cooldown(monkeypatch, tmp_path):
    actions, events = review(monkeypatch, tmp_path, assessment="invalidated")
    assert actions == []
    assert any(e.get("reason") == "post_reduce_close_cooldown" for e in events)


def test_unparseable_durable_reduce_time_cannot_bypass_cooldown(monkeypatch, tmp_path):
    actions, events = review(monkeypatch, tmp_path, saved_fields={"last_reduction_order_time": "invalid"})
    assert actions == []
    assert any(e.get("state_error") == "reduction_time_unavailable" for e in events)
