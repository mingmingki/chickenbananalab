import logging
from types import SimpleNamespace
from unittest.mock import Mock
import httpx
import openai
import pytest
import trader
import openai_analyzer
import config

SYMBOL='ETH/USDT:USDT'

def cfg(tmp_path):
    c=config.UserConfig(str(tmp_path))
    c.logger=logging.getLogger('offline-test')
    c.CORE_GPT_ENTRY_TIMEOUT_BYPASS=True
    c.GPT_ENTRY_GATE_ENABLED=True
    c.OPENAI_API_KEY='offline-placeholder'
    return c


def timeout():
    return dict(decision='TIMEOUT',confidence=None,reasoning='',error_reason='timeout',
        error_type='APITimeoutError',timeout_confirmed=True,request_purpose='entry_gate')

@pytest.mark.parametrize('side',['long','short'])
def test_confirmed_entry_timeout_bypasses_without_forged_approval(tmp_path,monkeypatch,side):
    original=timeout()
    monkeypatch.setattr(openai_analyzer,'verify',lambda *a,**k:dict(original))
    allowed,status,result=trader._gpt_entry_gate(cfg(tmp_path),SYMBOL,[], '',None,{'action':side,'confidence':.8})
    assert allowed and status=='TIMEOUT_BYPASS'
    assert result['decision']=='TIMEOUT'
    assert result['error_reason']=='timeout'
    assert 'exit_plan' not in result

@pytest.mark.parametrize('reason,error_type',[('timeout','APIConnectionError'),('api_error','APITimeoutError'),('rate_limit','RateLimitError'),('authentication_error','AuthenticationError'),('parse_error','JSONDecodeError'),('empty_response',None)])
def test_non_timeout_and_unproven_timeout_cannot_bypass(tmp_path,monkeypatch,reason,error_type):
    raw=dict(timeout(),error_reason=reason,error_type=error_type)
    monkeypatch.setattr(openai_analyzer,'verify',lambda *a,**k:raw)
    allowed,status,_=trader._gpt_entry_gate(cfg(tmp_path),SYMBOL,[], '',None,{'action':'short','confidence':.8})
    assert not allowed and status=='blocked_error'

@pytest.mark.parametrize('side',['hold','close',None])
def test_no_candidate_cannot_be_created_by_timeout(tmp_path,monkeypatch,side):
    monkeypatch.setattr(openai_analyzer,'verify',lambda *a,**k:timeout())
    allowed,_,_=trader._gpt_entry_gate(cfg(tmp_path),SYMBOL,[], '',None,{'action':side,'confidence':.8})
    assert not allowed

@pytest.mark.parametrize('symbol',['DOGE/USDT:USDT','SOL/USDT:USDT'])
def test_timeout_policy_does_not_apply_to_candidate_c(tmp_path,monkeypatch,symbol):
    monkeypatch.setattr(openai_analyzer,'verify',lambda *a,**k:timeout())
    allowed,status,_=trader._gpt_entry_gate(cfg(tmp_path),symbol,[], '',None,{'action':'short','confidence':.8})
    assert not allowed and status=='blocked_error'

@pytest.mark.parametrize('verdict',['wait','reject'])
def test_wait_reject_still_block(tmp_path,monkeypatch,verdict):
    monkeypatch.setattr(openai_analyzer,'verify',lambda *a,**k:dict(decision=verdict,confidence=.9,error_reason=None))
    allowed,status,_=trader._gpt_entry_gate(cfg(tmp_path),SYMBOL,[], '',None,{'action':'long','confidence':.8})
    assert not allowed and status=='blocked_'+verdict

@pytest.mark.parametrize('fault',['no_marker','wrong_purpose','missing_confidence','gemini_error'])
def test_invalid_timeout_candidate_still_blocks(tmp_path,monkeypatch,fault):
    raw=timeout()
    decision={'action':'long','confidence':.8}
    if fault=='no_marker': raw.pop('timeout_confirmed')
    if fault=='wrong_purpose': raw['request_purpose']='position_management'
    if fault=='missing_confidence': decision['confidence']=None
    if fault=='gemini_error': decision['error_reason']='timeout'
    monkeypatch.setattr(openai_analyzer,'verify',lambda *a,**k:raw)
    allowed,_,_=trader._gpt_entry_gate(cfg(tmp_path),SYMBOL,[], '',None,decision)
    assert not allowed


