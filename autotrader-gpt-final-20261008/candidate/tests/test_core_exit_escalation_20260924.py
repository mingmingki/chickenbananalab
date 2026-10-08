import datetime

import trader
from state import TraderState


SYMBOL = "XRP/USDT:USDT"


def test_signal_close_escalation_bypasses_regular_position_review_cooldown():
    state = TraderState()
    state.update_symbol(
        SYMBOL,
        position_ai_review_block_until=datetime.datetime.now() + datetime.timedelta(minutes=14),
    )

    assert trader._position_ai_review_cooldown_blocked_for_trigger(state, SYMBOL, "hold") is True
    assert trader._position_ai_review_cooldown_blocked_for_trigger(state, SYMBOL, "signal_close") is False
    assert trader._position_ai_review_cooldown_blocked_for_trigger(state, SYMBOL, "reversal_close") is False


def test_general_stage1_reduce_uses_confirmed_5m_after_ai_double_approval_without_requiring_1h(monkeypatch):
    rows_5m = [
        {"ts": "2026-09-24T05:00:00", "macd": 0.30, "rsi_14": 47.0, "close": 100.0, "ema_20": 100.5, "ema_50": 101.0},
        {"ts": "2026-09-24T05:05:00", "macd": 0.10, "rsi_14": 44.0, "close": 99.7, "ema_20": 100.4, "ema_50": 101.0},
    ]

    def closed_tail(_raw, tf, n):
        if tf == "5m":
            return rows_5m[-n:]
        if tf == "1h":
            return None
        raise AssertionError(tf)

    monkeypatch.setattr(trader, "_closed_indicator_tail", closed_tail)

    met, bar_ts = trader._reduce_v2_stage1_conditions_met(
        "long", 0.86, "weakening", {"5m": object()}
    )

    assert met is True
    assert bar_ts == rows_5m[-1]["ts"]


def test_general_stage1_reduce_accepts_invalidated_thesis_when_gpt_still_approves_reduce(monkeypatch):
    rows_5m = [
        {"ts": "2026-09-24T05:00:00", "macd": -0.10, "rsi_14": 55.0, "close": 100.2, "ema_20": 100.0, "ema_50": 99.5},
        {"ts": "2026-09-24T05:05:00", "macd": 0.10, "rsi_14": 58.0, "close": 100.5, "ema_20": 100.1, "ema_50": 99.5},
    ]

    # For a short, rising MACD / RSI>50 are adverse to the position.
    monkeypatch.setattr(
        trader,
        "_closed_indicator_tail",
        lambda _raw, tf, n: rows_5m[-n:] if tf == "5m" else None,
    )

    met, _ = trader._reduce_v2_stage1_conditions_met(
        "short", 0.90, "invalidated", {"5m": object()}
    )
    assert met is True


