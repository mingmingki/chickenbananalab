"""Deterministic exchange simulator; never imports credentials or network code."""
from contextlib import contextmanager
from copy import deepcopy
from decimal import Decimal as D
from threading import RLock


class FakePort:
    def __init__(self, store, *, fill_then_timeout=False):
        self.store = store
        self.lock = RLock()
        self.submitted_ids, self.orders = [], {}
        self.fill_then_timeout = fill_then_timeout
        self.allowed = True
        self.fill_ratio = D(1)
        self.pos = None
        self.protected = True
        self.cleanup_ok = True
        self.killed = False
        self.quote = '100'
        self.available = True

    @contextmanager
    def locked(self):
        with self.lock:
            yield

    def preflight(self, symbol, intent):
        state = self.store.state(symbol)
        return dict(allowed=self.allowed, reason='paused', revision=state['revision'],
                    position_id=state.get('position_id'), latest_price=self.quote,
                    lot='1', minimum='1', risk_remaining='100',
                    signal_valid=True, generation=1, quote_ms=1000)

    def submit(self, symbol, intent):
        assert self.store.pending(symbol)['id'] == intent['id']
        self.submitted_ids.append(intent['id'])
        qty = D(intent['qty']) * self.fill_ratio
        old = D(self.pos['contracts']) if self.pos else D(0)
        reducing = intent['kind'] in ('REDUCE', 'EXIT')
        remaining = old - qty if reducing else old + qty
        if remaining < 0:
            raise AssertionError('not_reduce_only')
        self.pos = dict(position_id=intent['position_id'], side=intent['side'],
                        contracts=str(remaining)) if remaining else None
        self.orders[intent['id']] = dict(
            client_id=intent['id'], terminal=qty == D(intent['qty']),
            filled=str(qty), remaining=str(D(intent['qty'])-qty),
            trades=[dict(id='fill-1',qty=str(qty),price='100')] if qty else [])
        if self.fill_then_timeout:
            raise TimeoutError('response_lost_after_fill')
        return {'id': intent['id']}

    def lookup(self, intent_id):
        if not self.available:
            return None
        return deepcopy(self.orders.get(intent_id))

    def position(self, symbol):
        return deepcopy(self.pos)

    def protection(self, symbol):
        return dict(confirmed=self.protected)

    def install_protection(self, symbol, intent):
        return dict(confirmed=self.protected)

    def cleanup_owned(self, symbol, intent):
        return dict(confirmed=self.cleanup_ok)

    def protection_failure(self, symbol, intent):
        # Simulator for the production kill/escalation protocol, not another order.
        self.killed = True
