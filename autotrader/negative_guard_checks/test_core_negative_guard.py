import datetime
import contextlib
import json
import logging
import tempfile
import time
import threading
from types import SimpleNamespace

import pytest

import openai_analyzer
import trader


@pytest.fixture(autouse=True)
def _join_negative_guard_test_workers():
    yield
    for worker in threading.enumerate():
        if worker.name.startswith('core-negative-guard-'):
            worker.join(2)
            assert not worker.is_alive()


def _closed_ts(minutes_ago):
    return (
        datetime.datetime.now(datetime.timezone.utc)
        - datetime.timedelta(minutes=minutes_ago)
    ).isoformat()


def _weakening_long_rows(*, final_minutes_ago=5):
    return [
        {
            "ts": _closed_ts(final_minutes_ago + 10),
            "close": 100.20,
            "rsi_14": 52.0,
            "macd": 3.0,
            "ema_20": 100.00,
            "ema_50": 99.00,
            "atr_14": 1.0,
        },
        {
            "ts": _closed_ts(final_minutes_ago + 5),
            "close": 99.70,
            "rsi_14": 48.0,
            "macd": 2.0,
            "ema_20": 100.00,
            "ema_50": 99.00,
            "atr_14": 1.0,
        },
        {
            "ts": _closed_ts(final_minutes_ago),
            "close": 99.20,
            "rsi_14": 44.0,
            "macd": 1.0,
            "ema_20": 100.00,
            "ema_50": 99.00,
            "atr_14": 1.0,
        },
    ]


def _install_closed_rows(monkeypatch, rows_5m):
    # The negative guard deliberately does not require 1h weakness.  Supplying
    # an explicitly bullish 1h row makes that contract visible even if the
    # implementation includes 1h data in diagnostics.
    bullish_1h = [{
        "ts": _closed_ts(60),
        "close": 105.0,
        "rsi_14": 62.0,
        "macd": 1.5,
        "ema_20": 102.0,
        "ema_50": 100.0,
        "atr_14": 2.0,
    }]

    def fake_tail(_raw, tf, n=2):
        rows = {"5m": rows_5m, "1h": bullish_1h}.get(tf)
        return rows[-n:] if rows else None

    monkeypatch.setattr(trader, "_closed_indicator_tail", fake_tail)


def _diagnostics(*, last_price, stage=0, stage1_bar_ts=None):
    """Public test contract for the event-only negative guard helper.

    ``loss_r`` is the adverse move divided by the *actual* entry-to-SL
    distance. ``stage`` is the already-completed reduction stage: 0 evaluates
    the first 25% reduction and 1 evaluates the second 25% reduction.
    """
    return trader._negative_guard_numeric_diagnostics(
        side="long",
        raw_dfs={},
        last_price=last_price,
        entry_price=100.0,
        sl_price=98.0,
        stage=stage,
        stage1_bar_ts=stage1_bar_ts,
    )


def test_stage1_does_not_trigger_below_half_actual_stop_distance(monkeypatch):
    rows = _weakening_long_rows()
    _install_closed_rows(monkeypatch, rows)

    result = _diagnostics(last_price=99.02)  # 0.98 / actual 2.00 SL distance = 0.49R

    assert result["loss_r"] == pytest.approx(0.49)
    assert result["sl_proximity_pct"] == pytest.approx(49.0)
    assert result["two_macd_weakening"] is True
    assert result["numeric_conditions_met"] is False


def test_stage1_triggers_at_half_r_with_confirmed_5m_structure_break(monkeypatch):
    rows = _weakening_long_rows()
    _install_closed_rows(monkeypatch, rows)

    result = _diagnostics(last_price=99.0)  # 1.00 / actual 2.00 SL distance = 0.50R

    assert result["loss_r"] == pytest.approx(0.50)
    assert result["sl_proximity_pct"] == pytest.approx(50.0)
    assert result["two_macd_weakening"] is True
    assert result["adverse_rsi"] is True
    assert result["adverse_close"] is True
    assert result["closed_5m_fresh"] is True
    assert result["closed_5m_ts"] == rows[-1]["ts"]
    assert result["numeric_conditions_met"] is True


def test_stage1_rejects_stale_closed_5m_structure(monkeypatch):
    rows = _weakening_long_rows(final_minutes_ago=20)
    _install_closed_rows(monkeypatch, rows)

    result = _diagnostics(last_price=98.8)

    assert result["loss_r"] == pytest.approx(0.60)
    assert result["closed_5m_fresh"] is False
    assert result["numeric_conditions_met"] is False


def test_stage1_rejects_loss_without_confirmed_5m_structure_break(monkeypatch):
    rows = _weakening_long_rows()
    rows[-1].update(macd=2.5, rsi_14=55.0, close=100.5)
    _install_closed_rows(monkeypatch, rows)

    result = _diagnostics(last_price=98.8)

    assert result["loss_r"] == pytest.approx(0.60)
    assert result["two_macd_weakening"] is False
    assert result["adverse_rsi"] is False
    assert result["adverse_close"] is False
    assert result["numeric_conditions_met"] is False


def test_stage1_accepts_macd_plus_rsi_even_when_close_is_above_ema20(monkeypatch):
    rows = _weakening_long_rows()
    rows[-1].update(rsi_14=44.0, close=100.5, ema_20=100.0)
    _install_closed_rows(monkeypatch, rows)

    result = _diagnostics(last_price=98.8)

    assert result["two_macd_weakening"] is True
    assert result["adverse_rsi"] is True
    assert result["adverse_close"] is False
    assert result["numeric_conditions_met"] is True


def test_stage1_accepts_macd_plus_ema_break_even_when_rsi_is_above_50(monkeypatch):
    rows = _weakening_long_rows()
    rows[-1].update(rsi_14=55.0, close=99.2, ema_20=100.0)
    _install_closed_rows(monkeypatch, rows)

    result = _diagnostics(last_price=98.8)

    assert result["two_macd_weakening"] is True
    assert result["adverse_rsi"] is False
    assert result["adverse_close"] is True
    assert result["numeric_conditions_met"] is True


