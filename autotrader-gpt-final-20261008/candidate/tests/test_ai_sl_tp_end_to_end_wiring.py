import json
import logging
from types import SimpleNamespace

import adaptive_exit_engine as ae
import candidate_c_gpt_gate_adapter as gga
import candidate_c_hybrid_live_adapter as live
import openai_analyzer


def _plan(stop=96.0, tp1=106.0, tp2=112.0):
    return {
        "stop_loss_price": stop,
        "take_profit_1_price": tp1,
        "take_profit_2_price": tp2,
        "confidence": 0.85,
        "reasoning": "structure-aware plan",
    }


def test_core_recovers_valid_gpt_price_plan_when_gemini_plan_missing():
    selected, source = ae.select_verified_ai_price_plan(
        {"exit_plan": None},
        {"exit_plan_decision": "approve", "exit_plan": _plan()},
    )
    assert source == "gpt_recovered_missing_gemini"
    assert selected["stop_loss_price"] == 96.0


class _EntryGateClient:
    def __init__(self, payload):
        self.payload = payload
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def with_options(self, **_kwargs):
        return self

    def _create(self, **_kwargs):
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(self.payload)))],
            usage=None,
        )


def test_openai_entry_gate_keeps_valid_recovery_plan_when_gemini_plan_missing(monkeypatch, tmp_path):
    payload = {
        "decision": "approve_now",
        "confidence": 0.91,
        "exit_plan_decision": "approve",
        "exit_plan": _plan(),
        "reasoning": "direction approved and explicit fallback prices supplied",
    }
    client = _EntryGateClient(payload)
    cfg = SimpleNamespace(
        OPENAI_API_KEY="test", OPENAI_MODEL="test-model", logger=logging.getLogger("ai-sltp-test"),
        user_dir=str(tmp_path),
    )
    monkeypatch.setattr(openai_analyzer, "_ensure_response_models_warmed_up", lambda: None)
    monkeypatch.setattr(openai_analyzer, "_get_client", lambda _cfg: client)
    monkeypatch.setattr(openai_analyzer.gpt_latency_log, "record_call", lambda *_a, **_k: None)
    result = openai_analyzer.verify(
        cfg, "X/USDT:USDT", ["5m", "1h"], "summary", None,
        {"action": "long", "confidence": 0.8, "exit_plan": None, "reasoning": "entry"},
        timeout=1.0, purpose="entry_gate", max_retries=0,
    )
    assert result["exit_plan_decision"] == "revise"
    assert result["exit_plan"]["stop_loss_price"] == 96.0


def test_candidate_c_gate_uses_gemini_price_proposal_and_returns_verified_plan(monkeypatch, tmp_path):
    intent = SimpleNamespace(
        side="long", setup_id="setup-1", reason_code="donchian_breakout",
        raw_stop_price=94.0, raw_target_price=112.0,
    )
    snapshot = {
        "symbol": "SOL/USDT:USDT", "tf_list": ["4h", "1h", "5m"], "position": None,
        "trend_direction": "LONG", "donchian_upper": 100.0, "donchian_lower": 90.0,
        "current_price": 101.0, "atr_4h": 2.0,
    }
    gemini = {"action": "long", "confidence": 0.8, "exit_plan": _plan(97.0, 107.0, 113.0), "reasoning": "levels"}
    monkeypatch.setattr(gga, "gemini_analyzer", SimpleNamespace(propose_entry_exit_plan=lambda *a, **k: gemini), raising=False)
    monkeypatch.setattr(gga.candidate_c_gpt_gate_log, "record", lambda *a, **k: None)
    monkeypatch.setattr(gga.openai_analyzer, "verify", lambda *a, **k: {
        "decision": "approve_now", "confidence": 0.9, "reasoning": "ok",
        "exit_plan_decision": "approve", "exit_plan": None,
    })
    cfg = SimpleNamespace(user_dir=str(tmp_path), logger=logging.getLogger("cc-ai-sltp"))
    result = gga.verify_candidate_signal(cfg, intent, snapshot, is_still_valid_fn=lambda: True)
    assert result["allowed"] is True
    assert result["ai_exit_source"] == "gemini_approved"
    assert result["ai_exit_plan"]["stop_loss_price"] == 97.0


def test_candidate_c_live_overlay_replaces_intent_prices_only_when_ai_plan_is_safe():
    overlay = getattr(live, "_apply_verified_ai_exit_plan_to_intent", None)
    assert overlay is not None, "Candidate C live adapter must apply verified AI exit plans"
    intent = SimpleNamespace(side="long", raw_stop_price=94.0, raw_target_price=112.0)
    gate = {"ai_exit_source": "gemini_approved", "ai_exit_plan": _plan(97.0, 107.0, 113.0)}
    updated, reason = overlay(intent, gate, entry_price=101.0, atr_4h=2.0)
    assert reason == "ai_exit_plan_applied"
    assert updated.raw_stop_price == 97.0
    assert updated.raw_target_price == 113.0


