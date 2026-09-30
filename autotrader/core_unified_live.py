"""Production bridge. Construction is inert; all mutations require LIVE and account lock.
Logical lifecycle IDs never substitute for exchange position IDs. Unknown writes
remain durably reserved; restart only queries them and never resubmits them.
"""
from decimal import Decimal as D, ROUND_CEILING, ROUND_FLOOR
from core_unified_policy import POLICY, number, floor_qty, probe_qty, approval_valid, review_mode, is_ai_decision, entry_qty
from core_unified_market import digest, validate_snapshot, WIDTH
from core_unified_state import new_position, advance
from core_unified_execution import ExecutionManager, result
from core_unified_inventory import LegacyInventoryPort
from core_unified_handoff import Handoff
from core_unified_okx_contract import position_identity, owned_protection


def quantized_stop(price,side,tick):
    tick=number(tick,positive=True)
    if side not in ('long','short'): raise ValueError('side')
    return (number(price,positive=True)/tick).to_integral_value(
        rounding=ROUND_CEILING if side=='long' else ROUND_FLOOR)*tick


def trend_flags(snapshot,side):
    sign=1 if side=='long' else -1
    frames=snapshot['frames']; five=frames['5m']; hour=frames['1h']
    def ema(row): return sign*(number(row['close'])-number(row['ema20']))
    def momentum(a,b): return sign*(number(a['macd'])-number(b['macd']))
    return dict(weak5=len(five)>=3 and all(momentum(five[j],five[j-1])<0 for j in (-1,-2))
                and all(ema(r)<0 for r in five[-2:]),
                weak1=ema(hour[-1])<0 and momentum(hour[-1],hour[-2])<0,
                opposed1=ema(hour[-1])<0,
                confirmed5=ema(five[-1])>0 and momentum(five[-1],five[-2])>0)


def near_stop_confirmation(state,position,now_ms):
    """Retain legacy 90% adverse stop-distance threshold and 25-second confirmation."""
    sign=1 if position['side']=='long' else -1
    entry=number(position['entry_price'],positive=True)
    distance=sign*(entry-number(state['stop_price'],positive=True))
    adverse=sign*(entry-number(position['mark_price'],positive=True))
    if distance<=0 or adverse/distance<D('.9'): return None,False
    first=state.get('emergency_first_ms')
    if type(first) is not int or now_ms<first: return now_ms,False
    return first,now_ms-first>=25000