def test_stage1_rejects_macd_weakness_when_neither_rsi_nor_ema_is_adverse(monkeypatch):
    rows = _weakening_long_rows()
    rows[-1].update(rsi_14=55.0, close=100.5, ema_20=100.0)
    _install_closed_rows(monkeypatch, rows)

    result = _diagnostics(last_price=98.8)

    assert result["two_macd_weakening"] is True
    assert result["adverse_rsi"] is False
    assert result["adverse_close"] is False
    assert result["numeric_conditions_met"] is False


def test_negative_guard_allows_ai_handoff_just_below_emergency_zone(monkeypatch):
    rows = _weakening_long_rows()
    _install_closed_rows(monkeypatch, rows)

    result = trader._negative_guard_numeric_diagnostics(
        side="long", raw_dfs={}, last_price=82.02, entry_price=100.0,
        sl_price=80.0, stage=0, stage1_bar_ts=None,
    )

    assert result["loss_r"] == pytest.approx(0.899)
    assert result["numeric_conditions_met"] is True


@pytest.mark.parametrize("loss_r", [0.90, 1.05])
def test_negative_guard_hands_off_to_emergency_at_or_beyond_point_nine_r(
    monkeypatch, loss_r,
):
    rows = _weakening_long_rows()
    _install_closed_rows(monkeypatch, rows)

    result = trader._negative_guard_numeric_diagnostics(
        side="long", raw_dfs={}, last_price=100.0 - loss_r * 20.0,
        entry_price=100.0, sl_price=80.0, stage=0, stage1_bar_ts=None,
    )

    assert result["loss_r"] == pytest.approx(loss_r)
    assert result["numeric_conditions_met"] is False


def test_stage2_can_trigger_at_three_quarter_r_on_new_5m_bar_without_1h_weakness(monkeypatch):
    rows = _weakening_long_rows()
    _install_closed_rows(monkeypatch, rows)

    result = _diagnostics(
        last_price=98.5,  # 1.50 / actual 2.00 SL distance = 0.75R
        stage=1,
        stage1_bar_ts=rows[-2]["ts"],
    )

    assert result["loss_r"] == pytest.approx(0.75)
    assert result["new_closed_5m_bar"] is True
    assert result["numeric_conditions_met"] is True


def test_stage2_rejects_reusing_the_stage1_closed_5m_bar(monkeypatch):
    rows = _weakening_long_rows()
    _install_closed_rows(monkeypatch, rows)

    result = _diagnostics(
        last_price=98.4,
        stage=1,
        stage1_bar_ts=rows[-1]["ts"],
    )

    assert result["loss_r"] == pytest.approx(0.80)
    assert result["new_closed_5m_bar"] is False
    assert result["numeric_conditions_met"] is False


class _ReviewState:
    def __init__(self, symbol_state=None):
        self.updates = []
        self.symbol_state = dict(symbol_state or {})

    def update_symbol(self, symbol, **fields):
        self.updates.append((symbol, fields))
        self.symbol_state.update(fields)

    def snapshot(self):
        return {"symbols": {"ETH/USDT:USDT": dict(self.symbol_state)}}


class _ReviewClient:
    def __init__(self, last_price=99.0, sl_price=98.0):
        self.last_price = last_price
        self.sl_price = sl_price

    def fetch_last_price(self):
        return self.last_price

    def fetch_current_protection(self, close_side):
        assert close_side == "sell"
        return {"sl_price": self.sl_price, "tp_price": 104.0, "sz": 10.0}


def _review_cfg():
    return SimpleNamespace(
        CORE_NEGATIVE_GUARD_ENABLED=True,
        EXECUTION_MODE="LIVE",
        POSITION_AI_LIVE_EXECUTE=True,
        POSITION_AI_REVIEW_COOLDOWN_MINUTES=15,
        MIN_CONFIDENCE=0.60,
        STOP_LOSS_PCT=1.5,
        user_dir="/tmp/negative-guard-test",
        logger=logging.getLogger("test_core_negative_guard"),
    )


def _position():
    return {
        "side": "long",
        "contracts": 10.0,
        "entry_price": 100.0,
        "position_id": "pos-1",
        "entry_timestamp_ms": 123456789,
    }


def _install_review_control_fakes(monkeypatch, lifecycle_state, diagnostics):
    class Handled(list):
        ready = None
        def __init__(self):
            super().__init__()
            self.ready = threading.Event()
        def append(self, value):
            super().append(value)
            self.ready.set()
    handled = Handled()
    persisted = []

    monkeypatch.setattr(trader, "_position_ai_review_candidate", lambda *_args: True)
    monkeypatch.setattr(
        trader.cc_ownership,
        "account_order_lock",
        lambda _user_dir: contextlib.nullcontext(),
    )
    monkeypatch.setattr(
        trader.reduce_v2_state,
        "ensure_position",
        lambda *_args, **_kwargs: dict(lifecycle_state),
    )

    def update_fields(_user_dir, _symbol, **fields):
        lifecycle_state.update(fields)
        persisted.append(fields)
        return dict(lifecycle_state)

    monkeypatch.setattr(trader.reduce_v2_state, "update_fields", update_fields)
    monkeypatch.setattr(trader.core_add_position_state, "get", lambda *_args: None)
    monkeypatch.setattr(
        trader,
        "_negative_guard_numeric_diagnostics",
        lambda *_args, **_kwargs: dict(diagnostics),
        raising=False,
    )

    def handle(*args, **kwargs):
        handled.append((args, kwargs))

    monkeypatch.setattr(trader, "_handle_position_ai_review", handle)
    return handled, persisted


