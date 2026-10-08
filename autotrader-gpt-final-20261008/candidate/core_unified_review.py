"""Bounded read-only AI workers. They cannot access an execution port."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import RLock
from core_unified_policy import confidence_ok, number


def validate_event_decision(candidate,snapshot,response):
    if not isinstance(response,dict):raise ValueError('response_shape')
    g=deepcopy(response)
    if g.get('action') not in ('long','short','hold','wait','close','reduce','add'):raise ValueError('action')
    if not confidence_ok(g.get('confidence'),0) or not confidence_ok(g.get('regime_confidence'),0):raise ValueError('confidence')
    if g.get('market_regime') not in ('bullish','bearish','neutral','transition'):raise ValueError('regime')
    for k in ('reasoning','thesis','changed_evidence'):
        if not isinstance(g.get(k),str) or not g[k].strip() or len(g[k])>1000:raise ValueError(k)
    p=snapshot.get('position')
    entering=g['action'] in ('long','short') and (not p or g['action']!=p['side'])
    if g['action'] in ('close','reduce','add') and not p:raise ValueError('position_required')
    if entering:
        fraction=number(g.get('entry_fraction'))
        if fraction not in tuple(map(number,('.25','.5','.75','1'))):raise ValueError('entry_fraction')
        if not isinstance(g.get('allocation_reason'),str) or not g['allocation_reason'].strip():raise ValueError('allocation_reason')
        g['entry_fraction']=float(fraction)
    else:g['entry_fraction']=None
    for k in ('next_confirmation_price','invalidation_price'):
        g[k]=float(number(g[k],positive=True)) if g.get(k) is not None else None
    return {k:g.get(k) for k in ('action','confidence','market_regime','regime_confidence','reasoning',
        'entry_fraction','thesis','changed_evidence','next_confirmation_price','invalidation_price','allocation_reason')}


class ReviewBroker:
    def __init__(self,gemini,gpt,clock_ms,max_workers=4,require_gpt=lambda:True):
        self.gemini,self.gpt,self.clock = gemini,gpt,clock_ms
        self.require_gpt = require_gpt
        self.pool = ThreadPoolExecutor(max_workers=max_workers,thread_name_prefix='core-review')
        self.limit = max_workers
        self.lock = RLock()
        self.running,self.queued,self.latest,self.seen = {},{},{},{}
        self.results,self.errors = [],[]
        self.events = []
        self.closing = False

    def mode(self,c):
        return 'gemini' if c.get('purpose') in ('ADD','CLOSE','REDUCE') or self.require_gpt() is False else 'dual'

    def event(self,c,reason,**details):
        with self.lock:
            self.events.append(dict(candidate_id=c['id'],symbol=c['symbol'],side=c['side'],
                purpose=c.get('purpose','ENTRY'),time_ms=self.clock(),reason=reason,**details))
            self.events=self.events[-100:]

    def drain_events(self):
        with self.lock:
            events,self.events=self.events,[]
            return events

    def busy(self,symbol):
        with self.lock:return symbol in self.running or symbol in self.queued

    def offer(self,candidate,snapshot):
        with self.lock:
            now = self.clock()
            self.seen = {k:v for k,v in self.seen.items() if v > now}
            if self.closing or candidate['id'] in self.seen:
                return False
            if not candidate['signal_ms'] <= now < candidate['expires_ms']:
                return False
            symbol = candidate['symbol']
            if symbol not in self.running and len(self.running) >= self.limit:
                return False
            c,s = deepcopy(candidate),deepcopy(snapshot)
            if c.get('review_mode','dual') != self.mode(c): return False
            self.seen[c['id']] = c['expires_ms']
            self.latest[symbol] = (c['id'],c['generation'])
            if symbol in self.running:
                self.queued[symbol] = (c,s)
            else:
                self._start(c,s)
            return True

    def _start(self,c,s):
        symbol = c['symbol']
        # Store slot before callback registration: completed futures invoke inline.
        future = self.pool.submit(self._review,c,s)
        self.running[symbol] = future
        future.add_done_callback(lambda f:self._done(symbol,c,f))

    def _review(self,c,s):
        try:
            mode=c.get('review_mode','dual')
            if mode!=self.mode(c): return None
            g = self.gemini(deepcopy(c),deepcopy(s))
            if c.get('decision_source')=='gemini_event':
                g=validate_event_decision(c,s,g)
                with self.lock:
                    if self.latest.get(c['symbol'])!=(c['id'],c['generation']):return None
                if mode!=self.mode(c) or not c['signal_ms']<=self.clock()<c['expires_ms']:return None
                self.event(c,'ai_review_complete',generation=c['generation'],snapshot_id=c['snapshot_id'],
                    completed_ms=self.clock(),decision=g,lifecycle_id=c.get('decision_position_id'))
                c=dict(c,entry_fraction=g['entry_fraction'],event_decision=deepcopy(g))
            self.event(c,'gemini_decision',action=g.get('action'),confidence=g.get('confidence'),
                       market_regime=g.get('market_regime'),regime_confidence=g.get('regime_confidence'),
                       reasoning=str(g.get('reasoning',''))[:500])
            if mode!=self.mode(c): return None
            if c.get('purpose')=='DECIDE':
                action=g.get('action'); position=s.get('position')
                if action in ('hold','wait'): return None
                if action in ('long','short'):
                    if position and action==position['side']: return None
                    c=dict(c,side=action,purpose='REVERSE' if position else 'ENTRY',decision_action=action)
                elif position and action in ('close','reduce','add'):
                    c=dict(c,side=position['side'],purpose={'close':'CLOSE','reduce':'REDUCE','add':'ADD'}[action],
                           decision_action=action)
                else: return None
                c['review_mode']=mode=self.mode(c)
            if (g.get('action') != c.get('decision_action',c['side']) or
                    not confidence_ok(g.get('confidence'),c.get('min_confidence','0.70'))):
                self.event(c,'confidence_below_minimum',confidence=g.get('confidence'),
                           minimum=c.get('min_confidence','0.70'))
                return None
            with self.lock:
                if self.latest.get(c['symbol']) != (c['id'],c['generation']):
                    return None
            if mode!=self.mode(c) or not c['signal_ms'] <= self.clock() < c['expires_ms']:
                self.event(c,'expired_or_settings_changed')
                return None
            # GPT receives the very same immutable market snapshot and Gemini decision.
            gs = deepcopy(s)
            gs['gemini_decision'] = g
            result = dict(decision='not_required',confidence=None)
            if mode=='dual':
                result = self.gpt(deepcopy(c),gs)
                self.event(c,'gpt_decision',action=result.get('decision'),confidence=result.get('confidence'),
                           reasoning=str(result.get('reasoning',''))[:500])
                if result.get('decision') != 'approve_now' or not confidence_ok(result.get('confidence')):
                    return None
            if mode!=self.mode(c): return None
            return dict(candidate_id=c['id'],snapshot_id=c['snapshot_id'],
                        review_mode=mode,purpose=c.get('purpose','ENTRY'),
                        resolved_candidate=deepcopy(c),entry_fraction=c.get('entry_fraction'),
                        generation=c['generation'],gemini_action=g['action'],
                        gemini_confidence=g['confidence'],gpt_decision=result['decision'],
                        gpt_confidence=result['confidence'],completed_ms=self.clock())
        except Exception as exc:
            self.event(c,'ai_error',error=type(exc).__name__)
            with self.lock:
                self.errors.append(dict(candidate_id=c['id'],error=type(exc).__name__))
                self.errors = self.errors[-100:]
            return None

    def _done(self,symbol,c,future):
        result = future.result()
        with self.lock:
            if (result and result['review_mode']==self.mode(result.get('resolved_candidate',c)) and self.latest.get(symbol) == (c['id'],c['generation']) and
                    c['signal_ms'] <= result['completed_ms'] <= self.clock() < c['expires_ms']):
                self.results.append((symbol,c['expires_ms'],result))
            self.running.pop(symbol,None)
            next_job = self.queued.pop(symbol,None)
            if next_job and not self.closing:
                self._start(*next_job)

    def invalidate(self,symbol):
        with self.lock:
            self.latest.pop(symbol,None)
            self.queued.pop(symbol,None)
            self.results = [r for r in self.results if r[0] != symbol]

    def drain(self):
        with self.lock:
            result = [a for symbol,expires,a in self.results
                      if a.get('review_mode','dual')==self.mode(a) and a['completed_ms'] <= self.clock() < expires and
                      self.latest.get(symbol) == (a['candidate_id'],a['generation'])]
            self.results = []
            return result

    def close(self):
        # Shutdown has a bounded wait only if supplied adapters enforce timeouts.
        # Runtime stop invalidates all symbols first; ordinary drain-close may retain results.
        with self.lock:
            self.closing = True
            self.queued.clear()
        self.pool.shutdown(wait=True,cancel_futures=True)
