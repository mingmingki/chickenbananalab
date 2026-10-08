"""Read-only bridge to existing OKX clients and legacy CORE state files."""
import json
from pathlib import Path
from core_unified_policy import number
from core_unified_okx_contract import normalize_order


class LegacyInventoryPort:
    def __init__(self,cfg,client,clock_ms,lock_factory=None):
        self.cfg,self.client,self.clock,self.lock_factory=cfg,client,clock_ms,lock_factory

    def locked(self):
        if self.lock_factory is not None: return self.lock_factory()
        from candidate_c_hybrid_ownership import account_order_lock
        return account_order_lock(self.cfg.user_dir)

    def inventory(self,symbol):
        if symbol!=self.client.symbol:
            raise ValueError('client_symbol_mismatch')
        started=self.clock()
        raw=self.client.exchange.fetch_positions([symbol])
        if not isinstance(raw,list):
            raise ValueError('positions_unknown')
        active=[]
        for position in raw:
            if position.get('symbol')!=symbol:
                raise ValueError('position_symbol_mismatch')
            contracts=number(position['contracts'])
            if contracts<0:
                raise ValueError('invalid_position_quantity')
            if contracts>0:
                if position.get('side') not in ('long','short'):
                    raise ValueError('position_side_unknown')
                active.append(position)
        orders=self.client.exchange.fetch_open_orders(symbol)
        protections=self.client.fetch_pending_protection_orders()
        if not isinstance(orders,list) or not isinstance(protections,list):
            raise ValueError('orders_unknown')
        pending=False
        for filename in ('reduce_v2_state.json','core_add_position_state.json'):
            record=self._record(filename,symbol)
            if record is not None and record.get('pending_order') is not None:
                pending=True
        manual=self._record('core_manual_close.json',symbol)
        settled_manual = manual is not None and (
            manual.get('status')=='completed' or
            (manual.get('status')=='confirmed' and manual.get('confirmed_at') is not None
             and manual.get('release_at') is not None and manual.get('journaled') is True))
        # A confirmed, journaled close may still have an ENTRY cooldown. Preserve
        # that record for legacy entry validation; it is not an unresolved order.
        if manual is not None and (not settled_manual or manual.get('cleanup_pending')):
            pending=True
        core=set(self.cfg.ENABLED_SYMBOLS)
        candidate=set(self.cfg.CANDIDATE_C_SYMBOLS)
        return dict(symbol=symbol,position=active if active else None,orders=orders,
                    protections=protections,legacy_pending=pending,
                    core_owns_symbol=symbol in core and not bool(core & candidate),observed_ms=started)

    def _record(self,filename,symbol):
        try:
            with (Path(self.cfg.user_dir)/filename).open(encoding='utf-8') as source:
                data=json.load(source)
        except FileNotFoundError:
            return None
        if not isinstance(data,dict):
            raise ValueError('legacy_ledger_shape')
        record=data.get(symbol)
        if record is not None and not isinstance(record,dict):
            raise ValueError('legacy_record_shape')
        return record

    def lookup(self,client_id,created_ms):
        if (not isinstance(client_id,str) or not client_id or type(created_ms) is not int or
                not 0<=created_ms<=self.clock()):
            raise ValueError('lookup_identity_or_time')
        # The legacy wrapper replaces clientOrderId with the requested value.
        # Inspect the actual exchange response instead, to retain identity proof.
        order=self.client.exchange.fetch_order('',self.client.symbol,params={'clOrdId':client_id})
        if order is None:
            return None
        if order.get('clientOrderId')!=client_id or not order.get('id'):
            raise ValueError('exchange_order_identity_mismatch')
        trades=self.client.exchange.fetch_my_trades(self.client.symbol,since=created_ms,
                                                   limit=100,params={'ordId':order['id']})
        # An incomplete page is never fabricated as a complete order. Caller keeps
        # reservation pending; pagination may be added without changing this contract.
        return normalize_order(order,trades,client_id=client_id,symbol=self.client.symbol)