def test_negative_guard_stage0_calls_ai_once_and_persists_bar_dedupe(monkeypatch):
    bar = _closed_ts(5)
    lifecycle = {
        "reduce_stage": 0,
        "baseline_known": True,
        "pending_order": None,
        "last_negative_guard_review_bar": None,
    }
    diagnostics = {
        "closed_5m_ts": bar,
        "numeric_conditions_met": True,
        "new_closed_5m_bar": True,
        "loss_r": 0.50,
        "sl_proximity_pct": 50.0,
        "threshold_r": 0.50,
    }
    handled, persisted = _install_review_control_fakes(
        monkeypatch, lifecycle, diagnostics,
    )

    args = (
        _review_cfg(), _ReviewState(), _ReviewClient(), "ETH/USDT:USDT",
        _position(), ["5m", "1h"], "summary", {},
    )
    assert trader._maybe_negative_guard_review(*args) is True
    assert handled.ready.wait(2)
    assert len(handled) == 1
    assert handled[0][1]["review_path"] == "negative_guard"
    assert any(item.get("last_negative_guard_review_bar") == bar for item in persisted)

    # update_fields above emulates the durable state store. A second fast tick
    # on the same confirmed candle must not spend another Gemini/GPT call.
    assert trader._maybe_negative_guard_review(*args) is False
    assert handled.ready.wait(2)
    assert len(handled) == 1


def test_negative_guard_stage1_can_review_a_new_closed_5m_bar(monkeypatch):
    stage1_bar = _closed_ts(10)
    new_bar = _closed_ts(5)
    lifecycle = {
        "reduce_stage": 1,
        "cumulative_reduced_ratio": 0.25,
        "baseline_known": True,
        "pending_order": None,
        "last_processed_closed_5m_candle_ts": stage1_bar,
        "last_negative_guard_review_bar": stage1_bar,
    }
    diagnostics = {
        "closed_5m_ts": new_bar,
        "numeric_conditions_met": True,
        "new_closed_5m_bar": True,
        "loss_r": 0.75,
        "sl_proximity_pct": 75.0,
        "threshold_r": 0.75,
    }
    handled, persisted = _install_review_control_fakes(
        monkeypatch, lifecycle, diagnostics,
    )

    result = trader._maybe_negative_guard_review(
        _review_cfg(), _ReviewState(), _ReviewClient(last_price=98.5),
        "ETH/USDT:USDT", _position(), ["5m", "1h"], "summary", {},
    )

    assert result is True
    assert handled.ready.wait(2)
    assert len(handled) == 1
    assert handled[0][1]["review_path"] == "negative_guard"
    assert any(item.get("last_negative_guard_review_bar") == new_bar for item in persisted)


def test_fast_tick_does_not_call_negative_guard_ai_while_emergency_is_armed(monkeypatch):
    state = _ReviewState({"emergency_close_first_seen_at": time.time()})
    cfg = _review_cfg()
    cfg.CORE_EMERGENCY_CLOSE_ENABLED = True
    cfg.CORE_FAST_REDUCE_ENABLED = True
    client = _ReviewClient(last_price=82.0, sl_price=80.0)
    client.fetch_position = lambda: _position()
    negative_guard_calls = []
    monkeypatch.setattr(
        trader, "_maybe_emergency_close_near_stop", lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        trader, "_maybe_negative_guard_review",
        lambda *_args, **_kwargs: negative_guard_calls.append((_args, _kwargs)) or True,
    )

    result = trader._run_core_fast_tick(cfg, state, client, "ETH/USDT:USDT")

    assert result is True
    assert negative_guard_calls == []


def _run_negative_guard_gpt_action(monkeypatch, action):
    cfg = _review_cfg()
    state = _ReviewState()
    client = _ReviewClient()
    position = _position()
    calls = {
        "close": [], "reduce": [], "add": [], "blocks": [],
        "verify_kwargs": [],
    }

    monkeypatch.setattr(trader.position_ai_log, "record_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        trader.gemini_analyzer,
        "analyze_held_position",
        lambda *_args, **_kwargs: {
            "assessment": "weakening",
            "confidence": 0.90,
            "reasoning": "confirmed weakness",
        },
    )
    def verify(*_args, **kwargs):
        calls["verify_kwargs"].append(kwargs)
        return {
            "action": action,
            "confidence": 0.90,
            "reasoning": "negative guard decision",
        }

    monkeypatch.setattr(trader.openai_analyzer, "verify_position_management", verify)
    monkeypatch.setattr(
        trader.reduce_v2_state,
        "record_block",
        lambda user_dir, symbol, reason: calls["blocks"].append((symbol, reason)),
    )
    monkeypatch.setattr(
        trader,
        "_execute_close",
        lambda *args, **kwargs: calls["close"].append((args, kwargs)) or True,
    )
    monkeypatch.setattr(
        trader,
        "_execute_position_ai_reduce_50",
        lambda *args, **kwargs: calls["reduce"].append((args, kwargs)) or True,
    )
    monkeypatch.setattr(
        trader,
        "_execute_position_ai_add",
        lambda *args, **kwargs: calls["add"].append((args, kwargs)) or True,
    )
    rows = _weakening_long_rows()
    _install_closed_rows(monkeypatch, rows)

    trader._handle_position_ai_review(
        cfg, state, client, "ETH/USDT:USDT", ["5m", "1h"],
        "summary", position, {"action": "hold", "confidence": 0.0}, {},
        review_path="negative_guard",
    )
    return calls


@pytest.mark.parametrize("action", ["CLOSE_ALL", "ADD_POSITION"])
def test_negative_guard_blocks_non_reduce_gpt_actions(monkeypatch, action):
    calls = _run_negative_guard_gpt_action(monkeypatch, action)

    assert calls["close"] == []
    assert calls["reduce"] == []
    assert calls["add"] == []
    assert calls["blocks"] == [
        ("ETH/USDT:USDT", "negative_guard_requires_reduce_approval")
    ]


def test_negative_guard_dispatches_only_approved_reduce(monkeypatch):
    calls = _run_negative_guard_gpt_action(monkeypatch, "REDUCE_50")

    assert calls["close"] == []
    assert calls["add"] == []
    assert len(calls["reduce"]) == 1
    reduce_args, _ = calls["reduce"][0]
    assert reduce_args[-1]["_core_reduce_approval"]["path"] == "negative_guard"
    assert len(calls["verify_kwargs"]) == 1
    assert calls["verify_kwargs"][0]["review_path"] == "negative_guard"
    assert calls["blocks"] == []


