import trade_learning_features as features


def test_feature_join_uses_successful_entry_gate_written_just_after_trade_open():
    lifecycle = {
        "trade_id": "t1",
        "symbol": "BTC/USDT:USDT",
        "entry_time": "2026-10-01T10:00:00",
        "events": [{"type": "open", "market_regime": "bullish", "trade_alignment": "with_regime"}],
    }
    sources = {
        "market": [], "candle": [], "posai": [], "lowfollow_by_trade": {}, "daily_by_trade": {},
        "gpt": [
            {"symbol": "BTC/USDT:USDT", "time": "2026-10-01T09:55:00", "mode": "entry_gate",
             "event_type": "entry", "order_success": False, "gpt_decision": "wait", "gpt_confidence": 0.61},
            {"symbol": "BTC/USDT:USDT", "time": "2026-10-01T10:00:02", "mode": "entry_gate",
             "event_type": "entry", "order_success": True, "gpt_decision": "approve_now", "gpt_confidence": 0.83},
        ],
    }

    out = features.enrich_lifecycle("/unused", lifecycle, sources=sources)

    assert out["features"]["gpt_decision"] == "approve_now"
    assert out["features"]["gpt_confidence"] == 0.83
