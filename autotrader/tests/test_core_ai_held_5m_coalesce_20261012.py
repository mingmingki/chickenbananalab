"""Held positions (user decision 2026-10-12): a 5m EMA-state flip or a favorable move
below 3 ATR waits until 15 minutes after the last Gemini review.

Adverse moves of 1 ATR or more, 1h/4h trend, adverse 5m swing break, 1h structure,
correction, position and market-event changes stay immediate. Flat (entry) behavior
is unchanged. Production uses a 1800s routine interval.
"""
import datetime as dt

import pandas as pd
import pytest

import trader

SYMBOL = "BTC/USDT:USDT"
ROUTINE = 1800
T0 = dt.datetime(2026, 10, 12, 8, 0, tzinfo=dt.timezone.utc)


class FakeState:
    def __init__(self, memory=None, pending=None):
        self.memory, self.pending = memory, pending

    def snapshot(self):
        row = {}
        if self.memory:
            row["core_ai_budget"] = self.memory
        if self.pending:
            row["core_ai_pending"] = self.pending
        return {"symbols": {SYMBOL: row}}


def frame(close=100.0, *, ema20=99.0, ema50=98.0, macd=1.0, rsi=55.0, atr=2.0):
    idx = [T0 - dt.timedelta(minutes=5 * (6 - i)) for i in range(7)]
    return pd.DataFrame({"timestamp": idx, "close": [close] * 7, "ema_20": [ema20] * 7, "ema_50": [ema50] * 7,
                         "macd": [macd] * 7, "rsi_14": [rsi] * 7, "atr_14": [atr] * 7})


def frames(five=None, one_h=None, four_h=None):
    return {"5m": frame(**(five or {})), "1h": frame(**(one_h or {})), "4h": frame(**(four_h or {}))}


FLIP_5M = {"ema20": 100.5}  # close 100 < EMA20 100.5 > EMA50 98: 5m state bull -> mixed, price unchanged


def moved(close):
    """5m close moved from 100 (ATR 2) while the 5m EMA state stays 'bull'."""
    low = min(close, 100.0)
    return {"close": close, "ema20": low - 1.0, "ema50": low - 2.0}


def held(**change):
    return dict({"side": "long", "contracts": 1.0, "entry_price": 100.0, "position_id": "p", "entry_timestamp_ms": 1}, **change)


def gate(state, data, minutes, *, structures=None, correction=None, position="held", event="e1"):
    position = held() if position == "held" else position
    return trader._core_ai_call_gate(state, SYMBOL, data, structures or {}, correction or {"active": False}, position,
                                     now=T0 + dt.timedelta(minutes=minutes), market_event_key=event,
                                     routine_interval_seconds=ROUTINE)


def first_review(position="held"):
    result = gate(FakeState(), frames(), 0, position=position)
    assert result["call_ai"] is True and result["reason"] == "initial"
    return result["next_memory"]


def test_window_constants_match_the_decision():
    assert trader.CORE_AI_HELD_COALESCE_SECONDS == 900
    assert trader.CORE_AI_HELD_FAVORABLE_MOVE_ATR == 3.0


def test_held_5m_ema_flip_waits_until_fifteen_minutes_after_last_review():
    memory = first_review()
    early = gate(FakeState(memory), frames(FLIP_5M), 5)
    assert early["call_ai"] is False and early["reason"] == "held_coalesced"
    assert early["pending_memory"] is not None
    still = gate(FakeState(memory, early["pending_memory"]), frames(FLIP_5M), 10)
    assert still["call_ai"] is False and still["reason"] == "held_coalesced"
    due = gate(FakeState(memory, still["pending_memory"]), frames(FLIP_5M), 15)
    assert due["call_ai"] is True


def test_held_5m_flip_that_reverts_is_still_reviewed_at_fifteen_minutes():
    memory = first_review()
    early = gate(FakeState(memory), frames(FLIP_5M), 5)
    assert early["call_ai"] is False
    assert gate(FakeState(memory, early["pending_memory"]), frames(), 10)["call_ai"] is False
    reverted = gate(FakeState(memory, early["pending_memory"]), frames(), 15)
    assert reverted["call_ai"] is True and reverted["reason"] == "coalesced_change_due"


def test_held_change_long_after_last_review_is_not_delayed():
    memory = first_review()
    assert gate(FakeState(memory), frames(FLIP_5M), 16)["call_ai"] is True
    assert gate(FakeState(memory), frames(moved(102.5)), 16)["call_ai"] is True