class _CapturedOpenAIClient:
    def __init__(self, action):
        self.action = action
        self.prompts = []
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self._create),
        )

    def with_options(self, **_kwargs):
        return self

    def _create(self, **kwargs):
        self.prompts.append(kwargs["messages"][0]["content"])
        payload = {
            "action": self.action,
            "confidence": 0.90,
            "reasoning": "negative guard parser test",
        }
        return SimpleNamespace(
            choices=[SimpleNamespace(
                message=SimpleNamespace(content=json.dumps(payload)),
            )],
            usage=None,
        )


def _verify_negative_guard_with_fake_gpt(monkeypatch, action):
    client = _CapturedOpenAIClient(action)
    cfg = SimpleNamespace(
        OPENAI_API_KEY="test-key",
        OPENAI_MODEL="test-model",
        logger=logging.getLogger("test_negative_guard_gpt_prompt"),
        user_dir="/tmp/negative-guard-gpt-test",
    )
    monkeypatch.setattr(openai_analyzer, "_ensure_response_models_warmed_up", lambda: None)
    monkeypatch.setattr(openai_analyzer, "_get_client", lambda _cfg: client)
    monkeypatch.setattr(openai_analyzer.gpt_latency_log, "record_call", lambda *_args, **_kwargs: None)

    position = dict(
        _position(), unrealized_pnl=-4.0, mark_price=99.0,
    )
    result = openai_analyzer.verify_position_management(
        cfg, "ETH/USDT:USDT", ["5m", "1h"], "candle summary", position,
        {"action": "hold", "reasoning": "loss event"},
        {"assessment": "weakening", "confidence": 0.90, "reasoning": "weak"},
        protection={"sl_price": 98.0}, review_path="negative_guard",
    )
    assert len(client.prompts) == 1
    return result, client.prompts[0]


def test_negative_guard_gpt_prompt_offers_only_hold_or_reduce(monkeypatch):
    result, prompt = _verify_negative_guard_with_fake_gpt(
        monkeypatch, "REDUCE_50",
    )

    assert '"action": "HOLD" | "REDUCE_50"' in prompt
    assert '"CLOSE_ALL"' not in prompt
    assert '"ADD_POSITION"' not in prompt
    assert result["action"] == "REDUCE_50"


@pytest.mark.parametrize("disallowed_action", ["CLOSE_ALL", "ADD_POSITION"])
def test_negative_guard_gpt_parser_rejects_general_management_actions(
    monkeypatch, disallowed_action,
):
    result, _prompt = _verify_negative_guard_with_fake_gpt(
        monkeypatch, disallowed_action,
    )

    assert result["action"] is None
    assert result["error_reason"] == "parse_error"


