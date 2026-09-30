"""Clock-driven, dependency-injected coordinator; no production clients."""
from threading import RLock
from core_unified_market import detect_probe
from core_unified_policy import POLICY


class OwnerRegistry:
    """In-process ownership guard. Production handoff must additionally be durable."""
    def __init__(self):
        self.lock,self.owners = RLock(),{}

    def owner(self, symbol):
        with self.lock: return self.owners.get(symbol,'legacy')

    def claim(self, symbol, owner):
        with self.lock:
            if owner != 'unified' or symbol in self.owners:
                return False
            self.owners[symbol] = owner
            return True

    def release(self, symbol, owner):
        with self.lock:
            if self.owners.get(symbol) != owner:
                return False
            del self.owners[symbol]
            return True


class Runtime:
    def __init__(self, market, broker, store, executor, clock, *, mode='OFF'):
        if mode not in ('OFF','SHADOW'):
            raise ValueError('LIVE adapter/handoff has not been qualified')
        self.market,self.broker,self.store,self.executor,self.clock = market,broker,store,executor,clock
        self.mode,self.status = mode,{'mode':mode,'reason':'initializing','reviewed_candidates':0}
        self.next_risk,self.next_context = 0,0
        self.last_wall,self.last_mono,self.last_generation = None,None,None
        self.last_bars,self.candidates = {},{}
        self.stopped = False

    def _invalidate(self):
        for symbol in self.market.symbols():
            self.broker.invalidate(symbol)
        self.candidates.clear()

    def tick(self):
        if self.mode == 'OFF' or self.stopped:
            return self.status
        now,mono = self.clock.utc_ms(),self.clock.monotonic()
        # Wall-clock validity gates candidates, never the monotonic risk schedule.
        if (self.last_mono is None or mono >= self.last_mono) and mono >= self.next_risk:
            self.market.risk_tick()
            self.next_risk = (mono//30+1)*30
        if ((self.last_wall is not None and now < self.last_wall) or
                (self.last_mono is not None and mono < self.last_mono)):
            self._invalidate()
            self.status['reason'] = 'clock_regression'
            return self.status
        self.last_wall,self.last_mono = now,mono
        # The injected market has already collected data. These hooks cannot place
        # orders in SHADOW and must never make blocking network/AI calls.
        controls = self.market.controls()
        if controls.get('generation') != self.last_generation:
            self._invalidate()
            self.last_generation = controls.get('generation')
        if controls.get('paused') is not False:
            self._invalidate()
            self.status['reason'] = 'paused'
            return self.status
        if mono >= self.next_context:
            self.market.context_tick()
            self.next_context = (mono//300+1)*300
        for approval in self.broker.drain():
            candidate = self.candidates.get(approval['candidate_id'])
            if candidate and candidate['signal_ms'] <= now < candidate['expires_ms']:
                self.status['reviewed_candidates'] += 1
                self.status['last_review'] = approval
                # Reviews are telemetry only: not fills, approvals to trade, or PnL.
                self.candidates.pop(candidate['id'],None)
        self.candidates = {k:v for k,v in self.candidates.items() if v['expires_ms'] > now}
        minute = now//60000
        for symbol in self.market.symbols():
            if self.last_bars.get(symbol) == minute:
                continue
            snapshot = self.market.snapshot(symbol)
            if snapshot is None:
                continue
            rows = snapshot.get('frames',{}).get('1m',[])
            # Cache arrival may lag the minute boundary. Do not consume the new
            # minute's slot on the old bar; retry this read-only cache next tick.
            if not rows or rows[-1].get('close_ms') != minute*60000:
                self.broker.invalidate(symbol)
                continue
            self.last_bars[symbol] = minute
            snapshot = dict(snapshot,now_ms=now,generation=controls['generation'])
            candidate = detect_probe(snapshot,POLICY)
            if candidate is None:
                self.broker.invalidate(symbol)
                continue
            if self.broker.offer(candidate,snapshot):
                self.candidates[candidate['id']] = candidate
        self.status['reason'] = 'shadow_only'
        return self.status

    def stop(self):
        if self.stopped:
            return
        self.stopped = True
        if self.mode != 'OFF':
            self._invalidate()
            self.broker.close()
        self.status['reason'] = 'stopped'