def test_exit_escalation_dispatches_position_review_despite_regular_cooldown(monkeypatch, tmp_path):
    class Cfg:
        user_dir = str(tmp_path)
        OPENAI_API_KEY = "test"
        POSITION_AI_REVIEW_ENABLED = True
        MIN_HOLD_MINUTES = 15

    state = TraderState()
    state.update_symbol(
        SYMBOL,
        entry_time=datetime.datetime.now() - datetime.timedelta(minutes=30),
        position_ai_review_block_until=datetime.datetime.now() + datetime.timedelta(minutes=14),
    )
    calls = []
    monkeypatch.setattr(
        trader,
        "_handle_position_ai_review",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    position = {"side": "short", "contracts": 4.0, "entry_price": 1.5}

    dispatched = trader._maybe_escalate_position_ai_exit(
        Cfg(), state, object(), SYMBOL, ["5m", "1h"], "summary",
        position, {"action": "close", "confidence": 0.8}, {}, "signal_close",
    )

    assert dispatched is True
    assert len(calls) == 1
    assert calls[0][1]["review_path"] == "exit_escalation"


def _risk_df(rows):
    import pandas as pd
    return pd.DataFrame(rows)


def test_hard_loss_close_requires_075r_confirmed_5m_and_live_1h_breakdown():
    raw = {
        "5m": _risk_df([
            {"timestamp": datetime.datetime(2026,9,25,2,40), "close": 99.0, "ema_20":100.0, "ema_50":100.5, "macd":-0.2},
            {"timestamp": datetime.datetime(2026,9,25,2,45), "close": 98.5, "ema_20":99.8, "ema_50":100.4, "macd":-0.4},
        ]),
        "1h": _risk_df([
            {"timestamp": datetime.datetime(2026,9,25,2,0), "close":98.0, "ema_20":99.0, "ema_50":100.0, "macd":-0.3},
        ]),
    }
    result = trader._hard_loss_close_diagnostics("long", raw, 98.5, 100.0, 98.0)
    assert result["triggered"] is True
    assert result["loss_r"] == 0.75
    assert result["one_h_reverse_aligned"] is True
    assert result["confirmed_5m_adverse"] is True


def test_hard_loss_close_does_not_fire_when_1h_trend_still_intact_like_xrp():
    raw = {
        "5m": _risk_df([
            {"timestamp": datetime.datetime(2026,9,25,2,45), "close":1.532, "ema_20":1.542, "ema_50":1.541, "macd":-0.0013},
        ]),
        "1h": _risk_df([
            {"timestamp": datetime.datetime(2026,9,25,2,0), "close":1.528, "ema_20":1.522, "ema_50":1.520, "macd":0.0067},
        ]),
    }
    result = trader._hard_loss_close_diagnostics("long", raw, 1.53, 1.5508, 1.5194)
    assert result["loss_r"] >= 0.6
    assert result["one_h_reverse_aligned"] is False
    assert result["triggered"] is False


def test_hard_loss_close_is_symmetric_for_short():
    raw = {
        "5m": _risk_df([{"timestamp": datetime.datetime(2026,9,25,2,45), "close":102.0, "ema_20":101.0, "ema_50":100.5, "macd":0.4}]),
        "1h": _risk_df([{"timestamp": datetime.datetime(2026,9,25,2,0), "close":102.0, "ema_20":101.0, "ema_50":100.0, "macd":0.3}]),
    }
    result = trader._hard_loss_close_diagnostics("short", raw, 101.5, 100.0, 102.0)
    assert result["triggered"] is True


def test_exit_escalation_invalidated_with_multitf_breakdown_bypasses_gpt(monkeypatch, tmp_path):
    class Cfg:
        user_dir = str(tmp_path)
        OPENAI_API_KEY = 'test'
        POSITION_AI_REVIEW_ENABLED = True
        POSITION_AI_LIVE_EXECUTE = True
        MIN_CONFIDENCE = 0.6
        POSITION_AI_REVIEW_COOLDOWN_MINUTES = 15
        EXECUTION_MODE = 'LIVE'
        logger = type('L', (), {'info':lambda *a,**k:None,'warning':lambda *a,**k:None,'exception':lambda *a,**k:None})()
    state=TraderState()
    position={'side':'long','contracts':2.0,'entry_price':100.0}
    monkeypatch.setattr(trader.gemini_analyzer,'analyze_held_position',lambda *a,**k:{'assessment':'invalidated','confidence':0.9,'reasoning':'broken'})
    gpt_calls=[]
    monkeypatch.setattr(trader.openai_analyzer,'verify_position_management',lambda *a,**k:gpt_calls.append(1) or {'action':'HOLD','confidence':.9})
    monkeypatch.setattr(trader,'_exit_escalation_multitf_invalidated',lambda *a,**k:True)
    closes=[]
    monkeypatch.setattr(trader,'_execute_close',lambda *a,**k:closes.append(k.get('reason')) or True)
    monkeypatch.setattr(trader.position_ai_log,'record_event',lambda *a,**k:None)
    monkeypatch.setattr(trader.OkxClient,'fetch_current_protection',lambda *a,**k:None, raising=False)
    trader._handle_position_ai_review(Cfg(),state,object(),SYMBOL,['5m','1h'],'summary',position,
        {'action':'close','confidence':.8},{'5m':object(),'1h':object()},review_path='exit_escalation')
    assert closes == ['deterministic_exit_escalation']
    assert gpt_calls == []


def test_low_confidence_exit_escalation_without_multitf_breakdown_keeps_gpt_authority(monkeypatch, tmp_path):
    class Cfg:
        user_dir = str(tmp_path)
        OPENAI_API_KEY = 'test'
        POSITION_AI_REVIEW_ENABLED = True
        POSITION_AI_LIVE_EXECUTE = True
        MIN_CONFIDENCE = 0.6
        POSITION_AI_REVIEW_COOLDOWN_MINUTES = 15
        EXECUTION_MODE = 'LIVE'
        logger = type('L', (), {'info':lambda *a,**k:None,'warning':lambda *a,**k:None,'exception':lambda *a,**k:None})()
    state=TraderState(); position={'side':'long','contracts':2.0,'entry_price':100.0}
    monkeypatch.setattr(trader.gemini_analyzer,'analyze_held_position',lambda *a,**k:{'assessment':'invalidated','confidence':0.75,'reasoning':'broken'})
    monkeypatch.setattr(trader,'_exit_escalation_multitf_invalidated',lambda *a,**k:False)
    gpt_calls=[]
    monkeypatch.setattr(trader.openai_analyzer,'verify_position_management',lambda *a,**k:gpt_calls.append(1) or {'action':'HOLD','confidence':.9,'reasoning':'wait'})
    monkeypatch.setattr(trader.position_ai_log,'record_event',lambda *a,**k:None)
    trader._handle_position_ai_review(Cfg(),state,object(),SYMBOL,['5m','1h'],'summary',position,
        {'action':'close','confidence':.8},{'5m':object(),'1h':object()},review_path='exit_escalation')
    assert gpt_calls == [1]


def test_multitf_breakdown_accepts_btc_like_5m_macd_still_positive_but_falling_fast():
    one_h=_risk_df([{'close':84257.3,'ema_20':84280.0,'ema_50':84462.3,'macd':-4.27}])
    five_m=_risk_df([
        {'close':84411.0,'ema_20':84603.9,'ema_50':84520.1,'macd':33.69},
        {'close':84450.2,'ema_20':84589.3,'ema_50':84517.4,'macd':17.64},
    ])
    result=trader._multitf_breakdown_from_indicator_frames('long',one_h,five_m)
    assert result['one_h_reverse_aligned'] is True
    assert result['confirmed_5m_adverse'] is True
    assert result['invalidated'] is True