@pytest.mark.parametrize("side,close", [("long", 102.5), ("long", 105.8), ("short", 97.5), ("short", 94.2)])
def test_held_favorable_move_below_three_atr_waits(side, close):
    memory = first_review(position=held(side=side))
    early = gate(FakeState(memory), frames(moved(close)), 5, position=held(side=side))
    assert early["call_ai"] is False and early["reason"] == "held_coalesced", early["reason"]
    due = gate(FakeState(memory, early["pending_memory"]), frames(moved(close)), 15, position=held(side=side))
    assert due["call_ai"] is True


@pytest.mark.parametrize("side,close", [("long", 97.9), ("long", 106.1), ("short", 102.1), ("short", 93.9)])
def test_held_adverse_one_atr_or_favorable_three_atr_stays_immediate(side, close):
    memory = first_review(position=held(side=side))
    result = gate(FakeState(memory), frames(moved(close)), 5, position=held(side=side))
    assert result["call_ai"] is True and result["reason"] == "price_move_atr"


def test_held_adverse_one_atr_with_5m_flip_stays_immediate():
    memory = first_review()
    result = gate(FakeState(memory), frames({"close": 97.9, "ema20": 99.0}), 5)  # flip to mixed + 1.05 ATR down
    assert result["call_ai"] is True


@pytest.mark.parametrize("trigger", ["1h_trend", "4h_trend", "adverse_5m_swing", "1h_structure", "correction",
                                     "flip_plus_1h_trend", "position", "market_event"])
def test_held_material_triggers_stay_immediate(trigger):
    memory = first_review()
    data, structures, correction, position, event = frames(), {}, None, "held", "e1"
    if trigger == "1h_trend":
        data = frames(one_h={"ema20": 100.5})
    if trigger == "4h_trend":
        data = frames(four_h={"close": 96.0, "ema20": 97.0, "ema50": 98.0})
    if trigger == "adverse_5m_swing":
        structures = {"5m": {"swing_low_broken": True}}
    if trigger == "1h_structure":
        structures = {"1h": {"high_structure": "LH", "low_structure": "LL", "swing_low_broken": True}}
    if trigger == "correction":
        correction = {"active": True}
    if trigger == "flip_plus_1h_trend":
        data = frames(FLIP_5M, one_h={"ema20": 100.5})
    if trigger == "position":
        position = held(contracts=0.5)
    if trigger == "market_event":
        event = "e2"
    result = gate(FakeState(memory), data, 5, structures=structures, correction=correction, position=position, event=event)
    assert result["call_ai"] is True, result["reason"]


@pytest.mark.parametrize("data", [frames(FLIP_5M), frames(moved(102.5))])
def test_flat_changes_still_call_immediately_for_entries(data):
    memory = first_review(position=None)
    result = gate(FakeState(memory), data, 5, position=None)
    assert result["call_ai"] is True


def test_held_sub_one_atr_moves_keep_routine_interval():
    memory = first_review()
    small = gate(FakeState(memory), frames(moved(101.5)), 5)  # 0.75 ATR, no state change
    assert small["call_ai"] is False and small["reason"] == "low_importance_coalesced"
    still = gate(FakeState(memory, small["pending_memory"]), frames(moved(101.5)), 20)
    assert still["call_ai"] is False
    due = gate(FakeState(memory, small["pending_memory"]), frames(moved(101.5)), 30)
    assert due["call_ai"] is True


def test_held_sub_one_atr_moves_unchanged_when_routine_is_shorter_than_window():
    memory = trader._core_ai_call_gate(FakeState(), SYMBOL, frames(), {}, {"active": False}, held(), now=T0,
                                       market_event_key="e1", routine_interval_seconds=600)["next_memory"]
    due = trader._core_ai_call_gate(FakeState(memory), SYMBOL, frames(moved(101.5)), {}, {"active": False}, held(),
                                    now=T0 + dt.timedelta(minutes=10), market_event_key="e1", routine_interval_seconds=600)
    assert due["call_ai"] is True and due["reason"] == "price_move_atr"


def test_held_change_shortens_an_existing_routine_pending_to_fifteen_minutes():
    memory = first_review()
    small = gate(FakeState(memory), frames(moved(101.5)), 5)
    flipped = gate(FakeState(memory, small["pending_memory"]), frames({"close": 101.5, "ema20": 102.0}), 8)
    assert flipped["call_ai"] is False and flipped["reason"] == "held_coalesced"
    reverted = gate(FakeState(memory, flipped["pending_memory"]), frames(moved(101.5)), 15)
    assert reverted["call_ai"] is True
