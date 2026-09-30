import trader
from state import TraderState

SYMBOL = 'XRP/USDT:USDT'

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
    def __init__(self, protection=None):
        self.protection = protection
    def fetch_current_protection(self, *_a, **_k):
        return self.protection

def test_guard_allows_small_loss_when_dual_ai_confirms_invalidated(monkeypatch, tmp_path):
    cfg = Cfg(tmp_path)
    position = {'side':'short','contracts':5.01,'entry_price':1.4953,'mark_price':1.5005}
    monkeypatch.setattr(trader.trade_log, 'last_unclosed_open', lambda *_a, **_k: {'sl_price':1.6075})
    monkeypatch.setattr(trader, '_exit_escalation_multitf_invalidated', lambda *_a, **_k: False)
    out = trader._position_ai_close_guard(
        cfg, SYMBOL, position, {'sl_price':1.6075}, {}, 'invalidated', gpt_confidence=0.90,
    )
    assert out['allow_close_all'] is True
    assert out['reason'] == 'dual_ai_invalidated'
    assert out['loss_r'] < 0.20


def test_general_handler_executes_close_for_dual_ai_invalidated(monkeypatch, tmp_path):
    cfg = Cfg(tmp_path); state = TraderState()
    position = {'side':'short','contracts':5.01,'entry_price':1.4953,'mark_price':1.5005}
    monkeypatch.setattr(trader.gemini_analyzer, 'analyze_held_position', lambda *a, **k: {'assessment':'invalidated','confidence':0.85,'reasoning':'invalid'})
    monkeypatch.setattr(trader.openai_analyzer, 'verify_position_management', lambda *a, **k: {'action':'CLOSE_ALL','confidence':0.90,'reasoning':'close'})
    monkeypatch.setattr(trader.trade_log, 'last_unclosed_open', lambda *_a, **_k: {'sl_price':1.6075})
    monkeypatch.setattr(trader, '_exit_escalation_multitf_invalidated', lambda *_a, **_k: False)
    closes=[]; reduces=[]
    monkeypatch.setattr(trader, '_execute_close', lambda *a, **k: closes.append(k.get('reason')) or True)
    monkeypatch.setattr(trader, '_execute_position_ai_reduce_50', lambda *a, **k: reduces.append(True) or True)
    monkeypatch.setattr(trader.position_ai_log, 'record_event', lambda *a, **k: None)
    trader._handle_position_ai_review(
        cfg, state, Client({'sl_price':1.6075}), SYMBOL, ['5m','1h'], 'summary', position,
        {'action':'hold','confidence':0.8}, {'5m':object(),'1h':object()}, review_path='general',
    )
    assert closes == ['position_ai_close_all']
    assert reduces == []
