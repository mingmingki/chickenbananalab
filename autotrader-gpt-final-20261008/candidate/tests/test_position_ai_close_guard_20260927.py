import trader
from state import TraderState

SYMBOL='ETH/USDT:USDT'

class Cfg:
    def __init__(self, user_dir):
        self.user_dir=str(user_dir)
        self.OPENAI_API_KEY='test'
        self.POSITION_AI_REVIEW_ENABLED=True
        self.POSITION_AI_LIVE_EXECUTE=True
        self.POSITION_AI_REVIEW_COOLDOWN_MINUTES=15
        self.MIN_CONFIDENCE=0.6
        self.EXECUTION_MODE='LIVE'
        self.logger=type('L',(),{'info':lambda *a,**k:None,'warning':lambda *a,**k:None,'exception':lambda *a,**k:None})()

class Client:
    def __init__(self, protection=None): self.protection=protection
    def fetch_current_protection(self, *_a, **_k): return self.protection


def test_close_guard_allows_full_close_at_or_beyond_020r(monkeypatch, tmp_path):
    cfg=Cfg(tmp_path)
    position={'side':'long','contracts':2.0,'entry_price':100.0,'mark_price':98.0}
    monkeypatch.setattr(trader.trade_log,'last_unclosed_open',lambda *_a,**_k:{'sl_price':90.0})
    monkeypatch.setattr(trader,'_exit_escalation_multitf_invalidated',lambda *_a,**_k:False)
    out=trader._position_ai_close_guard(cfg,SYMBOL,position,{'sl_price':90.0},{},'weakening')
    assert out['allow_close_all'] is True
    assert out['loss_r'] == 0.2
    assert out['reason'] == 'loss_r_threshold'


def test_close_guard_allows_full_close_on_deterministic_breakdown(monkeypatch, tmp_path):
    cfg=Cfg(tmp_path)
    position={'side':'long','contracts':2.0,'entry_price':100.0,'mark_price':99.5}
    monkeypatch.setattr(trader.trade_log,'last_unclosed_open',lambda *_a,**_k:{'sl_price':90.0})
    monkeypatch.setattr(trader,'_exit_escalation_multitf_invalidated',lambda *_a,**_k:True)
    out=trader._position_ai_close_guard(cfg,SYMBOL,position,{'sl_price':90.0},{'5m':object(),'1h':object()},'invalidated')
    assert out['allow_close_all'] is True
    assert out['breakdown'] is True
    assert out['reason'] == 'deterministic_breakdown'


def test_close_guard_downgrades_small_loss_without_breakdown(monkeypatch, tmp_path):
    cfg=Cfg(tmp_path)
    position={'side':'long','contracts':2.0,'entry_price':100.0,'mark_price':99.0}
    monkeypatch.setattr(trader.trade_log,'last_unclosed_open',lambda *_a,**_k:{'sl_price':90.0})
    monkeypatch.setattr(trader,'_exit_escalation_multitf_invalidated',lambda *_a,**_k:False)
    out=trader._position_ai_close_guard(cfg,SYMBOL,position,{'sl_price':90.0},{},'invalidated')
    assert out['allow_close_all'] is False
    assert out['loss_r'] == 0.1
    assert out['reason'] == 'downgrade_to_reduce'


def test_close_guard_fails_safe_to_full_close_when_risk_inputs_missing(monkeypatch, tmp_path):
    cfg=Cfg(tmp_path)
    position={'side':'long','contracts':2.0,'entry_price':100.0}
    monkeypatch.setattr(trader.trade_log,'last_unclosed_open',lambda *_a,**_k:{})
    monkeypatch.setattr(trader,'_exit_escalation_multitf_invalidated',lambda *_a,**_k:False)
    out=trader._position_ai_close_guard(cfg,SYMBOL,position,None,{},'invalidated')
    assert out['allow_close_all'] is True
    assert out['reason'] == 'risk_inputs_unavailable_fail_safe'


def test_general_gpt_close_all_is_downgraded_to_reduce_when_guard_blocks(monkeypatch, tmp_path):
    cfg=Cfg(tmp_path); state=TraderState()
    position={'side':'long','contracts':2.0,'entry_price':100.0,'mark_price':99.0}
    monkeypatch.setattr(trader.gemini_analyzer,'analyze_held_position',lambda *a,**k:{'assessment':'weakening','confidence':0.9,'reasoning':'weak'})
    monkeypatch.setattr(trader.openai_analyzer,'verify_position_management',lambda *a,**k:{'action':'CLOSE_ALL','confidence':0.9,'reasoning':'close'})
    monkeypatch.setattr(trader.trade_log,'last_unclosed_open',lambda *_a,**_k:{'sl_price':90.0})
    monkeypatch.setattr(trader,'_exit_escalation_multitf_invalidated',lambda *_a,**_k:False)
    monkeypatch.setattr(trader,'_closed_indicator_tail',lambda *_a,**_k:None)
    closes=[]; reduces=[]; events=[]
    monkeypatch.setattr(trader,'_execute_close',lambda *a,**k:closes.append(k.get('reason')) or True)
    monkeypatch.setattr(trader,'_execute_position_ai_reduce_50',lambda *a,**k:reduces.append((a,k)) or True)
    monkeypatch.setattr(trader.position_ai_log,'record_event',lambda *a,**k:events.append((a,k)))
    trader._handle_position_ai_review(cfg,state,Client({'sl_price':90.0}),SYMBOL,['5m','1h'],'summary',position,
        {'action':'hold','confidence':.8},{'5m':object(),'1h':object()},review_path='general')
    assert closes == []
    assert len(reduces) == 1
    assert any(len(a)>=3 and a[1]=='position_ai_close_all_downgraded' for a,_ in events)