def test_sdk_timeout_retains_typed_original_provenance(tmp_path,monkeypatch):
    request=httpx.Request('POST','https://example.invalid')
    client=Mock()
    client.with_options.return_value=client
    client.chat.completions.create.side_effect=openai.APITimeoutError(request=request)
    monkeypatch.setattr(openai_analyzer,'_get_client',lambda c:client)
    monkeypatch.setattr(openai_analyzer,'_ensure_response_models_warmed_up',lambda:None)
    result=openai_analyzer.verify(cfg(tmp_path),SYMBOL,[], '',None,{'action':'short','confidence':.8},purpose='entry_gate',timeout=25,max_retries=0)
    assert result['decision']=='TIMEOUT'
    assert result['error_type']=='APITimeoutError'
    assert result['timeout_confirmed'] is True
    assert result['request_purpose']=='entry_gate'


def test_paid_observation_shadow_off_by_default(tmp_path,monkeypatch):
    c=cfg(tmp_path)
    calls=[]
    class Thread:
        def __init__(self,*a,**k): calls.append(k)
        def start(self): pass
    monkeypatch.setattr(trader.threading,'Thread',Thread)
    trader._fire_shadow_verification_async(c,SYMBOL,[], '',None,{'action':'long'},'test-id','entry')
    assert not calls
    c.CORE_PAID_SHADOW_ENABLED=True
    trader._fire_shadow_verification_async(c,SYMBOL,[], '',None,{'action':'long'},'new-id','entry')
    assert len(calls)==1


@pytest.mark.parametrize('kind',['auth','billing','rate','connection','server','generic_timeout_text'])
def test_sdk_non_timeout_errors_keep_type_and_never_admit(tmp_path,monkeypatch,kind):
    request=httpx.Request('POST','https://example.invalid')
    response=httpx.Response(429 if kind in ('billing','rate') else 500,request=request)
    errors={
        'auth':openai.AuthenticationError('offline',response=response,body={}),
        'billing':openai.RateLimitError('offline',response=response,body={'code':'insufficient_quota'}),
        'rate':openai.RateLimitError('offline',response=response,body={}),
        'connection':openai.APIConnectionError(request=request),
        'server':openai.InternalServerError('offline',response=response,body={}),
        'generic_timeout_text':RuntimeError('timeout APITimeoutError'),
    }
    client=Mock();client.with_options.return_value=client
    client.chat.completions.create.side_effect=errors[kind]
    monkeypatch.setattr(openai_analyzer,'_get_client',lambda c:client)
    monkeypatch.setattr(openai_analyzer,'_ensure_response_models_warmed_up',lambda:None)
    c=cfg(tmp_path);decision={'action':'short','confidence':.8}
    allowed,gate,raw=trader._gpt_entry_gate(c,SYMBOL,[],'',None,decision)
    assert not allowed and gate=='blocked_error'
    assert raw['decision'] is None and raw['timeout_confirmed'] is False
    assert raw['error_type']==type(errors[kind]).__name__
    if kind=='billing': assert raw['error_category']=='billing_error'


@pytest.mark.parametrize('text',['','not json','[]','null','{"decision":"approve_now"}'])
def test_empty_or_malformed_responses_never_admit(tmp_path,monkeypatch,text):
    client=Mock();client.with_options.return_value=client
    client.chat.completions.create.return_value=SimpleNamespace(usage=None,
        choices=[SimpleNamespace(message=SimpleNamespace(content=text))])
    monkeypatch.setattr(openai_analyzer,'_get_client',lambda c:client)
    monkeypatch.setattr(openai_analyzer,'_ensure_response_models_warmed_up',lambda:None)
    allowed,gate,raw=trader._gpt_entry_gate(cfg(tmp_path),SYMBOL,[],'',None,{'action':'long','confidence':.8})
    if text == '':
        assert allowed and gate=='NO_RESPONSE_BYPASS'
        assert raw['error_type']=='EmptyResponse' and raw['request_purpose']=='entry_gate'
    else:
        assert not allowed and gate=='blocked_error'


def test_typed_timeout_from_held_verification_is_never_entry_bypass(tmp_path,monkeypatch):
    client=Mock();client.with_options.return_value=client
    client.chat.completions.create.side_effect=openai.APITimeoutError(request=httpx.Request('POST','https://example.invalid'))
    monkeypatch.setattr(openai_analyzer,'_get_client',lambda c:client)
    monkeypatch.setattr(openai_analyzer,'_ensure_response_models_warmed_up',lambda:None)
    c=cfg(tmp_path)
    position={'side':'long','contracts':1,'entry_price':100,'unrealized_pnl':0}
    raw=openai_analyzer.verify(c,SYMBOL,[],'',position,{'action':'short','confidence':.8},purpose='position_management')
    assert raw['decision'] is None and raw['request_purpose']=='position_management'
    import core_entry_policy
    assert not core_entry_policy.evaluate(c,SYMBOL,{'action':'short','confidence':.8},raw)[0]
