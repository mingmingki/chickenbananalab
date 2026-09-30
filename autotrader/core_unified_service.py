"""Production loop integration. Import is inert; start remains an operator action."""
import functools
import inspect
import json
import os
import sqlite3
import time
from threading import RLock
from core_unified_market import detect_probe, digest, validate_snapshot, probe_checks
from core_unified_policy import POLICY, number, review_mode

_registry={}
_lock=RLock()

def review_log_text(event):
    def confidence(value):
        try: return format(number(value),'.2f')
        except ValueError: return '-'
    reason=event.get('reasoning')
    reason=' '.join(str(reason).split()) if reason else '판단 근거가 응답에 포함되지 않았습니다.'
    return ('Gemini 판단: '+str(event.get('action') or '-')+
            ' (확신도 '+confidence(event.get('confidence'))+') / regime='+
            str(event.get('market_regime') or '-')+' ('+
            confidence(event.get('regime_confidence'))+') - '+reason)

def database(cfg):
    return os.path.join(cfg.user_dir,'core_unified.sqlite3')

def owner(cfg,symbol):
    path=database(cfg)
    if not os.path.exists(path): return 'legacy'
    try:
        from pathlib import Path
        connection=sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True,timeout=5)
        try:
            row=connection.execute('SELECT owner FROM owners WHERE symbol=?',(symbol,)).fetchone()
            if row: return row[0]
            unresolved=connection.execute('SELECT payload FROM states WHERE symbol=?',(symbol,)).fetchone()
            return 'unknown' if unresolved else 'legacy'
        finally: connection.close()
    except Exception: return 'unknown'

def legacy_writer(function):
    signature=inspect.signature(function)
    @functools.wraps(function)
    def guarded(*args,**kwargs):
        bound=signature.bind(*args,**kwargs).arguments
        cfg,symbol=bound['cfg'],bound['symbol']
        import candidate_c_hybrid_ownership
        with candidate_c_hybrid_ownership.account_order_lock(cfg.user_dir):
            if owner(cfg,symbol)!='legacy': return False
            return function(*args,**kwargs)
    return guarded

def routed_close(cfg,state,client,symbol,reason):
    """None means legacy; absence/corruption never grants a legacy fallback."""
    if owner(cfg,symbol)=='legacy': return None
    with _lock: controller=_registry.get((os.path.realpath(cfg.user_dir),symbol))
    if controller is None: return False
    outcome=controller.manual_close(reason)
    status=controller.status(); position=status.get('state')
    confirmed=(status.get('pending') is None and position is not None and
               position.get('phase')=='FLAT' and number(position['filled_qty'])==0)
    if confirmed:
        from core_unified_accounting import journal_complete
        confirmed=journal_complete(controller.store,symbol,position['position_id'])
    if confirmed:
        state.update_symbol(symbol,position=None,live_position=None,entry_time=None)
        if reason=='manual_stop':
            import core_manual_close
            record=core_manual_close.get(cfg.user_dir,symbol)
            if record:
                core_manual_close.patch(cfg.user_dir,symbol,record['close_id'],journaled=True,
                    journal_source='core_unified.sqlite3',cleanup_pending=False)
                core_manual_close.confirm(cfg.user_dir,symbol,record['close_id'],
                                          getattr(cfg,'REENTRY_COOLDOWN_MINUTES',15)*60)
    return confirmed

