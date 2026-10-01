import datetime as dt
import json
from types import SimpleNamespace as NS

import ai_strategy_review as review
import gemini_analyzer

KST = dt.timezone(dt.timedelta(hours=9))
START = dt.datetime(2026, 10, 1, 0, 0, tzinfo=KST)
END = dt.datetime(2026, 10, 2, 0, 0, tzinfo=KST)


class _Usage:
    prompt_token_count = 100
    candidates_token_count = 20
    thoughts_token_count = 5


class _Response:
    usage_metadata = _Usage()

    def __init__(self, payload):
        self.text = json.dumps(payload)


class _Models:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        return _Response(self.payload)


class _Client:
    def __init__(self, payload):
        self.models = _Models(payload)


def _cfg(tmp_path):
    return NS(
        GEMINI_API_KEY="k",
        GEMINI_MODEL="gemini-test",
        user_dir=str(tmp_path),
        logger=None,
    )


def test_core_primary_decision_uses_response_schema_and_usage_purpose(tmp_path, monkeypatch):
    payload = {
        "action": "hold",
        "confidence": 0.8,
        "market_regime": "bullish",
        "regime_confidence": 0.8,
        "trade_alignment": "neutral",
        "exit_plan": None,
        "reasoning": "상위 추세 유지",
    }
    client = _Client(payload)
    seen = []
    monkeypatch.setattr(gemini_analyzer, "_get_client", lambda _cfg: client)
    monkeypatch.setattr(gemini_analyzer.usage_log, "record_usage", lambda *a, **k: seen.append((a, k)))
    gemini_analyzer.analyze(_cfg(tmp_path), "BTC/USDT:USDT", ["1m", "5m"], "compact market", None)
    call = client.models.calls[0]
    assert hasattr(gemini_analyzer, "GEMINI_DECISION_SCHEMA")
    assert call["config"].response_schema == gemini_analyzer.GEMINI_DECISION_SCHEMA
    assert seen[0][1]["purpose"] == "core_primary_decision"
    assert '"take_profit_2_price"' not in call["contents"]


def test_position_review_uses_schema_without_verbose_json_contract(tmp_path, monkeypatch):
    client = _Client({"assessment": "thesis_intact", "confidence": 0.9, "reasoning": "유효"})
    monkeypatch.setattr(gemini_analyzer, "_get_client", lambda _cfg: client)
    monkeypatch.setattr(gemini_analyzer.usage_log, "record_usage", lambda *a, **k: None)
    position = {"side": "long", "contracts": 1, "entry_price": 100, "unrealized_pnl": 1}
    decision = {"reasoning": "추세 유지", "market_regime": "bullish", "regime_confidence": 0.8}
    gemini_analyzer.analyze_held_position(
        _cfg(tmp_path), "BTC/USDT:USDT", ["1m", "5m"], "compact market", position, decision
    )
    call = client.models.calls[0]
    assert hasattr(gemini_analyzer, "POSITION_REVIEW_SCHEMA")
    assert call["config"].response_schema == gemini_analyzer.POSITION_REVIEW_SCHEMA
    assert '"assessment":' not in call["contents"]


def test_candidate_exit_plan_uses_schema_and_usage_purpose(tmp_path, monkeypatch):
    payload = {
        "confidence": 0.8,
        "exit_plan": {
            "stop_loss_price": 95,
            "take_profit_1_price": 110,
            "take_profit_2_price": 115,
            "confidence": 0.8,
            "reasoning": "지지 저항",
        },
        "reasoning": "가격 계획",
    }
    client = _Client(payload)
    seen = []
    monkeypatch.setattr(gemini_analyzer, "_get_client", lambda _cfg: client)
    monkeypatch.setattr(gemini_analyzer.usage_log, "record_usage", lambda *a, **k: seen.append((a, k)))
    gemini_analyzer.propose_entry_exit_plan(
        _cfg(tmp_path), "DOGE/USDT:USDT", "long", ["5m", "1h"], "compact market", 95, 110
    )
    call = client.models.calls[0]
    assert hasattr(gemini_analyzer, "ENTRY_EXIT_PLAN_SCHEMA")
    assert call["config"].response_schema == gemini_analyzer.ENTRY_EXIT_PLAN_SCHEMA
    assert seen[0][1]["purpose"] == "candidate_exit_plan"


def test_strategy_review_snapshot_is_bounded_but_keeps_decision_evidence(monkeypatch):
    huge_feature = {"blob": "x" * 5000, "market_regime": "bullish", "entry_kind": "core"}
    trades = [
        {
            "trade_id": f"t{i}",
            "exit_time": "2026-10-01T12:00:00+09:00",
            "symbol": "BTC/USDT:USDT",
            "side": "long",
            "net_pnl": float(i - 50),
            "holding_minutes": 15,
            "features": huge_feature,
        }
        for i in range(100)
    ]
    groups = [
        {
            "condition": f"g{i}", "sample_class": "established_sample", "count": 60,
            "win_rate": 55.0, "profit_factor": 1.2, "net_pnl": 10.0, "blob": "y" * 3000,
        }
        for i in range(40)
    ]
    analysis = {"summary": {"completed_trades": 100, "net_pnl": -25.0}, "coverage": {"complete": 80},
                "groups": groups, "trades": trades}
    hypotheses = {f"h{i}": {"sample_count": 50, "metrics": {"profit_factor": 1.1}, "blob": "z" * 3000}
                  for i in range(40)}
    monkeypatch.setattr(review.strategy_learning, "latest_hypotheses", lambda _u: hypotheses)
    self_learning = {
        "state_counts": {"DISCOVERY": 2, "SHADOW_LEARNING": 3},
        "shadow_benefit_net": 12.3,
        "recent_interventions": [{"blob": "q" * 4000}] * 30,
        "recent_counterfactuals": [{"blob": "r" * 4000}] * 30,
        "evidence": {f"e{i}": {"blob": "s" * 3000} for i in range(30)},
    }
    payload = review._compact_payload("/tmp/u", START, END, analysis, self_learning)
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    assert len(encoded.encode("utf-8")) < 30_000
    assert payload["summary"]["completed_trades"] == 100
    assert payload["completed_trades"]
    assert payload["patterns_established"]
    assert payload["self_learning"]["state_counts"]["SHADOW_LEARNING"] == 3
