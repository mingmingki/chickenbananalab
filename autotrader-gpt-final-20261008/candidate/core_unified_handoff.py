"""Durable handoff coordinator. No exchange mutation is exposed here."""
from core_unified_policy import POLICY_VERSION


class Handoff:
    def __init__(self, store, port, clock_ms):
        self.store,self.port,self.clock = store,port,clock_ms

    def transfer(self,symbol,*,source,target):
        if {source,target}!={'legacy','unified'}:
            return dict(ok=False,reason='invalid_transition')
        with self.port.locked():
            try:
                owner=self.store.owner_record(symbol)
                if owner['owner']!=source:
                    return dict(ok=False,reason='owner_changed')
                if self.store.pending(symbol):
                    return dict(ok=False,reason='unified_pending')
            except Exception:
                return dict(ok=False,reason='ledger_unknown')
            try:
                # Read again after all order/protection queries: a position may
                # have changed while collecting the first inventory.
                first=self.port.inventory(symbol)
                second=self.port.inventory(symbol)
                now=self.clock()
                for inventory in (first,second):
                    if (inventory['symbol']!=symbol or inventory['core_owns_symbol'] is not True
                        or type(now) is not int or type(inventory['observed_ms']) is not int
                        or not 0<=now-inventory['observed_ms']<=5000):
                        return dict(ok=False,reason='inventory_unknown')
                    if (inventory['position'] is not None or inventory['orders']!=[]
                        or inventory['protections']!=[] or inventory['legacy_pending'] is not False):
                        return dict(ok=False,reason='not_quiescent')
            except Exception:
                return dict(ok=False,reason='inventory_unknown')
            try:
                generation=self.store.transfer_owner(symbol,source,target,owner['generation'],
                    dict(version=POLICY_VERSION,observed_ms=now,flat=True,
                         no_orders=True,no_protections=True,no_legacy_pending=True))
                if generation is None:
                    return dict(ok=False,reason='state_changed')
                return dict(ok=True,reason='transferred',owner=target,generation=generation)
            except Exception:
                return dict(ok=False,reason='ledger_unknown')

    def owner(self,symbol):
        # Never silently fall back to legacy if the ownership database is unreadable.
        return self.store.owner_record(symbol)['owner']
