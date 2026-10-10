"""CORE entry GPT policy. An exception never becomes an AI approval."""
import math

from config import CORE_SYMBOLS

CORE = frozenset(CORE_SYMBOLS)


def valid_candidate(cfg, symbol, decision):
    confidence = decision.get('confidence')
    return (symbol in CORE and decision.get('action') in ('long','short')
            and not decision.get('error_reason')
            and not isinstance(confidence, bool)
            and isinstance(confidence, (int,float)) and math.isfinite(confidence)
            and getattr(cfg,'MIN_CONFIDENCE',.6) <= confidence <= 1)


def confirmed_entry_timeout(result):
    return (isinstance(result,dict) and result.get('decision') == 'TIMEOUT'
            and result.get('error_reason') == 'timeout'
            and result.get('error_type') == 'APITimeoutError'
            and result.get('timeout_confirmed') is True
            and result.get('request_purpose') == 'entry_gate')


def confirmed_empty_entry_response(result):
    """GPT returned no content; not a reject, malformed JSON or transport failure."""
    return (isinstance(result, dict) and result.get('decision') is None
            and result.get('error_reason') == 'empty_response'
            and result.get('error_type') == 'EmptyResponse'
            and result.get('request_purpose') == 'entry_gate'
            and result.get('timeout_confirmed') is False)


def evaluate(cfg, symbol, decision, result):
    if not valid_candidate(cfg,symbol,decision):
        return False,'blocked_error','invalid_gemini_entry_candidate'
    if not isinstance(result,dict):
        return False,'blocked_error','missing_gpt_result'
    if confirmed_entry_timeout(result):
        if getattr(cfg,'CORE_GPT_ENTRY_TIMEOUT_BYPASS',False):
            return True,'TIMEOUT_BYPASS','confirmed_core_gpt_entry_timeout_config_enabled'
        return False,'blocked_error','core_timeout_bypass_disabled'
    if confirmed_empty_entry_response(result):
        if getattr(cfg,'CORE_GPT_ENTRY_TIMEOUT_BYPASS',False):
            return True,'NO_RESPONSE_BYPASS','confirmed_empty_gpt_entry_response_gemini_fallback'
        return False,'blocked_error','no_response_bypass_disabled'
    if result.get('error_reason') or result.get('decision') == 'TIMEOUT':
        return False,'blocked_error',result.get('error_reason') or 'unconfirmed_timeout'
    verdict=result.get('decision')
    if verdict == 'approve_now':
        confidence=result.get('confidence')
        if (isinstance(confidence,bool) or not isinstance(confidence,(int,float))
                or not math.isfinite(confidence) or not 0<=confidence<=1):
            return False,'blocked_error','invalid_gpt_confidence'
        return True,'approved','gpt_approve_now'
    if verdict in ('wait','reject'):
        return False,'blocked_'+verdict,'gpt_'+verdict
    return False,'blocked_error','invalid_gpt_decision'


def gate_status(gate):
    return {'approved':'GPT_APPROVED','TIMEOUT_BYPASS':'TIMEOUT_BYPASS','NO_RESPONSE_BYPASS':'NO_RESPONSE_BYPASS',
            'blocked_wait':'GPT_WAIT','blocked_reject':'GPT_REJECT',
            'blocked_error':'GPT_ERROR'}.get(gate,'GPT_ERROR')
