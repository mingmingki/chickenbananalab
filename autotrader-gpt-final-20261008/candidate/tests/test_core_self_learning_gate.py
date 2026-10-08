from types import SimpleNamespace
import pytest

import trader
import symbol_entry_control


class Logger:
    def __init__(self): self.rows=[]
    def info(self,*a,**k): self.rows.append(('info',a))
    def warning(self,*a,**k): self.rows.append(('warning',a))
    def error(self,*a,**k): self.rows.append(('error',a))
    def exception(self,*a,**k): self.rows.append(('exception',a))


class State:
    def snapshot(self): return {'symbols':{}}


def cfg(tmp_path):
    return SimpleNamespace(user_dir=str(tmp_path), logger=Logger(), GPT_ENTRY_GATE_ENABLED=True, OPENAI_API_KEY='x')


def decision():
    return {'action':'long','confidence':.74,'market_regime':'bullish','regime_confidence':.8,'trade_alignment':'with_regime'}


def call_handler(c, monkeypatch, *, gate=(True,'approved',{'decision':'approve_now','confidence':.76}), paused=False):
    monkeypatch.setattr(symbol_entry_control,'is_paused',lambda *_: paused)
    monkeypatch.setattr(trader,'_reentry_blocked',lambda *_:(False,None))
    monkeypatch.setattr(trader.core_kill_switch,'is_active',lambda *_:False)
    monkeypatch.setattr(trader,'_gpt_entry_gate',lambda *_a,**_k:gate)
    monkeypatch.setattr(trader,'_record_entry_gate_result',lambda *a,**k:None)
    monkeypatch.setattr(trader,'_record_core_entry_attempt',lambda *a,**k:None)
    return trader._handle_new_entry(
        c, State(), object(), 'BTC/USDT:USDT', 'long', decision(), 'd1', 'entry',
        ['5m'], 'summary', None, 100.0, 2.5, 98.0, 104.0,
    )


def test_learning_not_called_before_local_or_gpt_approval(tmp_path,monkeypatch):
    calls=[]
    fake=SimpleNamespace(evaluate_entry=lambda *a,**k:calls.append(1) or {'action':'ALLOW'})
    monkeypatch.setattr(trader,'learning_adapter',fake,raising=False)
    monkeypatch.setattr(trader,'learning_control',SimpleNamespace(get=lambda _:{'live_enabled':True}),raising=False)
    monkeypatch.setattr(trader,'learning_shadow',SimpleNamespace(record_decision=lambda *a,**k:None),raising=False)
    monkeypatch.setattr(trader,'_execute_approved_entry_with_optional_reversal',lambda *a,**k:True)
    c=cfg(tmp_path)
    call_handler(c,monkeypatch,paused=True)
    assert calls==[]
    for gate in [(False,'blocked_wait',{'decision':'wait','confidence':.7}),
                 (False,'blocked_reject',{'decision':'reject','confidence':.8}),
                 (False,'blocked_error',{'error_reason':'timeout'})]:
        call_handler(c,monkeypatch,gate=gate)
    assert calls==[]


def test_negative_learning_is_observed_without_overriding_core_consensus(tmp_path,monkeypatch):
    calls=[]; executed=[]; records=[]
    fake=SimpleNamespace(evaluate_entry=lambda *a,**k:calls.append((a,k)) or {
        'action':'HOLD_BY_LEARNING','confidence_delta':-.08,'matched_patterns':[{'pattern_id':'p1'}],
        'reason':'negative_live_pattern','live_applied':True,'shadow_action':'HOLD_BY_LEARNING','shadow_confidence_delta':-.08})
    monkeypatch.setattr(trader,'learning_adapter',fake,raising=False)
    monkeypatch.setattr(trader,'learning_control',SimpleNamespace(get=lambda _:{'live_enabled':True}),raising=False)
    monkeypatch.setattr(trader,'learning_shadow',SimpleNamespace(record_decision=lambda _u,r:records.append(r)),raising=False)
    monkeypatch.setattr(trader,'_execute_approved_entry_with_optional_reversal',lambda *a,**k:executed.append((a,k)) or True)
    call_handler(cfg(tmp_path),monkeypatch)
    assert len(calls)==1
    assert len(executed)==1
    assert calls[0][0][-1] is False
    assert records[-1]['learner_action']=='HOLD_BY_LEARNING'
    assert records[-1]['live_applied'] is False
    assert records[-1]['baseline_order_executed'] is True