class LivePort(LegacyInventoryPort):
    def __init__(self,controller):
        self.c=controller
        super().__init__(controller.cfg,controller.client,controller.clock)

    def executable_quote(self,side):
        started=self.clock(); quote=self.client.exchange.fetch_ticker(self.client.symbol)
        bid,ask=number(quote['bid'],positive=True),number(quote['ask'],positive=True)
        if bid>ask: raise ValueError('crossed_quote')
        observed=quote.get('timestamp')
        if observed is not None and (type(observed) is not int or not 0<=self.clock()-observed<=5000):
            raise ValueError('stale_quote')
        if not 0<=self.clock()-started<=5000: raise ValueError('slow_quote')
        return ask if side=='long' else bid

    def raw_position(self):
        rows=self.client.exchange.fetch_positions([self.client.symbol])
        if not isinstance(rows,list): raise ValueError('positions_unknown')
        active=[]
        for row in rows:
            if row.get('symbol')!=self.client.symbol: raise ValueError('position_symbol')
            if number(row['contracts'])>0:
                info=row.get('info') or {}
                pos=dict(side=row['side'],contracts=row['contracts'],position_id=info.get('posId'),
                         entry_timestamp_ms=int(info.get('cTime') or 0),pos_side=info.get('posSide'),
                         entry_price=row['entryPrice'],mark_price=row['markPrice'])
                if pos['pos_side']!='net' or not position_identity(pos): raise ValueError('net_identity_required')
                active.append(pos)
        if len(active)>1: raise ValueError('multiple_positions')
        return active[0] if active else None

    def position(self,symbol):
        raw=self.raw_position()
        if raw is None: return None
        state=self.c.store.state(symbol)
        actual=position_identity(raw)
        if state and state.get('exchange_identity')==actual:
            return dict(raw,position_id=state['position_id'])
        pending=self.c.store.pending(symbol)
        if state and not state.get('exchange_identity') and pending and pending['kind']=='ENTRY':
            order=self.lookup(pending['id'])
            # Bind only a new position born during this reserved entry, with
            # order-specific fills exactly explaining its actual quantity.
            if (order and number(order['filled'])>0 and number(order['filled'])==number(raw['contracts'])
                    and raw['side']==pending['side'] and
                    pending['created_ms']<=raw['entry_timestamp_ms']<=self.clock()):
                self.c.store.patch_metadata(symbol,exchange_identity=actual)
                return dict(raw,position_id=state['position_id'])
        raise ValueError('exchange_identity_unbound')

    def lookup(self,client_id):
        pending=self.c.store.pending(self.client.symbol)
        if not pending or pending['id']!=client_id: raise ValueError('lookup_reservation')
        return super().lookup(client_id,pending['created_ms'])

    def preflight(self,symbol,intent):
        c=self.c; state=c.store.state(symbol); metadata=self.client.instrument_metadata()
        if not c.enabled() or c.store.owner_record(symbol)['owner']!='unified':
            return dict(allowed=False,reason='not_live_owner')
        if intent['kind'] in ('ENTRY','ADD'):
            reason=c.entry_guard(intent['candidate'])
            if reason: return dict(allowed=False,reason=reason)
        if intent['kind']=='ENTRY':
            account=self.client.exchange.private_get_account_config()
            if (str(account.get('code'))!='0' or len(account.get('data',[]))!=1
                    or account['data'][0].get('posMode')!='net_mode'):
                return dict(allowed=False,reason='net_mode_required')
            self.client.ensure_leverage()
        if metadata.get('max_contracts') is not None and number(intent['qty'])>number(metadata['max_contracts']):
            return dict(allowed=False,reason='max_contracts')
        quote=self.executable_quote(intent['side']) if intent['kind'] in ('ENTRY','ADD') else self.client.fetch_last_price(); number(quote,positive=True)
        quote_ms=self.clock()
        remaining=c.risk_remaining(quote) if intent['kind'] in ('ENTRY','ADD') else D(0)
        if intent['kind'] in ('ENTRY','ADD'):
            intent['risk_required']=number(intent['qty'])*(abs(number(quote)-number(state['stop_price']))+number(quote)*c.cost_rate())*number(metadata['contract_size'],positive=True)
        snap=c.snapshot
        signal_valid=validate_snapshot(snap,self.clock())[0]
        if (intent['kind'] in ('ENTRY','ADD') and signal_valid and
                not is_ai_decision(intent['candidate'])):
            rows=snap['frames']['1m' if intent['kind']=='ENTRY' else '5m']
            sign=1 if intent['side']=='long' else -1
            signal_valid=(sign*(number(rows[-1]['close'])-number(rows[-1]['ema20']))>0 and
                          sign*(number(rows[-1]['macd'])-number(rows[-2]['macd']))>0)
        return dict(allowed=True,revision=state['revision'],position_id=state['position_id'],
                    lot=metadata['lot_step'],minimum=metadata['min_contracts'],
                    quote_ms=quote_ms,latest_price=quote,risk_remaining=remaining,
                    generation=snap.get('generation'),signal_valid=signal_valid)

    def submit(self,symbol,intent):
        if not self.c.enabled(): raise ValueError('not_live')
        if intent['kind'] in ('ENTRY','ADD'):
            reason=self.c.review_settings_guard(intent['candidate'])
            if reason: raise ValueError(reason)
        qty=float(number(intent['qty'])); side=intent['side']
        if intent['kind'] in ('EXIT','REDUCE'):
            return self.client.reduce_position(self.position(symbol),qty,client_order_id=intent['id'])
        # Contract units are explicit here; legacy entry wrapper takes coin units.
        return self.client.exchange.create_order(symbol,'market','buy' if side=='long' else 'sell',
                    qty,None,{'tdMode':'cross','posSide':'net','clOrdId':intent['id']})

    def protection(self,symbol):
        pos=self.position(symbol)
        if pos is None: return dict(confirmed=True)
        state=self.c.store.state(symbol)
        raw=self.raw_position()
        return owned_protection(raw,self.client.fetch_pending_protection_orders(),
            instrument_id=self.client.exchange.market(symbol)['id'],
            owned_ids=state.get('owned_stop_ids',[]),stop_price=state['stop_price'])

    def install_protection(self,symbol,intent):
        pos=self.position(symbol); state=self.c.store.state(symbol)
        if not pos: return
        rows=self.client.fetch_pending_protection_orders()
        key=digest([intent['id'],'protect',str(pos['contracts'])])[:32]
        matches=[r for r in rows if r.get('algoClOrdId')==key]
        if matches:
            ids=state.get('owned_stop_ids',[])+[r['algoId'] for r in matches]
            self.c.store.patch_metadata(symbol,owned_stop_ids=sorted(set(ids)))
            return
        if not self.c.store.reserve_action(symbol,key,dict(kind='PROTECTION',intent_id=intent['id'])):
            return
        answer=self.client.attach_protection(pos['side'],float(number(pos['contracts'])),
                    float(number(state['stop_price'])),state.get('take_profit'),algo_client_order_id=key)
        # Do not infer ownership from unrelated pending stops or response IDs.
        rows=self.client.fetch_pending_protection_orders()
        ids=[r['algoId'] for r in rows if r.get('algoClOrdId')==key]
        self.c.store.patch_metadata(symbol,owned_stop_ids=sorted(set(state.get('owned_stop_ids',[])+ids)))

    def protection_failure(self,symbol,intent):
        import core_kill_switch
        core_kill_switch.activate(self.cfg.user_dir,symbol+' unified protection unconfirmed')
        self.c.store.patch_metadata(symbol,emergency_required=True)
        pos=self.position(symbol)
        if not pos: return
        key=digest([intent['id'],'emergency'])[:32]
        if self.c.store.reserve_action(symbol,key,dict(kind='EMERGENCY_EXIT',position_id=intent['position_id'],original_id=intent['id'],created_ms=self.clock(),qty=str(pos['contracts']))):
            self.client.reduce_position(pos,float(number(pos['contracts'])),client_order_id=key)

    def record_protection_check(self,symbol,stage,attempt,reason):
        import logging
        self.c.store.patch_metadata(symbol,protection_check=dict(stage=stage,attempt=attempt,
            reason=reason,checked_ms=self.clock()))
        logger=getattr(self.cfg,'logger',logging.getLogger(__name__))
        if reason=='confirmed':
            logger.info('[%s] 손절 보호주문 재확인 완료 · 기존 주문으로 보호 확인',symbol)
        else:
            logger.warning('[%s] 손절 보호주문 확인 지연 · %s/3차 · 단계=%s 사유=%s',
                           symbol,attempt,stage,reason)

    def reconcile_emergency(self,symbol,intent):
        actions=[a for a in self.c.store.actions(symbol) if a.get('kind')=='EMERGENCY_EXIT' and a.get('original_id')==intent['id']]
        if not actions: return None
        if len(actions)!=1: return result('reconciling','emergency_actions_conflict')
        action=actions[0]
        order=super().lookup(action['id'],action['created_ms'])
        if not order or not order['terminal'] or number(order['remaining'])!=0:
            return result('reconciling','emergency_order_unknown')
        raw=self.raw_position()
        if raw is not None:
            state=self.c.store.state(symbol)
            if (position_identity(raw)!=state.get('exchange_identity') or raw['side']!=intent['side']
                    or number(raw['contracts'])+number(order['filled'])!=number(state['filled_qty'])):
                return result('reconciling','emergency_residual_identity')
            self.c.store.emergency_flat(symbol,intent['id'],action['id'],order['trades'],residual_qty=raw['contracts'])
            return result('complete','emergency_residual_manual_required')
        if not self.cleanup_owned(symbol,intent)['confirmed'] or self.raw_position() is not None:
            return result('reconciling','emergency_cleanup_unknown')
        self.c.store.emergency_flat(symbol,intent['id'],action['id'],order['trades'])
        return result('complete','emergency_flat_kill_retained')

    def cleanup_owned(self,symbol,intent):
        if self.raw_position() is not None: return dict(confirmed=False)
        state=self.c.store.state(symbol); ids=set(state.get('owned_stop_ids',[]))
        rows=self.client.fetch_pending_protection_orders()
        remaining=[r['algoId'] for r in rows if r.get('algoId') in ids]
        if remaining: self.client.cancel_protection(remaining)
        rows=self.client.fetch_pending_protection_orders()
        return dict(confirmed=not any(r.get('algoId') in ids for r in rows))