def test_candidate_c_execute_entry_applies_ai_prices_before_sizing(monkeypatch, tmp_path):
    from contextlib import nullcontext
    import pytest
    import symbol_entry_control

    class StopHere(Exception):
        pass

    from candidate_c_decision_engine import Intent
    intent = Intent(
        kind="EntryIntent", account_id="acct", symbol="SOL/USDT:USDT", strategy_id="candidate_c",
        setup_id="setup-1", position_epoch=None, config_version_id="v1", config_hash="cfg",
        decision_timestamp=1, source_candle_close_timestamp=1, side="long", idempotency_key="idem",
        reason_code="donchian_breakout", input_snapshot_hash="snap",
        raw_stop_price=94.0, raw_target_price=112.0, requested_risk_pct=1.0,
    )
    cfg = SimpleNamespace(
        user_dir=str(tmp_path), logger=logging.getLogger("cc-exec-ai-sltp"),
        CANDIDATE_C_GPT_ENTRY_GATE_ENABLED=True, CANDIDATE_C_LIVE_EXECUTE=True,
        CANDIDATE_C_LEVERAGE=5, CANDIDATE_C_ORDER_MODE="FIXED_MARGIN_AUTO_EXIT",
    )
    gate = {
        "allowed": True, "gate_result": "approved", "error_reason": None,
        "ai_exit_source": "gemini_approved", "ai_exit_plan": _plan(97.0, 107.0, 113.0),
    }
    monkeypatch.setattr(live, "_candidate_c_new_entry_allowed", lambda *a, **k: True)
    monkeypatch.setattr(live, "_candidate_manual_close_entry_block", lambda *a, **k: None)
    monkeypatch.setattr(live.gga, "verify_candidate_signal", lambda *a, **k: gate)
    monkeypatch.setattr(live.ownership, "account_order_lock", lambda *_a, **_k: nullcontext())
    monkeypatch.setattr(symbol_entry_control, "is_paused", lambda *a, **k: False)
    monkeypatch.setattr(live, "_candidate_c_entry_protection_prices", lambda cfg, intent, price: (94.0, 112.0))

    def check_sizing(_cfg, _client, updated, fresh_price, fresh_equity):
        assert updated.raw_stop_price == 97.0
        assert updated.raw_target_price == 113.0
        raise StopHere

    monkeypatch.setattr(live, "_calculate_candidate_c_entry_amount", check_sizing)
    client = SimpleNamespace(
        fetch_usdt_equity=lambda: 1000.0,
        fetch_position=lambda: None,
        ensure_leverage=lambda lev: None,
        fetch_pending_protection_algo_ids=lambda: [],
        fetch_last_price=lambda: 101.0,
    )
    ledger = SimpleNamespace(refresh=lambda: None, pending_intents=lambda: [])
    with pytest.raises(StopHere):
        live._execute_entry(
            cfg, client, intent, {"atr_4h": 2.0}, 1000.0, lambda: True,
            None, ledger=ledger, epoch_store=None,
        )


def test_exit_plan_only_review_is_independent_of_entry_decision(monkeypatch, tmp_path):
    payload = {
        'decision': 'wait',
        'confidence': 0.70,
        'exit_plan_decision': 'revise',
        'exit_plan': _plan(97.0, 107.0, 113.0),
        'reasoning': 'entry timing opinion must not control exit-price review',
    }
    client = _EntryGateClient(payload)
    cfg = SimpleNamespace(
        OPENAI_API_KEY='test', OPENAI_MODEL='test-model', logger=logging.getLogger('exit-plan-only'),
        user_dir=str(tmp_path),
    )
    monkeypatch.setattr(openai_analyzer, '_ensure_response_models_warmed_up', lambda: None)
    monkeypatch.setattr(openai_analyzer, '_get_client', lambda _cfg: client)
    monkeypatch.setattr(openai_analyzer.gpt_latency_log, 'record_call', lambda *_a, **_k: None)
    result = openai_analyzer.verify(
        cfg, 'SOL/USDT:USDT', ['4h', '1h', '5m'], 'summary', None,
        {'action': 'long', 'confidence': 0.8, 'exit_plan': _plan(96.0, 106.0, 112.0)},
        timeout=1.0, purpose='exit_plan_only', max_retries=0,
    )
    assert result['decision'] == 'wait'
    assert result['exit_plan_decision'] == 'revise'
    assert result['exit_plan']['stop_loss_price'] == 97.0