def test_positive_learning_never_changes_order_parameters(tmp_path,monkeypatch):
    executed=[]; records=[]
    # Isolate learning authority from the independent Entry Risk size reducer.
    monkeypatch.setattr(trader,'_core_entry_risk_adjustment',lambda **k:{
        'score':0,'factors':[],'blocked':False,'size_fraction':1.0,
        'require_strong_confirmation':False,'amount':float(k['amount'])})
    fake=SimpleNamespace(evaluate_entry=lambda *a,**k:{
        'action':'ALLOW','confidence_delta':.10,'matched_patterns':[{'pattern_id':'p'}],
        'reason':'matched_live_patterns','live_applied':True,'shadow_action':'ALLOW','shadow_confidence_delta':.10})
    monkeypatch.setattr(trader,'learning_adapter',fake,raising=False)
    monkeypatch.setattr(trader,'learning_control',SimpleNamespace(get=lambda _:{'live_enabled':True}),raising=False)
    monkeypatch.setattr(trader,'learning_shadow',SimpleNamespace(record_decision=lambda _u,r:records.append(r)),raising=False)
    monkeypatch.setattr(trader,'_execute_approved_entry_with_optional_reversal',lambda *a,**k:executed.append((a,k)) or True)
    call_handler(cfg(tmp_path),monkeypatch)
    assert len(executed)==1
    a,k=executed[0]
    assert a[4]=='long' and a[5]==2.5 and a[6]==100.0 and a[7]==98.0 and a[8]==104.0
    assert records[-1]['baseline_order_executed'] is True


def test_adapter_exception_fails_open_to_original_order(tmp_path,monkeypatch):
    executed=[]
    def boom(*a,**k): raise RuntimeError('learner down')
    monkeypatch.setattr(trader,'learning_adapter',SimpleNamespace(evaluate_entry=boom),raising=False)
    monkeypatch.setattr(trader,'learning_control',SimpleNamespace(get=lambda _:{'live_enabled':True}),raising=False)
    monkeypatch.setattr(trader,'learning_shadow',SimpleNamespace(record_decision=lambda *a,**k:None),raising=False)
    monkeypatch.setattr(trader,'_execute_approved_entry_with_optional_reversal',lambda *a,**k:executed.append((a,k)) or True)
    c=cfg(tmp_path)
    call_handler(c,monkeypatch)
    assert len(executed)==1
    assert any('LEARNING_ERROR' in str(row) for row in c.logger.rows)


def test_live_off_passed_to_adapter_and_baseline_executes(tmp_path,monkeypatch):
    flags=[]; executed=[]
    def evaluate(_u,_c,_f,enabled):
        flags.append(enabled); return {'action':'ALLOW','confidence_delta':0,'matched_patterns':[], 'reason':'shadow_only','live_applied':False,'shadow_action':'HOLD_BY_LEARNING','shadow_confidence_delta':-.05}
    monkeypatch.setattr(trader,'learning_adapter',SimpleNamespace(evaluate_entry=evaluate),raising=False)
    monkeypatch.setattr(trader,'learning_control',SimpleNamespace(get=lambda _:{'live_enabled':False}),raising=False)
    monkeypatch.setattr(trader,'learning_shadow',SimpleNamespace(record_decision=lambda *a,**k:None),raising=False)
    monkeypatch.setattr(trader,'_execute_approved_entry_with_optional_reversal',lambda *a,**k:executed.append(1) or True)
    call_handler(cfg(tmp_path),monkeypatch)
    assert flags==[False] and executed==[1]
