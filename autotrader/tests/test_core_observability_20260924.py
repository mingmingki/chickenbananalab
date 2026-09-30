import json
import logging
from types import SimpleNamespace

import openai_analyzer
import pnl_reconciliation
import pytest
import trader
from state import TraderState


SYMBOL = "PI/USDT:USDT"


def test_core_entry_attempt_is_stored_for_dashboard():
    state = TraderState()
    trader._record_core_entry_attempt(
        state, SYMBOL, "short", "LOCAL_BLOCKED", reason="short_level_none",
        confidence=0.73,
    )
    attempt = state.snapshot()["symbols"][SYMBOL]["last_entry_attempt"]
    assert attempt["action"] == "short"
    assert attempt["status"] == "LOCAL_BLOCKED"
    assert attempt["reason"] == "short_level_none"
    assert attempt["confidence"] == 0.73
    assert attempt["time"]


def test_entry_attempt_observability_never_breaks_trading_when_state_write_fails():
    class BrokenState:
        def update_symbol(self, *_args, **_kwargs):
            raise RuntimeError("observability store unavailable")

    trader._record_core_entry_attempt(
        BrokenState(), SYMBOL, "short", "LOCAL_BLOCKED", reason="short_level_none",
        confidence=0.73,
    )


def test_pnl_aggregate_exposes_exact_net_adjustment():
    agg = pnl_reconciliation._agg([
        {"pnl": 10.0, "fee": 2.0, "okx_net_pnl": 7.5},
        {"pnl": -1.0, "fee": 0.5, "okx_net_pnl": -1.7},
    ])
    # Gross 9 - fee 2.5 = 6.5, but exact OKX net is 5.8 => adjustment -0.7.
    assert agg["net_pnl"] == pytest.approx(5.8)
    assert agg["net_adjustment"] == pytest.approx(-0.7)
    assert agg["net_pnl"] == pytest.approx(agg["gross_pnl"] - agg["fee"] + agg["net_adjustment"])


class _CapturedClient:
    def __init__(self):
        self.prompts = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def with_options(self, **_kwargs):
        return self

    def _create(self, **kwargs):
        self.prompts.append(kwargs["messages"][0]["content"])
        payload = {"action": "CLOSE_ALL", "confidence": 0.9, "reasoning": "exit risk"}
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))],
            usage=None,
        )


def test_exit_escalation_gpt_prompt_never_offers_add_position(monkeypatch, tmp_path):
    client = _CapturedClient()
    cfg = SimpleNamespace(
        OPENAI_API_KEY="test", OPENAI_MODEL="test-model", logger=logging.getLogger("exit-prompt"),
        user_dir=str(tmp_path),
    )
    monkeypatch.setattr(openai_analyzer, "_ensure_response_models_warmed_up", lambda: None)
    monkeypatch.setattr(openai_analyzer, "_get_client", lambda _cfg: client)
    monkeypatch.setattr(openai_analyzer.gpt_latency_log, "record_call", lambda *_a, **_k: None)
    result = openai_analyzer.verify_position_management(
        cfg, SYMBOL, ["5m", "1h"], "summary",
        {"side": "short", "contracts": 4.0, "entry_price": 1.5, "unrealized_pnl": -2.0},
        {"action": "close", "reasoning": "Gemini exit"},
        {"assessment": "invalidated", "confidence": 0.9, "reasoning": "thesis broken"},
        allowed_actions=("HOLD", "REDUCE_50", "CLOSE_ALL"), review_path="exit_escalation",
    )
    prompt = client.prompts[0]
    assert '"action": "HOLD" | "REDUCE_50" | "CLOSE_ALL"' in prompt
    assert '"ADD_POSITION"' not in prompt
    assert result["action"] == "CLOSE_ALL"