def test_candidate_c_rule_entry_gpt_off_still_applies_ai_exit_review_before_sizing(monkeypatch, tmp_path):
    from contextlib import nullcontext
    import pytest
    import symbol_entry_control
    from candidate_c_decision_engine import Intent

    class StopHere(Exception):
        pass

    intent = Intent(
        kind='EntryIntent', account_id='acct', symbol='SOL/USDT:USDT', strategy_id='candidate_c',
        setup_id='setup-2', position_epoch=None, config_version_id='v1', config_hash='cfg',
        decision_timestamp=1, source_candle_close_timestamp=1, side='long', idempotency_key='idem2',
        reason_code='donchian_breakout', input_snapshot_hash='snap2',
        raw_stop_price=94.0, raw_target_price=112.0, requested_risk_pct=1.0,
    )
    cfg = SimpleNamespace(
        user_dir=str(tmp_path), logger=logging.getLogger('cc-rule-ai-exit'),
        CANDIDATE_C_GPT_ENTRY_GATE_ENABLED=False, CANDIDATE_C_AI_EXIT_PLAN_ENABLED=True,
        CANDIDATE_C_LIVE_EXECUTE=True, CANDIDATE_C_LEVERAGE=5,
        CANDIDATE_C_ORDER_MODE='FIXED_MARGIN_AUTO_EXIT',
    )
    monkeypatch.setattr(live, '_candidate_c_new_entry_allowed', lambda *a, **k: True)
    monkeypatch.setattr(live, '_candidate_manual_close_entry_block', lambda *a, **k: None)
    monkeypatch.setattr(live.gga, 'rule_based_entry_without_gpt', lambda *a, **k: {
        'allowed': True, 'gate_result': 'rule_based_no_gpt_review', 'error_reason': None,
        'gpt_confidence': None, 'gpt_reasoning': '',
    })
    reviewed = []
    monkeypatch.setattr(live.gga, 'review_exit_plan_only', lambda *a, **k: reviewed.append(1) or {
        'ai_exit_source': 'gemini_approved', 'ai_exit_plan': _plan(97.0, 107.0, 113.0),
        'gemini_exit_plan': _plan(97.0, 107.0, 113.0), 'exit_review_error': None,
    }, raising=False)
    monkeypatch.setattr(live.ownership, 'account_order_lock', lambda *_a, **_k: nullcontext())
    monkeypatch.setattr(symbol_entry_control, 'is_paused', lambda *a, **k: False)
    monkeypatch.setattr(live, '_candidate_c_entry_protection_prices', lambda cfg, intent, price: (94.0, 112.0))

    def check_sizing(_cfg, _client, updated, fresh_price, fresh_equity):
        assert reviewed == [1]
        assert updated.raw_stop_price == 97.0
        assert updated.raw_target_price == 113.0
        raise StopHere

    monkeypatch.setattr(live, '_calculate_candidate_c_entry_amount', check_sizing)
    client = SimpleNamespace(
        fetch_usdt_equity=lambda: 1000.0, fetch_position=lambda: None,
        ensure_leverage=lambda lev: None, fetch_pending_protection_algo_ids=lambda: [],
        fetch_last_price=lambda: 101.0,
    )
    ledger = SimpleNamespace(refresh=lambda: None, pending_intents=lambda: [])
    with pytest.raises(StopHere):
        live._execute_entry(
            cfg, client, intent, {'atr_4h': 2.0, 'symbol': intent.symbol, 'tf_list': ['4h', '1h', '5m']},
            1000.0, lambda: True, None, ledger=ledger, epoch_store=None,
        )


def test_candidate_c_runtime_exposes_independent_ai_exit_plan_switch():
    import candidate_c_runtime
    cfg = SimpleNamespace(
        CANDIDATE_C_ENABLED=True, CANDIDATE_C_LIVE_EXECUTE=True,
        CANDIDATE_C_SYMBOLS=['DOGE/USDT:USDT', 'SOL/USDT:USDT'],
        CANDIDATE_C_SIZING_MODE='FIXED_MARGIN', CANDIDATE_C_RISK_PER_TRADE_PCT=1.0,
        CANDIDATE_C_FIXED_MARGIN_USDT=250.0, CANDIDATE_C_LEVERAGE=5,
        CANDIDATE_C_MAX_ORDER_NOTIONAL_USDT=5000.0,
        CANDIDATE_C_MAX_CONCURRENT_POSITIONS=2, CANDIDATE_C_MAX_DAILY_LOSS_PCT=10.0,
        ACCOUNT_HARD_DAILY_LOSS_PCT=10.0, CANDIDATE_C_GPT_ENTRY_GATE_ENABLED=False,
        CANDIDATE_C_AI_EXIT_PLAN_ENABLED=True,
    )
    settings = candidate_c_runtime.effective_settings(cfg)
    assert settings['gpt_entry_gate_enabled'] is False
    assert settings['ai_exit_plan_enabled'] is True