@pytest.mark.parametrize("status_filled", [2.5, 0.0])
def test_reduce_then_exchange_stop_flat_race_clears_durable_pending(
    monkeypatch, status_filled,
):
    symbol = "ETH/USDT:USDT"
    bar = _closed_ts(5)
    position = _position()
    lifecycle_id = trader.reduce_v2_state.position_identity(position)
    lifecycle = {
        "lifecycle_id": lifecycle_id,
        "reduce_stage": 0,
        "initial_contracts": 10.0,
        "actual_reduced_contracts": 0.0,
        "cumulative_reduced_ratio": 0.0,
        "baseline_known": True,
        "pending_order": None,
        "last_processed_closed_5m_candle_ts": None,
    }
    state = _ReviewState({"entry_time": None})
    persisted = []
    cleared = []
    add_cleared = []
    blocked = []
    reduce_records = []
    close_records = []
    stage_executions = []

    class FlatAfterReduceClient:
        symbol = "ETH/USDT:USDT"

        def __init__(self):
            self.position_reads = 0
            self.reduce_calls = []

        def fetch_position(self):
            self.position_reads += 1
            return dict(position) if self.position_reads == 1 else None

        def fetch_multi_ohlcv(self, timeframes):
            assert timeframes == ["5m"]
            return {"5m": object()}

        def fetch_current_protection(self, close_side):
            assert close_side == "sell"
            return {
                "algo_id": "protect-1", "sz": 10.0,
                "sl_price": 98.0, "tp_price": 104.0,
            }

        def fetch_last_price(self):
            return 98.8

        def contract_size(self):
            return 1.0

        def reduce_position(self, approved_position, contracts, client_order_id):
            self.reduce_calls.append((approved_position, contracts, client_order_id))
            return {"id": "reduce-order-1"}

        def fetch_order_status_by_client_id(self, _client_order_id):
            return {
                "id": "reduce-order-1", "status": "closed", "filled": status_filled,
                "timestamp": 1_000, "info": {"ordId": "reduce-order-1"},
            }

        def fetch_trades_for_order(self, _order_id, _since):
            return [{
                "id": "fill-1", "order": "reduce-order-1", "symbol": self.symbol,
                "side": "sell", "amount": 2.5, "price": 98.8,
                "fee": {"currency": "USDT", "cost": 0.1}, "timestamp": 1_001,
                "info": {"tradeId": "fill-1", "ordId": "reduce-order-1"},
            }]

        def fetch_pending_protection_algo_ids(self):
            return []

        def cancel_protection(self, _algo_ids):
            raise AssertionError("flat exchange already removed the owned protection")

    client = FlatAfterReduceClient()

    monkeypatch.setattr(trader.core_kill_switch, "is_active", lambda *_args: False)
    monkeypatch.setattr(trader.core_manual_close, "get", lambda *_args: None)
    monkeypatch.setattr(trader.core_add_position_state, "get", lambda *_args: None)
    monkeypatch.setattr(
        trader.core_add_position_state, "clear",
        lambda _user_dir, _symbol: add_cleared.append(_symbol),
    )
    monkeypatch.setattr(
        trader, "_ensure_reduce_position",
        lambda *_args, **_kwargs: dict(lifecycle),
    )
    monkeypatch.setattr(
        trader.reduce_v2_state, "get",
        lambda *_args, **_kwargs: dict(lifecycle) if lifecycle else None,
    )

    def update_fields(_user_dir, _symbol, **fields):
        lifecycle.update(fields)
        persisted.append(fields)
        return dict(lifecycle)

    def clear(_user_dir, _symbol):
        lifecycle.clear()
        cleared.append(_symbol)
        return True

    monkeypatch.setattr(trader.reduce_v2_state, "update_fields", update_fields)
    monkeypatch.setattr(trader.reduce_v2_state, "clear", clear)
    monkeypatch.setattr(
        trader.reduce_v2_state, "record_stage_executed",
        lambda *_args, **_kwargs: (
            stage_executions.append((_args, _kwargs)),
            lifecycle.update(reduce_stage=1),
        ),
    )
    monkeypatch.setattr(
        trader.reduce_v2_state, "mark_candle_processed",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        trader.reduce_v2_state, "record_block",
        lambda _user_dir, _symbol, reason: blocked.append(reason),
    )
    monkeypatch.setattr(
        trader, "_negative_guard_numeric_diagnostics",
        lambda *_args, **_kwargs: {
            "numeric_conditions_met": True,
            "closed_5m_ts": bar,
            "threshold_r": 0.50,
            "loss_r": 0.60,
            "two_macd_weakening": True,
            "adverse_rsi": True,
            "adverse_close": False,
        },
    )
    monkeypatch.setattr(
        trader, "_closed_indicator_tail",
        lambda _raw, _tf, n=1: [{"ts": bar, "macd": 1.0}],
    )
    monkeypatch.setattr(
        trader.risk_manager, "quantize_coin_amount_to_market",
        lambda _client, _symbol, amount: amount,
    )
    monkeypatch.setattr(trader.position_ai_log, "record_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        trader.trade_log, "record_reduce",
        lambda *_args, **_kwargs: reduce_records.append((_args, _kwargs)),
    )
    monkeypatch.setattr(
        trader.trade_log, "last_unclosed_open",
        lambda *_args, **_kwargs: ({
            "time": "2026-09-20T00:00:00", "sl_price": 98.0, "tp_price": 104.0,
        } if status_filled == 0 else None),
    )
    monkeypatch.setattr(
        trader, "_resolve_external_close_pnl",
        lambda *_args, **_kwargs: {
            "gross_pnl": -2.0, "fee": 0.2, "net_pnl": -2.2,
            "exit_price": 98.0, "funding_fee": 0.0, "source": "okx_realized",
        },
    )
    monkeypatch.setattr(
        trader.trade_log, "record_close",
        lambda *_args, **_kwargs: close_records.append((_args, _kwargs)),
    )
    monkeypatch.setattr(trader.core_short_downgrade, "record_close", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(trader, "_notify_telegram", lambda *_args, **_kwargs: None)

    cfg = SimpleNamespace(
        EXECUTION_MODE="LIVE", CORE_NEGATIVE_GUARD_ENABLED=True,
        STOP_LOSS_PCT=1.5, MIN_CONFIDENCE=0.60,
        user_dir="/tmp/negative-guard-flat-race",
        logger=logging.getLogger("test_negative_guard_flat_race"),
    )
    raw_dfs = {
        "_core_reduce_approval": {
            "started_at": time.time(), "path": "negative_guard",
            "lifecycle_id": lifecycle_id, "bar_ts": bar,
            "gemini_confidence": 0.90,
        },
    }

    original = getattr(
        trader._execute_position_ai_reduce_50_locked,
        "__wrapped__", trader._execute_position_ai_reduce_50_locked,
    )
    original(
        cfg, state, client, symbol, position, "review-1", 0.90,
        "weakening", raw_dfs,
    )

    assert len(client.reduce_calls) == 1, (blocked, lifecycle, persisted)
    assert lifecycle.get("pending_order") is None
    assert cleared == [symbol] or any(
        item.get("pending_order", object()) is None for item in persisted
    )
    assert any(
        fields.get("position", object()) is None
        and fields.get("live_position", object()) is None
        for _symbol, fields in state.updates
    )
    assert "remaining_position_unknown_or_flat" not in blocked
    if status_filled == 0:
        assert reduce_records == []
        assert stage_executions == []
        assert len(close_records) == 1
        close_args, close_kwargs = close_records[0]
        assert close_args[4] == pytest.approx(position["contracts"])
        assert close_kwargs["execution_id"] == f"core-external-close:{lifecycle_id}"
    else:
        assert len(reduce_records) == 1
        assert len(stage_executions) == 1


def test_reconcile_zero_fill_flat_uses_full_close_finalizer(monkeypatch):
    symbol = "ETH/USDT:USDT"
    position = _position()
    pending = {
        "client_order_id": "cr-zero-flat", "lifecycle_id": trader.reduce_v2_state.position_identity(position),
        "contracts": 2.5, "before_contracts": 10.0, "stage": 1,
        "bar_ts": _closed_ts(5), "dedup_tf": "5m", "position": position,
        "protection": {"algo_id": "protect-1", "sl_price": 98.0, "tp_price": 104.0, "sz": 10.0},
    }
    finalized = []
    reduce_records = []
    stage_executions = []

    class Client:
        def fetch_order_status_by_client_id(self, client_order_id):
            assert client_order_id == pending["client_order_id"]
            return {"status": "canceled", "filled": 0.0, "id": "order-1"}

        def fetch_position(self):
            return None

    monkeypatch.setattr(trader.cc_ownership, "account_order_lock", lambda *_args: contextlib.nullcontext())
    monkeypatch.setattr(
        trader.reduce_v2_state, "get",
        lambda *_args, **_kwargs: {"pending_order": pending},
    )
    monkeypatch.setattr(
        trader, "_finalize_flat_after_core_reduce",
        lambda *args, **kwargs: finalized.append((args, kwargs)) or True,
    )
    monkeypatch.setattr(
        trader.trade_log, "record_reduce",
        lambda *_args, **_kwargs: reduce_records.append((_args, _kwargs)),
    )
    monkeypatch.setattr(
        trader.reduce_v2_state, "record_stage_executed",
        lambda *_args, **_kwargs: stage_executions.append((_args, _kwargs)),
    )
    cfg = SimpleNamespace(
        EXECUTION_MODE="LIVE", user_dir="/tmp/negative-guard-reconcile-zero-flat",
        logger=logging.getLogger("test_reconcile_zero_fill_flat"),
    )

    assert trader._reconcile_pending_core_reduce(
        cfg, _ReviewState(), Client(), symbol,
    ) is True
    assert len(finalized) == 1
    assert finalized[0][0][5] == 0.0
    assert reduce_records == []
    assert stage_executions == []


def test_reduce_protection_is_amended_in_place_and_verified_by_same_algo_id(monkeypatch):
    symbol = "ETH/USDT:USDT"
    position = _position()
    actual = dict(position, contracts=7.5)
    pending = {
        "client_order_id": "cr-amend", "lifecycle_id": trader.reduce_v2_state.position_identity(position),
        "before_contracts": 10.0, "position": position,
        "protection": {
            "algo_id": "protect-1", "sl_price": 98.0, "tp_price": 104.0, "sz": 10.0,
        },
        "protection_target": {"sl_price": 99.0, "tp_price": 104.0},
    }
    persisted = []

    class Client:
        def __init__(self):
            self.protection = {
                "algo_id": "protect-1", "sl_price": 98.0, "tp_price": 104.0,
                "sz": 10.0, "side": "sell", "state": "live",
            }
            self.amends = []

        def fetch_position(self):
            return dict(actual)

        def fetch_protection_order_by_algo_id(self, algo_id):
            assert algo_id == "protect-1"
            return dict(self.protection)

        def amend_protective_stop(self, algo_id, *, new_sl_price=None, new_sz=None):
            self.amends.append((algo_id, new_sl_price, new_sz))
            self.protection.update(sl_price=new_sl_price, sz=new_sz)
            return {"ok": True, "algo_id": algo_id}

        def cancel_protection(self, *_args, **_kwargs):
            raise AssertionError("existing OCO must never be canceled before resize")

        def attach_protection(self, *_args, **_kwargs):
            raise AssertionError("resize must preserve the same algo_id")

    client = Client()
    monkeypatch.setattr(
        trader.reduce_v2_state, "update_fields",
        lambda _user_dir, _symbol, **fields: persisted.append(fields) or fields,
    )
    cfg = SimpleNamespace(
        user_dir="/tmp/negative-guard-amend", logger=logging.getLogger("test_reduce_protection_amend"),
    )

    assert trader._restore_core_reduce_protection(cfg, client, symbol, pending, actual) is True
    assert client.amends == [("protect-1", 99.0, 7.5)]
    assert any(
        fields["pending_order"].get("protection_amend_submitted") is True
        for fields in persisted
    )
    assert client.protection["algo_id"] == "protect-1"


def test_reduce_protection_restart_observes_completed_amend_without_resubmit(monkeypatch):
    symbol = "ETH/USDT:USDT"
    position = _position()
    actual = dict(position, contracts=7.5)
    pending = {
        "client_order_id": "cr-amend", "lifecycle_id": trader.reduce_v2_state.position_identity(position),
        "before_contracts": 10.0, "position": position,
        "protection": {
            "algo_id": "protect-1", "sl_price": 98.0, "tp_price": 104.0, "sz": 10.0,
        },
        "protection_target": {"sl_price": 99.0, "tp_price": 104.0},
        "protection_amend_submitted": True,
    }

    class Client:
        def fetch_position(self):
            return dict(actual)

        def fetch_protection_order_by_algo_id(self, algo_id):
            assert algo_id == "protect-1"
            return {
                "algo_id": "protect-1", "sl_price": 99.0, "tp_price": 104.0,
                "sz": 7.5, "side": "sell", "state": "live",
            }

        def amend_protective_stop(self, *_args, **_kwargs):
            raise AssertionError("restart must observe before deciding whether to submit")

        def cancel_protection(self, *_args, **_kwargs):
            raise AssertionError("existing OCO must not be canceled")

        def attach_protection(self, *_args, **_kwargs):
            raise AssertionError("existing OCO must not be replaced")

    monkeypatch.setattr(trader.reduce_v2_state, "update_fields", lambda *_args, **_kwargs: None)
    cfg = SimpleNamespace(
        user_dir="/tmp/negative-guard-amend-restart",
        logger=logging.getLogger("test_reduce_protection_amend_restart"),
    )

    assert trader._restore_core_reduce_protection(
        cfg, Client(), symbol, pending, actual,
    ) is True


def test_negative_guard_disable_after_ai_approval_blocks_late_reduce(monkeypatch):
    symbol = "ETH/USDT:USDT"
    position = _position()
    lifecycle_id = trader.reduce_v2_state.position_identity(position)
    lifecycle = {
        "lifecycle_id": lifecycle_id, "baseline_known": True, "pending_order": None,
        "cumulative_reduced_ratio": 0.0, "reduce_stage": 0, "initial_contracts": 10.0,
    }
    blocked = []

    class Client:
        def fetch_position(self):
            return dict(position)

        def fetch_multi_ohlcv(self, _timeframes):
            raise AssertionError("disabled negative guard must stop before fresh market reads")

    cfg = _review_cfg()
    cfg.CORE_NEGATIVE_GUARD_ENABLED = False
    monkeypatch.setattr(trader.core_kill_switch, "is_active", lambda *_args: False)
    monkeypatch.setattr(trader.core_manual_close, "get", lambda *_args: None)
    monkeypatch.setattr(trader.core_add_position_state, "get", lambda *_args: None)
    monkeypatch.setattr(
        trader, "_ensure_reduce_position", lambda *_args, **_kwargs: dict(lifecycle),
    )
    monkeypatch.setattr(
        trader.reduce_v2_state, "record_block",
        lambda _user_dir, _symbol, reason: blocked.append(reason),
    )
    raw_dfs = {"_core_reduce_approval": {
        "started_at": time.time(), "path": "negative_guard",
        "lifecycle_id": lifecycle_id, "bar_ts": _closed_ts(5),
        "gemini_confidence": 0.90,
    }}
    original = getattr(
        trader._execute_position_ai_reduce_50_locked,
        "__wrapped__", trader._execute_position_ai_reduce_50_locked,
    )

    assert original(
        cfg, _ReviewState({"entry_time": None}), Client(), symbol, position,
        "review-disabled", 0.90, "weakening", raw_dfs,
    ) is False
    assert blocked == ["negative_guard_disabled_after_approval"]


def _run_in_place_reduce_protection_resize(monkeypatch, *, tp_price):
    symbol = "ETH/USDT:USDT"
    before = _position()
    actual = dict(before, contracts=7.5)
    lifecycle_id = trader.reduce_v2_state.position_identity(before)
    pending = {
        "client_order_id": "reduce-amend-1",
        "lifecycle_id": lifecycle_id,
        "before_contracts": 10.0,
        "position": before,
        "protection": {
            "algo_id": "protect-same-id", "sz": 10.0,
            "sl_price": 98.0, "tp_price": tp_price,
        },
        "protection_target": {"sl_price": 99.0, "tp_price": tp_price},
    }
    updates = []

    class Exchange:
        def market(self, _symbol):
            return {
                "id": "ETH-USDT-SWAP",
                "precision": {"amount": 0.1, "price": 0.1},
            }

    class AmendOnlyClient:
        symbol = "ETH/USDT:USDT"

        def __init__(self):
            self.exchange = Exchange()
            self.protection = {
                "algo_id": "protect-same-id", "algo_cl_ord_id": "original-cid",
                "side": "sell", "state": "live", "sz": 10.0,
                "sl_price": 98.0, "tp_price": tp_price,
            }
            self.amend_calls = []
            self.cancel_calls = []
            self.attach_calls = []

        def ensure_markets_loaded(self):
            return None

        def fetch_position(self):
            return dict(actual)

        def fetch_current_protection(self, close_side):
            assert close_side == "sell"
            return dict(self.protection)

        def fetch_protection_order_by_algo_id(self, algo_id):
            assert algo_id == "protect-same-id"
            return dict(self.protection)

        def amend_protective_stop(self, algo_id, *, new_sl_price=None, new_sz=None):
            self.amend_calls.append((algo_id, new_sl_price, new_sz))
            self.protection.update(sl_price=new_sl_price, sz=new_sz)
            return {"ok": True, "algo_id": algo_id}

        def cancel_protection(self, algo_ids):
            self.cancel_calls.append(algo_ids)
            raise AssertionError("owned protection must be amended in place")

        def attach_protection(self, *args, **kwargs):
            self.attach_calls.append((args, kwargs))
            raise AssertionError("in-place amend must not attach a replacement")

    client = AmendOnlyClient()
    monkeypatch.setattr(
        trader.reduce_v2_state, "update_fields",
        lambda _user_dir, _symbol, **fields: updates.append(fields),
    )
    cfg = SimpleNamespace(user_dir="/tmp/negative-guard-amend")

    result = trader._restore_core_reduce_protection(
        cfg, client, symbol, pending, actual,
    )
    return result, client, updates


def test_reduce_protection_resize_supports_sl_only_without_tp(monkeypatch):
    result, client, _updates = _run_in_place_reduce_protection_resize(
        monkeypatch, tp_price=None,
    )

    assert result is True
    assert client.amend_calls == [("protect-same-id", 99.0, 7.5)]
    assert client.protection["tp_price"] is None
    assert client.cancel_calls == []
    assert client.attach_calls == []


def test_emergency_close_uses_exchange_last_price_instead_of_stale_mark(monkeypatch):
    symbol = "ETH/USDT:USDT"
    state = _ReviewState({"emergency_close_first_seen_at": 100.0})
    position = dict(_position(), mark_price=100.0)
    close_calls = []

    class LastPriceEmergencyClient(_ReviewClient):
        def __init__(self):
            super().__init__(last_price=98.1, sl_price=98.0)
            self.last_price_reads = 0

        def fetch_last_price(self):
            self.last_price_reads += 1
            return self.last_price

    client = LastPriceEmergencyClient()
    cfg = _review_cfg()
    cfg.CORE_EMERGENCY_CLOSE_ENABLED = True
    monkeypatch.setattr(trader.time, "time", lambda: 130.0)
    monkeypatch.setattr(
        trader, "_execute_close",
        lambda *_args, **kwargs: close_calls.append(kwargs) or True,
    )

    result = trader._maybe_emergency_close_near_stop(
        cfg, state, client, symbol, position,
    )

    assert result is True
    assert client.last_price_reads == 1
    assert close_calls == [{"reason": "sl_proximity_emergency_close"}]
    assert state.symbol_state["emergency_close_first_seen_at"] is None


def test_negative_guard_async_does_not_block_tick_and_releases_after_error(monkeypatch):
    lifecycle = dict(reduce_stage=0, baseline_known=True, pending_order=None)
    diagnostics = dict(closed_5m_ts=_closed_ts(5), numeric_conditions_met=True,
                       loss_r=.6, sl_proximity_pct=60., threshold_r=.5)
    _install_review_control_fakes(monkeypatch, lifecycle, diagnostics)
    started, release = threading.Event(), threading.Event()
    threads = []
    real_thread = threading.Thread
    def make_thread(*args, **kwargs):
        thread = real_thread(*args, **kwargs)
        threads.append(thread)
        return thread
    monkeypatch.setattr(trader.threading, 'Thread', make_thread)
    def blocked_review(*args, **kwargs):
        assert lifecycle['last_negative_guard_review_bar'] == diagnostics['closed_5m_ts']
        started.set()
        release.wait(3)
        raise RuntimeError('model unavailable')
    monkeypatch.setattr(trader, '_handle_position_ai_review', blocked_review)
    cfg = _review_cfg()
    args = (cfg, _ReviewState(), _ReviewClient(), 'ASYNC/USDT:USDT',
            _position(), ['5m'], 'summary', {})
    try:
        before = time.monotonic()
        assert trader._maybe_negative_guard_review(*args) is True
        assert time.monotonic() - before < 1
        assert started.wait(1)
        assert threads[0].daemon is True
        # Even a fresh candle must not enqueue another in-flight model review.
        diagnostics['closed_5m_ts'] = _closed_ts(4)
        assert trader._maybe_negative_guard_review(*args) is False
        assert len(threads) == 1
    finally:
        release.set()
        for thread in threads:
            thread.join(2)
    key = (trader.os.path.realpath(cfg.user_dir), args[3])
    assert key not in trader._negative_guard_workers


def test_negative_guard_worker_start_failure_keeps_durable_dedupe(monkeypatch):
    lifecycle = dict(reduce_stage=0, baseline_known=True, pending_order=None)
    diagnostics = dict(closed_5m_ts=_closed_ts(5), numeric_conditions_met=True,
                       loss_r=.6, sl_proximity_pct=60., threshold_r=.5)
    handled, _ = _install_review_control_fakes(monkeypatch, lifecycle, diagnostics)
    class FailedThread:
        def __init__(self, **kwargs): pass
        def start(self): raise RuntimeError('thread unavailable')
    monkeypatch.setattr(trader.threading, 'Thread', FailedThread)
    cfg = _review_cfg()
    args = (cfg, _ReviewState(), _ReviewClient(), 'FAIL/USDT:USDT',
            _position(), ['5m'], 'summary', {})
    assert trader._maybe_negative_guard_review(*args) is False
    assert lifecycle['last_negative_guard_review_bar'] == diagnostics['closed_5m_ts']
    assert (trader.os.path.realpath(cfg.user_dir), args[3]) not in trader._negative_guard_workers
    assert not handled
    assert trader._maybe_negative_guard_review(*args) is False


def test_partial_reduce_fill_keeps_stage_open_and_accumulates_exact_quantity():
    symbol = "ETH/USDT:USDT"
    position = _position()
    with tempfile.TemporaryDirectory() as user_dir:
        trader.reduce_v2_state.ensure_position(
            user_dir, symbol, position, initial_contracts=10.0,
        )
        trader.reduce_v2_state.record_stage_executed(
            user_dir, symbol, stage=1, filled_contracts=4.0,
            execution_id="partial-1", stage_completed=False,
        )
        partial = trader.reduce_v2_state.get(user_dir, symbol)
        assert partial["reduce_stage"] == 0
        assert partial["actual_reduced_contracts"] == pytest.approx(4.0)
        assert partial["cumulative_reduced_ratio"] == pytest.approx(0.4)

        trader.reduce_v2_state.record_stage_executed(
            user_dir, symbol, stage=1, filled_contracts=6.0,
            execution_id="remainder-1", stage_completed=True,
        )
        completed = trader.reduce_v2_state.get(user_dir, symbol)
        assert completed["reduce_stage"] == 1
        assert completed["actual_reduced_contracts"] == pytest.approx(10.0)


def test_partial_reduce_execution_id_is_idempotent():
    symbol = "ETH/USDT:USDT"
    position = _position()
    with tempfile.TemporaryDirectory() as user_dir:
        trader.reduce_v2_state.ensure_position(
            user_dir, symbol, position, initial_contracts=10.0,
        )
        for _ in range(2):
            trader.reduce_v2_state.record_stage_executed(
                user_dir, symbol, stage=1, filled_contracts=4.0,
                execution_id="same-partial", stage_completed=False,
            )
        state = trader.reduce_v2_state.get(user_dir, symbol)
        assert state["reduce_stage"] == 0
        assert state["actual_reduced_contracts"] == pytest.approx(4.0)


def _run_gpt_failure_review(monkeypatch, *, review_path, error_reason):
    cfg = _review_cfg()
    state = _ReviewState()
    client = _ReviewClient()
    position = _position()
    calls = {"reduce": [], "close": [], "add": [], "events": []}

    monkeypatch.setattr(
        trader.position_ai_log, "record_event",
        lambda *args, **kwargs: calls["events"].append((args, kwargs)),
    )
    monkeypatch.setattr(
        trader.gemini_analyzer, "analyze_held_position",
        lambda *_args, **_kwargs: {
            "assessment": "weakening",
            "confidence": 0.90,
            "reasoning": "confirmed weakness",
        },
    )
    monkeypatch.setattr(
        trader.openai_analyzer, "verify_position_management",
        lambda *_args, **_kwargs: {"action": None, "error_reason": error_reason},
    )
    monkeypatch.setattr(
        trader, "_execute_position_ai_reduce_50",
        lambda *args, **kwargs: calls["reduce"].append((args, kwargs)) or True,
    )
    monkeypatch.setattr(
        trader, "_execute_close",
        lambda *args, **kwargs: calls["close"].append((args, kwargs)) or True,
    )
    monkeypatch.setattr(
        trader, "_execute_position_ai_add",
        lambda *args, **kwargs: calls["add"].append((args, kwargs)) or True,
    )
    _install_closed_rows(monkeypatch, _weakening_long_rows())

    trader._handle_position_ai_review(
        cfg, state, client, "ETH/USDT:USDT", ["5m", "1h"],
        "summary", position, {"action": "hold", "confidence": 0.0}, {},
        review_path=review_path,
    )
    return calls


def test_negative_guard_gpt_timeout_falls_back_to_capped_reduce(monkeypatch):
    calls = _run_gpt_failure_review(
        monkeypatch, review_path="negative_guard", error_reason="timeout",
    )

    assert len(calls["reduce"]) == 1
    reduce_args, _ = calls["reduce"][0]
    approval = reduce_args[-1]["_core_reduce_approval"]
    assert approval["path"] == "negative_guard"
    assert approval["gpt_timeout_fallback"] is True
    assert calls["close"] == []
    assert calls["add"] == []


def test_negative_guard_non_timeout_gpt_error_remains_hold(monkeypatch):
    calls = _run_gpt_failure_review(
        monkeypatch, review_path="negative_guard", error_reason="parse_error",
    )

    assert calls["reduce"] == []
    assert calls["close"] == []
    assert calls["add"] == []


def test_general_position_ai_timeout_remains_hold(monkeypatch):
    calls = _run_gpt_failure_review(
        monkeypatch, review_path="general", error_reason="timeout",
    )

    assert calls["reduce"] == []
    assert calls["close"] == []
    assert calls["add"] == []
