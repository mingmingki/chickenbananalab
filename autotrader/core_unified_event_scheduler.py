"""Event review orchestration; owns no exchange/order implementation."""
from core_unified_events import detect_events
from core_unified_market import digest
from core_unified_policy import POLICY,number


def event_current(event,snapshot):
    rows=snapshot['frames']['1m'];p=number(rows[-1]['close']);level=number(event['threshold'])
    sign=1 if event['direction']=='long' else -1
    if event['kind']=='direction_change':
        return all(sign*(number(b['close'])-number(a['close']))>0 for a,b in zip(rows[-3:-1],rows[-2:]))
    return sign*(p-level)>=0

class EventScheduler:
    def __init__(self,coordinator,memory):
        self.c=coordinator;self.memory=memory;self.approvals=[];self.telemetry={}
    def invalidate(self,generation):
        self.approvals=[];self.memory.invalidate(self.c.symbol,generation)
    def tick(self,snapshot,paused=False):
        c=self.c;now=c.clock();state=c.controller.status().get('state')
        held=bool(state and state.get('phase')!='FLAT' and number(state.get('filled_qty',0))>0)
        life=state['position_id'] if held else None
        for event in c.broker.drain_events():
            reason=event['reason'];item=c.candidates.get(event.get('candidate_id'))
            if reason=='ai_review_complete':
                if item:
                    candidate,_=item
                    if (candidate['generation']==c.generation and candidate.get('decision_position_id')==life
                            and event.get('snapshot_id')==candidate['snapshot_id']
                            and candidate['signal_ms']<=now<candidate['expires_ms']):
                        self.memory.finish(candidate['id'],event['decision'],event['completed_ms'])
                continue
            if reason=='ai_error' and item:self.memory.fail(item[0]['id'],event.get('error','unknown'),now)
            c.report(reason,**{k:v for k,v in event.items() if k not in ('reason','time_ms')})
        self.approvals.extend(c.broker.drain())
        waiting=[]
        for approval in self.approvals:
            item=c.candidates.get(approval['candidate_id'])
            if not item:continue
            original,_=item;candidate=approval.get('resolved_candidate',original)
            if candidate['generation']!=c.generation or not candidate['signal_ms']<=now<candidate['expires_ms']:continue
            if not self.memory.proof(candidate):
                waiting.append(approval);continue
            c.candidates.pop(candidate['id'],None)
            if held and not c.manage_enabled():continue
            outcome=c.controller.management(candidate,approval,snapshot)
            c.report('execution',side=candidate['side'],outcome=outcome)
        self.approvals=waiting
        for key,(candidate,_) in list(c.candidates.items()):
            if candidate['expires_ms']<=now:
                self.memory.fail(key,'expired',now);c.candidates.pop(key,None)
        state=c.controller.status().get('state')
        held=bool(state and state.get('phase')!='FLAT' and number(state.get('filled_qty',0))>0)
        life=state['position_id'] if held else None
        if held and not c.manage_enabled():c.report('position_ai_disabled');return
        if paused and not held:c.report('entry_paused');return
        m=self.memory.context(c.symbol,life)
        events,m=detect_events(snapshot,m,state if held else None)
        pending={e['id']:e for e in m.get('waiting_events',[]) if event_current(e,snapshot)}
        bar=snapshot['frames']['1m'][-1]['close_ms']
        pending.update({e['id']:e for e in events})
        # Prior thesis/lifecycle events do not grant permission after a close/change.
        pending={k:e for k,e in pending.items() if e['basis_version']==m.get('basis_version','startup')}
        m['waiting_events']=self.memory.unclaimed(c.symbol,list(pending.values()))[-20:]
        self.memory.observe(c.symbol,m)
        self.telemetry=dict(self.memory.counts(c.symbol,now),last_review_ms=m.get('last_success_ms'),
            next_fallback_ms=m.get('last_success_ms',now)+int(c.review_interval()*1000),thesis=m.get('thesis'))
        if c.broker.busy(c.symbol):return
        events=m['waiting_events']
        fallback=m.get('last_success_ms') is None or now-m['last_success_ms']>=c.review_interval()*1000
        if not events and not fallback:return
        position=state if held else None
        review=dict(snapshot,position=position,events=events,decision_memory=m)
        candidate=dict(id=digest([snapshot['account'],c.symbol,now,c.generation,'event']),version=POLICY['version'],
            symbol=c.symbol,side='undecided',purpose='DECIDE',decision_source='gemini_event',
            signal_ms=now,expires_ms=now+POLICY['ttl_ms'],bar_ms=snapshot['frames']['1m'][-1]['close_ms'],
            generation=c.generation,decision_position_id=life,min_confidence=c.min_confidence(),
            reference_price=str(snapshot['frames']['1m'][-1]['close']),atr5=str(snapshot['frames']['5m'][-1]['atr14']),
            snapshot_id=digest(review),event_ids=[e['id'] for e in events])
        candidate['review_mode']=c.mode(candidate)
        result=self.memory.reserve(candidate,events,now,held and any(e['risk_priority'] for e in events))
        self.telemetry.update(result,trigger_kind=','.join(sorted({e['kind'] for e in events})) or 'fallback')
        if not result['allowed']:
            c.report(result['reason'],next_allowed_ms=result.get('next_allowed_ms'));return
        if c.broker.offer(candidate,review):
            c.candidates[candidate['id']]=(candidate,review)
            m['waiting_events']=[];self.memory.observe(c.symbol,m)
            c.report('ai_pending',purpose='DECIDE',review_mode=candidate['review_mode'],trigger_kind=self.telemetry['trigger_kind'])
        else:
            self.memory.fail(candidate['id'],'not_queued',now);c.report('review_not_queued')