class LiveController:
    def __init__(self,cfg,state,client,symbol,loss_guard,store,clock_ms,stop_event):
        self.cfg,self.legacy_state,self.client,self.symbol=cfg,state,client,symbol
        self.loss_guard,self.store,self.clock,self.stop_event=loss_guard,store,clock_ms,stop_event
        self.snapshot={}; self.port=LivePort(self); self.manager=ExecutionManager(store,self.port,clock_ms)
        self.handoff=Handoff(store,self.port,clock_ms); self.closed=False; self.manual_active=False
        self.pending_reverse=None

    def enabled(self):
        return ((self.manual_active or (not self.closed and not self.stop_event.is_set()))
                and getattr(self.cfg,'CORE_UNIFIED_MODE','OFF') in ('LIVE','ROLLBACK')
                and getattr(self.cfg,'EXECUTION_MODE','OFF')=='LIVE')

    def ready(self):
        if not self.enabled(): return False
        with self.port.locked():
            try:
                if self.store.owner_record(self.symbol)['owner']=='legacy':
                    return self.handoff.transfer(self.symbol,source='legacy',target='unified')['ok']
                if self.store.pending(self.symbol):
                    self.manager.reconcile(self.symbol); return False
                state=self.store.state(self.symbol); pos=self.port.raw_position()
                if pos is None:
                    return not state or state.get('phase')=='FLAT'
                return (self.port.position(self.symbol) is not None and
                        number(state['filled_qty'])==number(pos['contracts']) and
                        self.port.protection(self.symbol)['confirmed'])
            except Exception: return False

    def event_decision_guard(self,candidate):
        if candidate.get('decision_source')!='gemini_event':return None
        if not getattr(self.cfg,'CORE_EVENT_AI_ENABLED',False):return 'review_settings_changed'
        from core_unified_event_memory import EventMemory
        proof=EventMemory(self.store).proof(candidate)
        if not proof:return 'event_decision_unverified'
        g=proof['decision']
        if g.get('action')!=candidate.get('decision_action',candidate['side']):return 'event_decision_unverified'
        if candidate.get('event_decision')!=g:return 'event_decision_unverified'
        if candidate.get('purpose') in ('ADD','CLOSE','REDUCE','REVERSE') and not g.get('changed_evidence','').strip():
            return 'event_evidence_missing'
        return None

    def review_settings_guard(self,candidate):
        event=candidate.get('decision_source')=='gemini_event'
        if is_ai_decision(candidate) and event!=bool(getattr(self.cfg,'CORE_EVENT_AI_ENABLED',False)):
            return 'review_settings_changed'
        if candidate.get('review_mode','dual')!=review_mode(self.cfg,candidate): return 'review_settings_changed'
        if hasattr(self,'current_generation') and candidate['generation']!=self.current_generation():
            return 'controls_changed'
        if candidate.get('purpose') in ('ADD','REVERSE','CLOSE','REDUCE') and (
                getattr(self.cfg,'POSITION_AI_REVIEW_ENABLED',True) is not True or
                getattr(self.cfg,'POSITION_AI_LIVE_EXECUTE',True) is not True):
            return 'position_ai_disabled'
        return None

    def reentry_reason(self,candidate):
        if candidate.get('purpose') == 'ADD': return None
        rows=[r for r in self.store.accounting_records()
              if r['symbol']==self.symbol and r['kind']=='EXIT']
        if not rows: return None
        if any(type(r.get('filled_ms')) is not int for r in rows):
            return 'entry_history_unknown'
        last=max(rows,key=lambda r:r['filled_ms'])
        bound=self.pending_reverse
        if (candidate.get('purpose')=='REVERSE' and bound is not None
                and bound[0]['id']==candidate['id'] and bound[2]==last['position_id']
                and candidate['side'] in ('long','short') and candidate['side']!=last['side']):
            return None
        if candidate.get('decision_source')=='gemini_event':
            if self.clock()<last['filled_ms']+60000:return 'reentry_cooldown'
            from core_unified_event_memory import EventMemory
            proof=EventMemory(self.store).proof(candidate)
            if not proof or not any(e.get('directional') and last['filled_ms']<e['bar_ms']<=candidate['signal_ms'] for e in proof['events']):
                return 'fresh_entry_event_required'
            if not proof['decision'].get('changed_evidence','').strip():return 'event_evidence_missing'
            return None
        wait=max(float(self.cfg.MIN_HOLD_MINUTES)*60000,
                 float(self.cfg.POLL_INTERVAL_SECONDS)*1000)
        ready=last['filled_ms']+wait
        if self.clock()<ready: return 'reentry_cooldown'
        if candidate['signal_ms']<ready: return 'fresh_entry_review_required'
        return None

    def entry_guard(self,candidate):
        settings_reason=self.review_settings_guard(candidate)
        if settings_reason: return settings_reason
        reason=self.event_decision_guard(candidate)
        if reason:return reason
        reason=self.reentry_reason(candidate)
        if reason: return reason
        import core_kill_switch, symbol_entry_control, core_manual_close
        if core_kill_switch.is_active(self.cfg.user_dir): return 'kill_switch'
        if symbol_entry_control.is_paused(self.cfg.user_dir,self.symbol): return 'manual_pause'
        reason=core_manual_close.block_reason(core_manual_close.get(self.cfg.user_dir,self.symbol),
            now=self.clock()/1000,bar_closed_at=candidate.get('bar_ms',candidate['signal_ms'])/1000,
            approval_started_at=candidate['signal_ms']/1000)
        if reason: return reason
        guard=getattr(self.cfg,'unified_reentry_guards',{}).get(self.symbol)
        if guard:
            reason=guard(candidate['side'],candidate)
            if reason: return reason
        equity=float(number(self.client.fetch_usdt_equity(),positive=True))
        if not self.loss_guard.allow_new_entry(equity): return 'daily_loss'
        from core_unified_accounting import allow_new_entry
        if not allow_new_entry(self.cfg,self.store,equity,self.clock): return 'unified_daily_loss'
        return None

    def cost_rate(self):
        return D('.001') + (number(getattr(self.cfg,'SPREAD_BPS',0))+number(getattr(self.cfg,'SLIPPAGE_BPS',0)))*D('.0002')

    def risk_remaining(self,price):
        import risk_manager
        state=self.store.state(self.symbol); meta=self.client.instrument_metadata()
        cs=number(meta['contract_size'],positive=True)
        target=number(risk_manager.calculate_position_size(self.cfg,float(self.client.fetch_usdt_equity()),float(price))) / cs
        cap=min(number(state['risk_budget']),target*number(state['initial_r'])*cs)
        # Filled cost basis is accumulated from exact trade prices, not mark price.
        used=number(state.get('entry_risk_used',0))
        return max(D(0),cap-used)

    def _execute(self,intent,candidate=None,approval=None):
        if candidate is not None:
            state=self.store.state(self.symbol); price=self.port.executable_quote(candidate['side'])
            cs=number(self.client.instrument_metadata()['contract_size'],positive=True)
            intent.update(candidate=candidate,approval=approval,
                risk_required=number(intent['qty'])*(abs(price-number(state['stop_price']))+price*self.cost_rate())*cs)
        intent['created_ms']=self.clock()
        return self.manager.execute(self.symbol,intent)

    def entry(self,candidate,approval,snapshot):
        self.snapshot=snapshot
        with self.port.locked():
            try:
                if not self.ready(): return result('blocked','not_ready')
                if self.port.raw_position() is not None: return result('blocked','not_flat')
                if self.store.has_intent(digest([candidate['id'],'ENTRY'])[:32]): return result('blocked','candidate_consumed')
                if not validate_snapshot(snapshot,self.clock())[0]: return result('blocked','snapshot')
                price=self.port.executable_quote(candidate['side'])
                valid,why=approval_valid(candidate,approval,self.clock(),price)
                if not valid: return result('blocked',why)
                reason=self.entry_guard(candidate)
                if reason: return result('blocked',reason)
                import risk_manager
                meta=self.client.instrument_metadata(); cs=number(meta['contract_size'],positive=True)
                target=floor_qty(number(risk_manager.calculate_position_size(self.cfg,
                    float(self.client.fetch_usdt_equity()),float(price)))/cs,meta['lot_step'],meta['min_contracts'])
                qty=entry_qty(candidate,target,meta['lot_step'],meta['min_contracts'])
                if qty<=0 or (meta.get('max_contracts') is not None and qty>number(meta['max_contracts'])):
                    return result('blocked','quantity_limits')
                stop,tp=risk_manager.sl_tp_prices(self.cfg,candidate['side'],float(price))
                stop=quantized_stop(stop,candidate['side'],meta['tick_size'])
                tp=float(self.client.exchange.price_to_precision(self.symbol,tp)) if tp is not None else None
                if (1 if candidate['side']=='long' else -1)*(price-stop)<=0:
                    return result('blocked','invalid_quantized_stop')
                initial_r=abs(price-number(stop)); pid=digest([candidate['id'],'lifecycle'])[:32]
                risk_budget=target*initial_r*cs
                if candidate.get('decision_source')=='gemini_event':
                    target=floor_qty(risk_budget/((initial_r+price*self.cost_rate())*cs),meta['lot_step'],meta['min_contracts'])
                    if target<=0:return result('blocked','quantity_limits')
                    qty=entry_qty(candidate,target,meta['lot_step'],meta['min_contracts'])
                    if qty<=0:return result('blocked','quantity_limits')
                state=new_position(pid,candidate['side'],price,initial_r,target,qty,risk_budget)
                if candidate.get('decision_source')=='gemini_event':
                    g=candidate['event_decision']
                    state.update(entry_fraction=candidate['entry_fraction'],entry_thesis=g['thesis'],
                        entry_event_ids=candidate['event_ids'],next_confirmation_price=g.get('next_confirmation_price'),
                        invalidation_price=g.get('invalidation_price'))
                state.update(filled_qty='0',stop_price=str(stop),take_profit=tp,owned_stop_ids=[],entry_risk_used='0',contract_size=str(cs),cost_rate=str(self.cost_rate()),
                             decision_source=candidate.get('decision_source','rule_candidate'),opened_ms=self.clock())
                old=self.store.state(self.symbol)
                if not self.store.save_state(self.symbol,state,old['revision'] if old else None):
                    return result('blocked','state_conflict')
                state=self.store.state(self.symbol)
                intent=dict(id=digest([candidate['id'],'ENTRY'])[:32],position_id=pid,side=candidate['side'],
                    kind='ENTRY',qty=qty,bar_ms=candidate['signal_ms'],expected_revision=state['revision'])
                out=self._execute(intent,candidate,approval)
                if out['status']=='blocked' and not self.store.pending(self.symbol):
                    failed=self.store.state(self.symbol)
                    if number(failed['filled_qty'])==0:
                        failed['phase']='FLAT'
                        self.store.save_state(self.symbol,failed,failed['revision'])
                return out
            except Exception as exc: return result('blocked',type(exc).__name__)

    def _event(self,event,candidate=None,approval=None):
        state=self.store.state(self.symbol); decision=advance(state,event,POLICY)
        if decision['intent']: return self._execute(decision['intent'],candidate,approval)
        if decision['state']!=state:
            self.store.save_state(self.symbol,decision['state'],state['revision'])
        return result('idle',decision['reason'])

    def risk_tick(self,snapshot):
        self.snapshot=snapshot
        with self.port.locked():
            try:
                if not self.enabled() or self.store.owner_record(self.symbol)['owner']!='unified':
                    return result('blocked','not_live_owner')
                if self.store.pending(self.symbol): return self.manager.reconcile(self.symbol)
                state=self.store.state(self.symbol)
                if not state or state['phase']=='FLAT': return result('idle','flat')
                if state.get('emergency_required'): return result('blocked','emergency_residual_manual_required')
                pos=self.port.position(self.symbol)
                if not pos: return self._external_flat()
                if not self.port.protection(self.symbol)['confirmed']:
                    return self.manual_close('protection_missing')
                if getattr(self.cfg,'CORE_EMERGENCY_CLOSE_ENABLED',True):
                    first,due=near_stop_confirmation(state,pos,self.clock())
                    self.store.patch_metadata(self.symbol,emergency_first_ms=first)
                    if due: return self.manual_close('sl_proximity_emergency_close')
                self._event(dict(kind='MARK',price=self.client.fetch_last_price()))
                if not validate_snapshot(snapshot,self.clock())[0]: return result('blocked','snapshot')
                flags=trend_flags(snapshot,state['side']); frames=snapshot['frames']
                meta=self.client.instrument_metadata(); sign=1 if state['side']=='long' else -1
                one=frames['1m']; invalid=(sign*(number(one[-1]['close'])-number(one[-1]['ema20']))<0
                    and sign*(number(one[-1]['macd'])-number(one[-2]['macd']))<0)
                if not is_ai_decision(state):
                    out=self._event(dict(kind='PROBE_INVALIDATED',confirmed=invalid,bar_ms=one[-1]['close_ms']))
                    if out['status']!='idle': return out
                self._tighten_break_even()
                return self._event(dict(kind='WEAK_5M',confirmed=flags['weak5'],weak_1h=flags['weak1'],
                    bar_ms=frames['5m'][-1]['close_ms'],lot=meta['lot_step'],minimum=meta['min_contracts']))
            except Exception as exc: return result('reconciling',type(exc).__name__)

    def _external_flat(self):
        state=self.store.state(self.symbol); evidence=[]; total=D(0)
        if self.store.pending(self.symbol): return result('reconciling','pending')
        for identifier in state.get('owned_stop_ids',[]):
            answer=self.client.exchange.private_get_trade_order_algo({'algoId':identifier})
            if str(answer.get('code'))!='0': return result('reconciling','algo_history_unknown')
            for row in answer.get('data',[]):
                if row.get('algoId')!=identifier: return result('reconciling','algo_identity')
                if row.get('state') not in ('effective','canceled','order_failed'): return result('reconciling','algo_not_terminal')
                order_id=row.get('ordId')
                if row.get('state')!='effective': continue
                if not order_id: return result('reconciling','triggered_order_unknown')
                order=self.client.exchange.fetch_order(order_id,self.symbol)
                if (order.get('id')!=order_id or order.get('symbol')!=self.symbol or
                        order.get('status')!='closed' or number(order['remaining'])!=0 or
                        order.get('side')!=('sell' if state['side']=='long' else 'buy')):
                    return result('reconciling','triggered_order_not_terminal')
                trades=self.client.exchange.fetch_my_trades(self.symbol,
                    since=int(state['exchange_identity'].split(':')[-2]),limit=100,params={'ordId':order_id})
                ids=set(); filled=D(0)
                for trade in trades:
                    if not trade.get('id') or trade.get('order')!=order_id or trade.get('symbol')!=self.symbol:
                        return result('reconciling','triggered_fill_identity')
                    if trade['id'] in ids: continue
                    ids.add(trade['id']); filled+=number(trade['amount'],positive=True)
                    evidence.append(dict(order_id=order_id,trade_id=trade['id'],qty=trade['amount'],price=trade['price'],filled_ms=trade.get('timestamp')))
                if filled!=number(order['filled']): return result('reconciling','triggered_fills_incomplete')
                total+=filled
        if total!=number(state['filled_qty']): return result('reconciling','external_close_unexplained')
        if not self.port.cleanup_owned(self.symbol,{}).get('confirmed') or self.port.raw_position() is not None:
            return result('reconciling','external_cleanup_unknown')
        self.store.external_flat(self.symbol,state['revision'],evidence)
        return result('complete','external_protection_flat')

    def _tighten_break_even(self):
        state=self.store.state(self.symbol)
        if state.get('reduce_stage',0)<1: return
        pos=self.port.position(self.symbol)
        if not pos: return
        sign=1 if state['side']=='long' else -1
        entry=number(pos['entry_price'],positive=True)
        proposed=number(self.client.exchange.price_to_precision(self.symbol,float(entry*(1+sign*D('.001')))),positive=True)
        current=number(state['stop_price']); mark=number(self.client.fetch_last_price(),positive=True)
        if sign*(proposed-current)<=0 or sign*(mark-proposed)<=0: return
        ids=set(state.get('owned_stop_ids',[]))
        rows=self.client.fetch_pending_protection_orders()
        for row in rows:
            if row.get('algoId') not in ids: continue
            # Preserve a stronger actual stop even if persisted target is older.
            if sign*(proposed-number(row['slTriggerPx']))<=0: continue
            key=digest([state['position_id'],'BE',row['algoId'],str(proposed)])[:32]
            if self.store.reserve_action(self.symbol,key,dict(kind='STOP_TIGHTEN')):
                self.client.amend_protective_stop(row['algoId'],new_sl_price=float(proposed))
        proof=owned_protection(self.port.raw_position(),self.client.fetch_pending_protection_orders(),
            instrument_id=self.client.exchange.market(self.symbol)['id'],owned_ids=ids,stop_price=proposed)
        if proof['confirmed']: self.store.patch_metadata(self.symbol,stop_price=str(proposed))

    def management(self,candidate,approval,snapshot):
        self.snapshot=snapshot
        with self.port.locked():
            try:
                reason=self.review_settings_guard(candidate)
                if reason: return result('blocked',reason)
                out=self.risk_tick(snapshot)
                if out['status']!='idle': return out
                state=self.store.state(self.symbol)
                periodic=candidate.get('decision_source')=='gemini_periodic'
                ai=is_ai_decision(candidate)
                reason=self.event_decision_guard(candidate)
                if reason:return result('blocked',reason)
                held=state and state['phase']!='FLAT' and number(state['filled_qty'])>0
                if ai:
                    if candidate.get('decision_position_id')!=(state['position_id'] if held else None):
                        return result('blocked','decision_position_changed')
                    if held and periodic:
                        opened=state.get('opened_ms')
                        if opened is None:
                            identity=state.get('exchange_identity','').split(':')
                            opened=int(identity[-2]) if len(identity)>=3 else None
                        if opened is None: return result('blocked','position_age_unknown')
                        if self.clock()-opened < float(getattr(self.cfg,'MIN_HOLD_MINUTES',15))*60000:
                            return result('blocked','minimum_hold')
                if not state or state['phase']=='FLAT': return self.entry(candidate,approval,snapshot)
                price=number(self.client.fetch_last_price(),positive=True)
                ok,why=approval_valid(candidate,approval,self.clock(),price)
                if not ok: return result('blocked',why)
                if candidate.get('purpose')=='CLOSE':
                    return self._event(dict(kind='REVERSE_APPROVED',approved=True,bar_ms=candidate['signal_ms']))
                if candidate.get('purpose')=='REDUCE':
                    meta=self.client.instrument_metadata()
                    return self._event(dict(kind='AI_REDUCE_APPROVED',approved=True,
                        bar_ms=candidate['signal_ms'],lot=meta['lot_step'],minimum=meta['min_contracts']))
                if candidate['side']!=state['side']:
                    from copy import deepcopy
                    self.pending_reverse=(deepcopy(candidate),deepcopy(approval),state['position_id'])
                    out=self._event(dict(kind='REVERSE_APPROVED',approved=True,bar_ms=candidate['signal_ms']))
                    if out['status']=='blocked': self.pending_reverse=None
                    return self.continue_reverse(snapshot) if out['status']=='complete' else out
                flags=({'confirmed5':True,'opposed1':False} if ai else trend_flags(snapshot,state['side']))
                meta=self.client.instrument_metadata()
                pos=self.port.position(self.symbol); cs=number(meta['contract_size'],positive=True)
                sign=1 if state['side']=='long' else -1
                # Existing CORE round-trip fee assumption, applied to actual average entry.
                entry=number(pos['entry_price'],positive=True)
                net=number(pos['contracts'])*cs*(sign*(price-entry)-entry*D('.001'))
                higher=ai or all(sign*(number(snapshot['frames'][tf][-1]['close'])-
                    number(snapshot['frames'][tf][-1]['ema20']))>0 and
                    sign*(number(snapshot['frames'][tf][-1]['ema20'])-
                    number(snapshot['frames'][tf][-1]['ema50']))>0 for tf in ('4h','1d'))
                unit_risk=(abs(price-number(state['stop_price']))+price*self.cost_rate())*cs
                return self._event(dict(kind='TREND_CONFIRMED',confirmed=flags['confirmed5'],approved=True,
                    one_h_opposed=flags['opposed1'],higher_confirmed=higher,net_profit=net,
                    risk_available_qty=self.risk_remaining(price)/unit_risk,
                    lot=meta['lot_step'],minimum=meta['min_contracts'],
                    bar_ms=candidate['signal_ms'] if ai else snapshot['frames']['5m'][-1]['close_ms']),candidate,approval)
            except Exception as exc: return result('blocked',type(exc).__name__)

    def continue_reverse(self,snapshot):
        """Reuse one fresh approval only after the old lifecycle is confirmed flat.

        In-memory permission is deliberately lost on restart. Unknown or partial
        exits never create an opposite entry; the existing risk loop reconciles.
        """
        if self.pending_reverse is None: return result('idle','no_reverse')
        with self.port.locked():
            candidate,approval,old_id=self.pending_reverse
            if (not self.enabled() or self.review_settings_guard(candidate) or
                    not candidate['signal_ms']<=self.clock()<candidate['expires_ms']):
                self.pending_reverse=None
                return result('blocked','reverse_expired_or_controls_changed')
            state=self.store.state(self.symbol)
            if not state or state['position_id']!=old_id:
                self.pending_reverse=None
                return result('blocked','reverse_lifecycle_changed')
            if self.store.pending(self.symbol): return result('reconciling','reverse_exit_pending')
            if state['phase']!='FLAT' or number(state['filled_qty'])!=0:
                self.pending_reverse=None
                return result('blocked','reverse_exit_incomplete')
            try:
                return self.entry(candidate,approval,snapshot)
            finally:
                self.pending_reverse=None

    def manual_close(self,reason):
        with self.port.locked():
            self.pending_reverse=None
            self.manual_active=True
            try:
                if not self.enabled() or self.store.owner_record(self.symbol)['owner']!='unified':
                    return result('blocked','not_live_owner')
                if self.store.pending(self.symbol): return self.manager.reconcile(self.symbol)
                state=self.store.state(self.symbol)
                if not state or number(state['filled_qty'])==0: return result('idle','flat')
                intent=dict(id=digest([state['position_id'],state['revision'],'EXIT',reason])[:32],
                    position_id=state['position_id'],side=state['side'],kind='EXIT',qty=state['filled_qty'],
                    expected_revision=state['revision'],bar_ms=self.clock(),reason=reason)
                return self._execute(intent)
            except Exception as exc: return result('reconciling',type(exc).__name__)
            finally: self.manual_active=False

    def status(self):
        try:
            state=self.store.state(self.symbol)
            if state and number(state.get('filled_qty',0))>0:
                from core_unified_accounting import position_cost_context
                try:
                    state.update(position_cost_context(self.store.accounting_records(),self.symbol,state,self.clock()))
                except (ValueError,KeyError,TypeError,ArithmeticError):
                    state['cost_basis_known']=False
            return dict(owner=self.store.owner_record(self.symbol)['owner'],state=state,
                        pending=self.store.pending(self.symbol),enabled=self.enabled())
        except Exception as exc: return dict(owner='unknown',error=type(exc).__name__,enabled=False)

    def close(self):
        self.closed=True