class Coordinator:
    def __init__(self,symbol,feed,broker,controller,clock_ms,monotonic,controls,
                 notify=None,mode=None,manage_enabled=lambda:True,review_interval=None,min_confidence=lambda:.6,event_mode=lambda:False,event_memory=None):
        self.symbol,self.feed,self.broker,self.controller=symbol,feed,broker,controller
        self.clock,self.monotonic,self.controls=clock_ms,monotonic,controls
        self.event_mode=event_mode;self.event_scheduler=None
        if event_memory is not None:
            from core_unified_event_scheduler import EventScheduler
            self.event_scheduler=EventScheduler(self,event_memory)
        self.generation=None; self.next_risk=0; self.last_bar=None
        self.candidates={}; self.stopped=False; self.last_wall=None
        self.notify=notify or (lambda **event:None)
        self.mode=mode or (lambda candidate:'dual')
        self.manage_enabled=manage_enabled
        self.last_status={'reason':'starting'}
        self.last_ai_decision=None
        self.last_risk=None
        self.review_interval=review_interval; self.next_review=0; self.min_confidence=min_confidence

    def report(self,reason,**details):
        self.last_status=dict(reason=reason,time_ms=self.clock(),**details)
        if reason=='gemini_decision': self.last_ai_decision=dict(self.last_status)
        self.notify(**self.last_status)

    def invalidate(self):
        self.broker.invalidate(self.symbol); self.candidates.clear()
        if self.event_scheduler:self.event_scheduler.invalidate(self.generation)

    def tick(self):
        if self.stopped: return
        now=self.clock(); mono=self.monotonic()
        generation,paused=self.controls()
        if generation!=self.generation:
            self.invalidate(); self.generation=generation; self.next_review=0
        self.feed.generation=generation
        snapshot=self.feed.snapshot()
        if snapshot: snapshot=dict(snapshot,now_ms=now,generation=generation)
        # Risk reconciliation is scheduled independently of data/AI availability.
        if mono>=self.next_risk:
            self.last_risk=self.controller.risk_tick(snapshot or {})
            self.next_risk=mono+30
        entry_pause=bool(self.event_mode() and isinstance(paused,dict) and paused.get('reason')=='entry_paused')
        if (paused and not entry_pause) or (self.last_wall is not None and now<self.last_wall):
            cause=(dict(paused) if isinstance(paused,dict) else {'reason':'entry_paused'}) if paused else {'reason':'clock_moved_back'}
            self.invalidate(); self.last_wall=now
            self.report(cause.pop('reason'),**cause); return
        self.last_wall=now
        if not snapshot or not validate_snapshot(snapshot,now)[0]:
            self.invalidate(); self.report('market_data_unavailable'); return
        status=self.controller.status()
        if status.get('owner')!='unified': self.invalidate(); return
        if isinstance(getattr(self.controller,'pending_reverse',None),tuple):
            outcome=self.controller.continue_reverse(snapshot)
            self.report('reverse_execution',outcome=outcome)
            return
        if self.event_mode():
            if self.event_scheduler is None:self.report('event_memory_unavailable');return
            try:self.event_scheduler.tick(snapshot,paused=bool(paused))
            except Exception as exc:
                self.invalidate();self.report('event_memory_error',error=type(exc).__name__)
            return
        for event in self.broker.drain_events():
            self.report(event.pop('reason'),**{k:v for k,v in event.items() if k!='time_ms'})
        for approval in self.broker.drain():
            item=self.candidates.pop(approval['candidate_id'],None)
            if item:
                candidate,original=item
                candidate=approval.get('resolved_candidate',candidate)
                if candidate['generation']==generation and candidate['signal_ms']<=now<candidate['expires_ms']:
                    if candidate.get('review_mode','dual')!=self.mode(candidate): continue
                    outcome=self.controller.management(candidate,approval,snapshot)
                    self.report('execution',side=candidate['side'],outcome=outcome)
        self.candidates={k:v for k,v in self.candidates.items() if v[0]['expires_ms']>now}
        if self.review_interval is not None:
            if mono<self.next_review: return
            position=self.controller.status().get('state')
            held=position and position.get('phase')!='FLAT' and number(position.get('filled_qty',0))>0
            snapshot=dict(snapshot,position=position if held else None)
            candidate=dict(id=digest([snapshot['account'],self.symbol,now,generation,'Gemini']),
                version=POLICY['version'],symbol=self.symbol,side='undecided',purpose='DECIDE',
                decision_source='gemini_periodic',signal_ms=now,expires_ms=now+POLICY['ttl_ms'],
                bar_ms=snapshot['frames']['1m'][-1]['close_ms'],generation=generation,
                decision_position_id=position['position_id'] if held else None,
                min_confidence=self.min_confidence(),
                reference_price=str(snapshot['frames']['1m'][-1]['close']),
                atr5=str(snapshot['frames']['5m'][-1]['atr14']),snapshot_id=digest(snapshot))
            candidate['review_mode']=self.mode(candidate)
            if self.broker.offer(candidate,snapshot):
                self.candidates[candidate['id']]=(candidate,snapshot)
                self.next_review=mono+max(1,float(self.review_interval()))
                self.report('ai_pending',purpose='DECIDE',review_mode=candidate['review_mode'])
            else:
                self.next_review=mono+5
                self.report('review_not_queued')
            return
        bar=snapshot['frames']['1m'][-1]['close_ms']
        if bar!=now//60000*60000 or self.last_bar==bar: return
        self.last_bar=bar
        position=self.controller.status().get('state')
        held=position and position.get('phase')!='FLAT' and number(position.get('filled_qty',0))>0
        if held: snapshot=dict(snapshot,position=position)
        if held and not self.manage_enabled():
            self.invalidate(); self.report('position_ai_disabled'); return
        candidate=detect_probe(snapshot,POLICY)
        if held and candidate:
            candidate['purpose']='ADD' if candidate['side']==position['side'] else 'REVERSE'
        elif held and not position.get('protection_armed') and bar==snapshot['frames']['5m'][-1]['close_ms']:
            rows=snapshot['frames']['5m']; sign=1 if position['side']=='long' else -1
            if (sign*(number(rows[-1]['close'])-number(rows[-1]['ema20']))>0 and
                    sign*(number(rows[-1]['macd'])-number(rows[-2]['macd']))>0):
                candidate=dict(id=digest([snapshot['account'],self.symbol,position['position_id'],bar,'ADD']),
                    version=POLICY['version'],symbol=self.symbol,side=position['side'],purpose='ADD',
                    signal_ms=bar,expires_ms=bar+POLICY['ttl_ms'],generation=generation,
                    reference_price=str(snapshot['frames']['1m'][-1]['close']),
                    atr5=str(rows[-1]['atr14']),snapshot_id=digest(snapshot))
        if candidate:
            candidate.setdefault('purpose','ENTRY')
            candidate['review_mode']=self.mode(candidate)
            if self.broker.offer(candidate,snapshot):
                self.candidates[candidate['id']]=(candidate,snapshot)
                self.report('ai_pending',side=candidate['side'],purpose=candidate['purpose'],
                            review_mode=candidate['review_mode'])
            else: self.report('review_not_queued')
        else:
            self.invalidate(); self.report('no_signal',checks=probe_checks(snapshot),bar_ms=bar)

    def stop(self):
        self.stopped=True; self.invalidate(); self.controller.close()
        self.feed.close(); self.broker.close()

