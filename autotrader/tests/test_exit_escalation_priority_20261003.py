import trader
from state import TraderState

SYMBOL = 'ETH/USDT:USDT'


class Cfg:
    def __init__(self, user_dir):
        self.user_dir = str(user_dir)
        self.OPENAI_API_KEY = 'test'
        self.POSITION_AI_REVIEW_ENABLED = True
        self.POSITION_AI_LIVE_EXECUTE = True
        self.POSITION_AI_REVIEW_COOLDOWN_MINUTES = 15
        self.MIN_CONFIDENCE = 0.6
        self.EXECUTION_MODE = 'LIVE'
        self.logger = type('L', (), {
            'info': lambda *a, **k: None,
            'warning': lambda *a, **k: None,
            'exception': lambda *a, **k: None,
        })()


class Client:
    def fetch_current_protection(self, *_a, **_k):
        return {'sl_price': 90.0}


def test_exit_close_plus_invalidated_cannot_be_downgraded_to_reduce(monkeypatch, tmp_path):
    cfg = Cfg(tmp_path)
    state = TraderState()
    position = {'side': 'long', 'contracts': 3.0, 'entry_price': 100.0, 'mark_price': 99.0}
    monkeypatch.setattr(
        trader.gemini_analyzer, 'analyze_held_position',
        lambda *a, **k: {'assessment': 'invalidated', 'confidence': 0.85, 'reasoning': 'thesis broken'},
    )
    monkeypatch.setattr(
        trader.openai_analyzer, 'verify_position_management',
        lambda *a, **k: {'action': 'REDUCE_50', 'confidence': 0.90, 'reasoning': 'keep HTF exposure'},
    )
    monkeypatch.setattr(trader, '_exit_escalation_multitf_invalidated', lambda *_a, **_k: False)
    monkeypatch.setattr(trader, '_closed_indicator_tail', lambda *_a, **_k: [])
    closes, reduces = [], []
    monkeypatch.setattr(trader, '_execute_close', lambda *a, **k: closes.append(k.get('reason')) or True)
    monkeypatch.setattr(trader, '_execute_position_ai_reduce_50', lambda *a, **k: reduces.append(True) or True)
    monkeypatch.setattr(trader.position_ai_log, 'record_event', lambda *a, **k: None)

    trader._handle_position_ai_review(
        cfg, state, Client(), SYMBOL, ['5m', '1h', '4h'], 'summary', position,
        {'action': 'close', 'confidence': 0.75}, {'5m': object(), '1h': object(), '4h': object()},
        review_path='exit_escalation',
    )

    assert closes == ['position_ai_close_all']
    assert reduces == []
