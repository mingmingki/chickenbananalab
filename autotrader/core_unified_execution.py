"""Serialized intent executor. Exchange access is injected at the port boundary."""
from copy import deepcopy
import time
from core_unified_policy import approval_valid, floor_qty, number


def result(status, reason):
    return {'status': status, 'reason': reason}


class ExecutionManager:
    def __init__(self, store, port, clock_ms):
        self.store, self.port, self.clock = store, port, clock_ms

    def execute(self, symbol, intent):
        # No exceptions outside this boundary cause an automatic submission retry.
        with self.port.locked():
            try:
                if self.store.pending(symbol):
                    return self._reconcile_locked(symbol)
                i = deepcopy(intent)
                state = self.store.state(symbol)
                check = self.port.preflight(symbol, i)
                if check.get('allowed') is not True:
                    return result('blocked', check.get('reason', 'preflight'))
                if (not state or state['revision'] != i['expected_revision'] or
                    check['revision'] != state['revision'] or
                    i['position_id'] != state.get('position_id') or
                    check['position_id'] != state.get('position_id') or
                    i['side'] not in ('long','short') or i['side'] != state.get('side')):
                    return result('blocked', 'identity_or_revision')
                if i['kind'] not in ('ENTRY', 'ADD', 'REDUCE', 'EXIT'):
                    return result('blocked', 'unknown_intent')
                qty = number(i['qty'], positive=True)
                if floor_qty(qty,check['lot'],check['minimum']) != qty:
                    return result('blocked', 'quantity')
                position = self.port.position(symbol)
                if i['kind'] == 'ENTRY':
                    if position is not None or number(state['filled_qty']) != 0:
                        return result('blocked', 'not_flat')
                elif (not position or position['position_id'] != i['position_id'] or
                      position['side'] != i['side'] or
                      number(position['contracts']) != number(state['filled_qty'])):
                    return result('blocked', 'position_changed')
                if i['kind'] in ('ENTRY', 'ADD'):
                    now = self.clock()
                    if (i['candidate'].get('symbol') != symbol or
                            i['candidate'].get('side') != i['side']):
                        return result('blocked','approval_scope')
                    if (type(check.get('quote_ms')) is not int or
                            not 0 <= now-check['quote_ms'] <= 5000 or
                            check.get('signal_valid') is not True or
                            check.get('generation') != i['candidate']['generation']):
                        return result('blocked', 'stale_signal_or_quote')
                    ok, reason = approval_valid(i['candidate'],i['approval'],now,check['latest_price'])
                    if not ok:
                        return result('blocked', reason)
                    if number(i['risk_required'],positive=True) > number(check['risk_remaining']):
                        return result('blocked', 'risk_budget')
                elif qty > number(position['contracts']):
                    return result('blocked', 'over_reduce')
                if not self.store.reserve(symbol,i,state['revision']):
                    return result('blocked', 'reservation_conflict')
            except Exception as exc:
                return result('blocked', 'preflight_'+type(exc).__name__)
            try:
                self.port.submit(symbol,i)
            except Exception:
                # The exchange may have filled even when submit raised.
                # Reservation remains durable; only lookup may resolve it.
                pass
            return self._reconcile_locked(symbol)

    def reconcile(self, symbol):
        with self.port.locked():
            return self._reconcile_locked(symbol)

    def _protect_actual_exposure(self,symbol,intent):
        """Partial/unknown orders may already have exposure: terminal is irrelevant."""
        deadline=time.monotonic()+3
        for attempt in range(1,4):
            stage='position_identity'
            try:
                position = self.port.position(symbol)
                if position is None:
                    return True
                if (position['position_id'] != intent['position_id'] or
                        position['side'] != intent['side']):
                    # Do not amend/close another owner's new position.
                    return False
                stage='protection_read'
                check=self.port.protection(symbol)
                if check.get('confirmed') is not True:
                    stage='protection_install'
                    # Production port durably reserves this exact action before
                    # writing. A retry only discovers its existing exchange ID;
                    # it never resends an ambiguous placement.
                    self.port.install_protection(symbol,intent)
                stage='protection_confirm'
                check=self.port.protection(symbol)
                if check.get('confirmed') is True:
                    if attempt>1: self._protection_diagnostic(symbol,stage,attempt,'confirmed')
                    return True
                reason=check.get('reason','protection_unconfirmed')
            except Exception as exc:
                # Record a safe code, never raw exchange exceptions/credentials.
                value=str(exc)
                reason=value if isinstance(exc,ValueError) and value.replace('_','').isalpha() and len(value)<100 else type(exc).__name__
            self._protection_diagnostic(symbol,stage,attempt,reason)
            remaining=deadline-time.monotonic()
            if attempt==3 or remaining<=0: break
            time.sleep(min(.15,remaining))
        try:
            self.port.protection_failure(symbol,intent)
        except Exception:
            # Reservation remains unresolved even if escalation is unavailable.
            pass
        return False

    def _protection_diagnostic(self,symbol,stage,attempt,reason):
        record=getattr(self.port,'record_protection_check',None)
        if record:
            try: record(symbol,stage,attempt,reason)
            except Exception: pass

    def _reconcile_locked(self, symbol):
        try:
            i = self.store.pending(symbol)
            if i is None:
                return result('idle', 'no_pending')
            protected = self._protect_actual_exposure(symbol,i)
            order = self.port.lookup(i['id'])
            if not order or order.get('client_id') != i['id']:
                return result('reconciling', 'order_unknown')
            trades = order['trades']
            for fill in trades:
                if not fill.get('id'):
                    return result('reconciling', 'fill_identity_missing')
                self.store.apply_fill(i['id'],fill['id'],fill['qty'],fill['price'],filled_ms=fill.get('filled_ms'))
            pending = self.store.pending(symbol)
            if number(order['filled']) != number(pending['filled']):
                return result('reconciling', 'fill_history_incomplete')
            if order.get('terminal') is not True or number(order['remaining']) != 0:
                return result('reconciling', 'order_not_terminal')
            self.store.mark_terminal(i['id'],order['filled'])
            emergency=getattr(self.port,'reconcile_emergency',None)
            if emergency:
                outcome=emergency(symbol,i)
                if outcome is not None: return outcome
            if not protected:
                return result('reconciling','protection_failed_or_identity_unknown')
            state = self.store.state(symbol)
            position = self.port.position(symbol)
            if number(state['filled_qty']) == 0:
                if position is not None:
                    return result('reconciling', 'not_flat')
                if self.port.cleanup_owned(symbol,i).get('confirmed') is not True:
                    return result('reconciling', 'owned_cleanup_unknown')
                # Cleanup itself takes time; recheck before releasing the reservation.
                if self.port.position(symbol) is not None:
                    return result('reconciling', 'position_changed_during_cleanup')
            else:
                if (not position or position['position_id'] != i['position_id'] or
                    position['side'] != i['side'] or
                    number(position['contracts']) != number(state['filled_qty'])):
                    return result('reconciling', 'position_mismatch')
                if not self._protect_actual_exposure(symbol,i):
                    return result('reconciling', 'protection_failed')
            self.store.complete(i['id'],protection_confirmed=True,terminal_partial_confirmed=True)
            return result('complete', 'reconciled')
        except Exception as exc:
            return result('reconciling', type(exc).__name__)