class ShadowController:
    """Telemetry only: deliberately has no store, exchange client or order port."""
    def __init__(self): self.last_review=None
    def status(self): return {'owner':'unified','state':None,'mode':'SHADOW','last_review':self.last_review}
    def risk_tick(self,snapshot): pass
    def management(self,candidate,approval,snapshot):
        self.last_review={'candidate_id':candidate['id'],'approval':approval}
    def close(self): pass

def rollback_step(controller):
    """Transfer only an exchange-proven, fully settled flat lifecycle."""
    if controller.store.owner_record(controller.symbol)['owner']=='legacy':
        return dict(ok=True,reason='already_legacy')
    return controller.handoff.transfer(controller.symbol,source='unified',target='legacy')


def run_rollback(cfg,state,symbol,client,loss_guard,stop_event):
    from pathlib import Path
    from core_unified_store import Store
    from core_unified_live import LiveController
    from core_unified_notifications import start_notifications
    import symbol_entry_control
    store=Store(database(cfg));clock=lambda:int(time.time()*1000)
    controller=LiveController(cfg,state,client,symbol,loss_guard,store,clock,stop_event)
    key=(os.path.realpath(cfg.user_dir),symbol)
    with _lock:_registry[key]=controller
    notifications=None
    try:notifications=start_notifications(cfg,store,symbol)
    except Exception as exc:cfg.logger.warning('[%s] 체결 알림 초기화 실패 · %s',symbol,type(exc).__name__)
    try:
        while not stop_event.is_set():
            try:
                # No market collector or AI broker is constructed while draining.
                risk=controller.risk_tick({}) if store.owner_record(symbol)['owner']=='unified' else None
                outcome=rollback_step(controller)
                state.update_symbol(symbol,unified_status=dict(rollback=outcome,risk=risk))
                if outcome['ok']:
                    path=Path(cfg.user_dir)/'core_rollback_pause_manifest.json'
                    records=json.loads(path.read_text())
                    with controller.port.locked():
                        current=symbol_entry_control.get_status(cfg.user_dir,symbol)
                        if current==records[symbol]['during']:
                            symbol_entry_control.set_paused(cfg.user_dir,symbol,records[symbol]['before']['paused'])
                    state.update_symbol(symbol,unified_status=None,position=None,live_position=None,entry_time=None)
                    cfg.logger.info('[%s] 이전 CORE 복구 완료 · Gemini 5분 검토 + GPT 진입 승인 · 25%% 선행진입 해제',symbol)
                    return False
                cfg.logger.info('[%s] 이전 CORE 전환 대기 · 기존 포지션 보호/체결 확인 중 · 사유=%s',symbol,outcome['reason'])
            except Exception as exc:
                cfg.logger.warning('[%s] 이전 CORE 전환 확인 실패 · %s',symbol,type(exc).__name__)
            stop_event.wait(30)
        return True
    finally:
        controller.close()
        with _lock:_registry.pop(key,None)
        if notifications is None:store.close()


