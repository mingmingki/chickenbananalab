"""Entry-only observability shared with the durable, asynchronous CORE outbox."""
from contextvars import ContextVar
from functools import wraps
import logging
import usage_log
import core_entry_events

_candidate_evidence=ContextVar('candidate_entry_evidence',default=None)


def add_candidate_evidence(**fields):
    current=_candidate_evidence.get()
    if current is not None:
        current.update(fields)


def record_candidate_block(cfg,intent,result,snapshot=None):
    """A real EntryIntent is required. NoAction, ordinary holds and Shadow are silent."""
    try:
        if not getattr(cfg,'CANDIDATE_C_LIVE_EXECUTE',False): return
        get=(intent.get if isinstance(intent,dict) else lambda k,d=None:getattr(intent,k,d))
        kind=get('kind') or get('intent_kind')
        attempted_setup=(kind=='NoAction' and get('setup_id') and get('side') in ('long','short')
            and (get('reason_code') or result.get('reason_code')) not in ('no_setup','hold'))
        if kind!='EntryIntent' and not get('entry_attempt') and not attempted_setup: return
        if result.get('executed'): return
        reason=result.get('reason') or result.get('gate_result') or result.get('reason_code') or 'entry_not_submitted'
        if reason=='existing_durable_intent': return  # same persisted execution is being reconciled
        setup=get('setup_id') or get('idempotency_key')
        stamp=get('decision_timestamp')
        if not setup or not stamp: return
        evidence=dict(get('entry_validation_values') or {},**(_candidate_evidence.get() or {}))
        gate=evidence.pop('gate',{})
        snap=snapshot or {}
        row=dict(engine='Candidate C',decision_id=f"C:{setup}:{stamp}",setup_id=setup,
            symbol=get('symbol') or result.get('symbol'),side=get('side'),
            status='ORDER_PENDING' if result.get('pending') else 'LOCAL_BLOCKED',reason=reason,
            gemini_action='deterministic_entry',gemini_confidence=None,
            gpt_raw_result=gate.get('gate_result') or result.get('gate_result'),
            gpt_confidence=gate.get('gpt_confidence'),gpt_reason=gate.get('gpt_reasoning'),
            gate_processing=gate.get('gate_result') or 'NOT_REQUESTED',
            entry_price=evidence.get('entry_price') or snap.get('close'),
            sl_price=evidence.get('sl_price',get('raw_stop_price')),
            tp_price=evidence.get('tp_price',get('raw_target_price')),
            quantity_coin=evidence.get('quantity_coin'),contracts=evidence.get('contracts'),
            leverage=getattr(cfg,'CANDIDATE_C_LEVERAGE',None),
            configured_margin_usdt=getattr(cfg,'CANDIDATE_C_FIXED_MARGIN_USDT',None),
            margin_estimate_usdt=evidence.get('margin_estimate_usdt'),
            sizing_reduction_reason=evidence.get('sizing_reduction_reason'),
            validation_values=dict(evidence,entry_size_fraction=get('entry_size_fraction',1),
                requested_risk_pct=get('requested_risk_pct'),decision_timestamp=stamp,
                source_candle_close_timestamp=get('source_candle_close_timestamp'),
                error_reason=result.get('error_reason')))
        core_entry_events.record(cfg,row)
        core_entry_events.kick(cfg)
    except Exception as exc:
        (getattr(cfg,'logger',None) or logging.getLogger(__name__)).warning(
            'C_ENTRY_NOTIFICATION_FAILED error_type=%s',type(exc).__name__)


def observe_candidate_execution(function):
    @wraps(function)
    def wrapped(cfg,client,intent,*args,**kwargs):
        token=_candidate_evidence.set({})
        try:
            with usage_log.call_context(engine='Candidate C',trigger=getattr(intent,'reason_code',None),stage='entry_gate'):
                result=function(cfg,client,intent,*args,**kwargs)
            if getattr(intent,'kind',None)=='EntryIntent':
                evidence=dict(_candidate_evidence.get() or {})
                result=dict(result,execution_evidence=evidence)
            record_candidate_block(cfg,intent,result,kwargs.get('snapshot'))
            return result
        finally:
            _candidate_evidence.reset(token)
    return wrapped