def run_symbol(cfg,state,symbol,client,loss_guard,stop_event,legacy_cycle,legacy_fast):
    """Return False only for OFF with no durable unified owner."""
    mode=getattr(cfg,'CORE_UNIFIED_MODE','OFF')
    if mode=='ROLLBACK':
        return run_rollback(cfg,state,symbol,client,loss_guard,stop_event)
    current=owner(cfg,symbol)
    if mode=='OFF' and current=='legacy': return False
    if mode=='SHADOW' and getattr(cfg,'EXECUTION_MODE','OFF')!='OFF':
        from core_unified_adapters import MarketFeed,make_broker
        clock=lambda:int(time.time()*1000)
        controller=ShadowController(); feed=MarketFeed(cfg,symbol,clock)
        from core_unified_store import Store
        from core_unified_event_memory import EventMemory
        shadow_store=Store(os.path.join(cfg.user_dir,'core_event_shadow.sqlite3'))
        coordinator=Coordinator(symbol,feed,make_broker(cfg,clock),controller,clock,time.monotonic,
            lambda:('shadow',False),mode=lambda c:review_mode(cfg,c),
            review_interval=lambda:cfg.POLL_INTERVAL_SECONDS,min_confidence=lambda:cfg.MIN_CONFIDENCE,
            event_mode=lambda:getattr(cfg,'CORE_EVENT_AI_ENABLED',False),event_memory=EventMemory(shadow_store))
        feed.start(stop_event)
        try:
            while not stop_event.is_set():
                try:
                    coordinator.tick(); state.update_symbol(symbol,unified_status=controller.status())
                except Exception:
                    cfg.logger.exception('[%s] unified shadow collection failed',symbol)
                stop_event.wait(0.5)
        finally:
            coordinator.stop();shadow_store.close()
        return True
    if mode!='LIVE' or getattr(cfg,'EXECUTION_MODE','OFF')!='LIVE':
        # An owned lifecycle cannot fall back to independent legacy writers.
        state.update_symbol(symbol,unified_status='inactive_mode')
        stop_event.wait(); return True
    from core_unified_store import Store
    from core_unified_live import LiveController
    from core_unified_adapters import MarketFeed,make_broker
    store=Store(database(cfg)); clock=lambda:int(time.time()*1000)
    notifications=None
    try:
        from core_unified_notifications import start_notifications
        notifications=start_notifications(cfg,store,symbol)
    except Exception as exc:
        cfg.logger.warning('[CORE telegram] 알림 초기화 실패 · %s',type(exc).__name__)
    controller=LiveController(cfg,state,client,symbol,loss_guard,store,clock,stop_event)
    feed=MarketFeed(cfg,symbol,clock); broker=make_broker(cfg,clock)
    key=(os.path.realpath(cfg.user_dir),symbol)
    with _lock: _registry[key]=controller
    def controls():
        import core_kill_switch,symbol_entry_control,core_manual_close
        record=store.owner_record(symbol)
        pause_record=symbol_entry_control.get_status(cfg.user_dir,symbol)
        paused=pause_record['paused']
        killed=core_kill_switch.is_active(cfg.user_dir)
        manual=core_manual_close.get(cfg.user_dir,symbol)
        return digest([record['generation'],pause_record,killed,manual,
            cfg.GPT_ENTRY_GATE_ENABLED,cfg.POSITION_AI_REVIEW_ENABLED,getattr(cfg,'CORE_EVENT_AI_ENABLED',False),
            getattr(cfg,'settings_revision',0)]),(dict(reason='safety_stop',detail=core_kill_switch.get_reason(cfg.user_dir))
            if killed else dict(reason='entry_paused') if paused else False)
    last_log=[None,0]
    def notify(**event):
        identity=digest({k:v for k,v in event.items() if k!='time_ms'})
        if identity!=last_log[0] or (event['reason'] not in ('event_call_budget','event_error_backoff','event_call_spacing') and clock()-last_log[1]>=60000):
            if event['reason']=='gemini_decision':
                cfg.logger.info('[%s] %s',symbol,review_log_text(event))
            else:
                cfg.logger.info('[%s] CORE %s',symbol,json.dumps(event,ensure_ascii=False,default=str))
            last_log[:]=[identity,clock()]
    from core_unified_event_memory import EventMemory
    memory=EventMemory(store)
    coordinator=Coordinator(symbol,feed,broker,controller,clock,time.monotonic,controls,
        notify=notify,mode=lambda c:review_mode(cfg,c),
        manage_enabled=lambda:cfg.POSITION_AI_REVIEW_ENABLED and cfg.POSITION_AI_LIVE_EXECUTE,
        review_interval=lambda:cfg.POLL_INTERVAL_SECONDS,min_confidence=lambda:cfg.MIN_CONFIDENCE,
        event_mode=lambda:getattr(cfg,'CORE_EVENT_AI_ENABLED',False),event_memory=memory)
    controller.current_generation=lambda:controls()[0]
    next_legacy=0; next_fast=0; next_ready=0
    next_status=0
    cfg.logger.info('[%s] CORE 시작: mode=%s AI=%s Gemini 검토주기=%ss 사건모드=%s 사전 지표필터=없음 위험점검=30s',symbol,mode,review_mode(cfg),cfg.POLL_INTERVAL_SECONDS,getattr(cfg,'CORE_EVENT_AI_ENABLED',False))
    feed.start(stop_event)
    try:
        while not stop_event.is_set():
            try:
                mono=time.monotonic()
                if mono>=next_ready:
                    controller.ready(); next_ready=mono+30
                current=store.owner_record(symbol)['owner']
                if current=='legacy':
                    if mono>=next_legacy:
                        legacy_cycle(); next_legacy=time.monotonic()+cfg.POLL_INTERVAL_SECONDS
                    if mono>=next_fast:
                        legacy_fast(); next_fast=time.monotonic()+30
                else:
                    coordinator.tick()
                    if mono>=next_status:
                        from pathlib import Path
                        status=dict(controller.status(),last_tick_ms=clock(),review_mode=review_mode(cfg),
                            decision_source='gemini_event' if coordinator.event_mode() else 'gemini_periodic',
                            event_ai_enabled=coordinator.event_mode(),event_status=coordinator.event_scheduler.telemetry if coordinator.event_mode() else {},
                            review_interval_seconds=cfg.POLL_INTERVAL_SECONDS,
                            next_review_ms=clock()+int(max(0,coordinator.next_review-time.monotonic())*1000),
                            last_ai_decision=coordinator.last_ai_decision,
                            decision=coordinator.last_status,risk=coordinator.last_risk,feed_error=feed.error,
                            market_collected_ms=(feed.snapshot() or {}).get('collected_ms'))
                        ai=coordinator.last_ai_decision or {}
                        state.update_symbol(symbol,unified_status=status,last_action=ai.get('action','CORE'),
                            last_confidence=ai.get('confidence'),last_reasoning=ai.get('reasoning') or json.dumps(coordinator.last_status,ensure_ascii=False),
                            last_market_regime=ai.get('market_regime'),last_regime_confidence=ai.get('regime_confidence'))
                        path=Path(cfg.user_dir)/('core_runtime_'+digest(symbol)[:12]+'.json')
                        temp=path.with_suffix('.tmp')
                        temp.write_text(json.dumps(dict(symbol=symbol,**status),ensure_ascii=False,default=str))
                        os.replace(temp,path)
                        if coordinator.last_status['reason'] not in ('event_call_budget','event_error_backoff','event_call_spacing'):
                            cfg.logger.info('[%s] CORE heartbeat AI=%s 상태=%s 위험점검=%s',symbol,
                                status['review_mode'],coordinator.last_status['reason'],coordinator.last_risk)
                        next_status=mono+30
            except Exception as exc:
                cfg.logger.exception('[%s] unified loop blocked',symbol)
                state.update(last_error=symbol+': unified '+type(exc).__name__)
            stop_event.wait(0.5)
    finally:
        coordinator.stop()
        # Keep the controller reachable for an explicitly requested manual close;
        # the existing dashboard stop handler signals the loop before closing.
    return True
