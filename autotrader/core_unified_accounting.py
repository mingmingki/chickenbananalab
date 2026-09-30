"""Replay exact durable fills for the CORE daily realized-loss gate.

No writes, counters, credentials, or model/exchange clients. Legacy realized PNL
and unified PNL remain separate sources and are combined only at this boundary.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from core_unified_policy import number

KST = timezone(timedelta(hours=9))
ROUND_TRIP_FEE = Decimal('0.001')


def realized_for_day(records, target_date, now_ms):
    """Weighted average inventory per (symbol,lifecycle), including partial exits.

    Required records: trade_id, intent_id, symbol, position_id, kind, side,
    qty (contracts), price, contract_size, filled_ms (exchange timestamp).
    Missing cost basis or inconsistent metadata is an error, never zero PNL.
    """
    if type(now_ms) is not int or now_ms < 0:
        raise ValueError('clock')
    unique = {}
    for row in records:
        for key in ('trade_id','intent_id','symbol','position_id'):
            if not isinstance(row[key],str) or not row[key]:
                raise ValueError('accounting_identity')
        stamp = row['filled_ms']
        if type(stamp) is not int or not 0 <= stamp <= now_ms:
            raise ValueError('accounting_timestamp')
        identity = (row['symbol'],row['trade_id'])
        if identity in unique and unique[identity] != row:
            raise ValueError('conflicting_trade')
        unique[identity] = row
    inventory = {}
    total = Decimal(0)
    # Stable order retains exchange/store ordering for identical timestamps.
    for row in sorted(unique.values(), key=lambda r:r['filled_ms']):
        qty,price,cs = (number(row[k],positive=True) for k in ('qty','price','contract_size'))
        side,kind = row['side'],row['kind']
        if side not in ('long','short') or kind not in ('ENTRY','ADD','REDUCE','EXIT'):
            raise ValueError('accounting_kind')
        key = (row['symbol'],row['position_id'])
        state = inventory.setdefault(key,dict(qty=Decimal(0),cost=Decimal(0),side=side,cs=cs,closed=False))
        if state['side'] != side or state['cs'] != cs or state['closed']:
            raise ValueError('accounting_lifecycle')
        if kind in ('ENTRY','ADD'):
            if kind == 'ADD' and not state['qty']:
                raise ValueError('missing_entry_basis')
            state['qty'] += qty
            state['cost'] += qty*price
            continue
        if qty > state['qty']:
            raise ValueError('missing_entry_basis')
        average = state['cost']/state['qty']
        sign = Decimal(1) if side=='long' else Decimal(-1)
        # These are estimated net results, not claimed actual exchange fees.
        # Optional configured extra costs may only increase the round-trip floor.
        fee_rate = number(row.get('fee_rate', ROUND_TRIP_FEE))
        if fee_rate < ROUND_TRIP_FEE:
            raise ValueError('fee_floor')
        net = qty*cs*(sign*(price-average)-average*fee_rate)
        if datetime.fromtimestamp(row['filled_ms']/1000,KST).date() == target_date:
            total += net
        state['qty'] -= qty
        state['cost'] -= average*qty
        if not state['qty']:
            state['closed'] = True
    return total


def allow_new_entry(cfg, store, equity, clock_ms, *, baseline_reader=None, legacy_reader=None):
    """Read-only supplement to legacy DailyLossGuard; uncertainty blocks entry.

    The ordinary guard establishes today's baseline first. This helper never
    creates/resets that baseline and is safe to call without journal writes.
    """
    try:
        if baseline_reader is None:
            from risk_manager import _load_daily_baseline
            baseline_reader = _load_daily_baseline
        if legacy_reader is None:
            from pnl_reconciliation import realized_pnl_for_kst_date
            legacy_reader = lambda directory, group, day: realized_pnl_for_kst_date(
                directory, group, day, include_unified=False)
        now = clock_ms()
        if type(now) is not int or now < 0:
            return False
        today = datetime.fromtimestamp(now/1000,KST).date()
        status,baseline = baseline_reader(cfg.user_dir)
        if status != 'ok' or baseline['trading_date'] != today.isoformat():
            return False
        start = number(baseline['start_equity'],positive=True)
        current = number(equity,positive=True)
        account_limit = number(cfg.ACCOUNT_HARD_DAILY_LOSS_PCT,positive=True)
        group_limit = number(cfg.MAX_DAILY_LOSS_PCT,positive=True)
        if (start-current)/start*100 >= account_limit:
            return False
        legacy = number(legacy_reader(cfg.user_dir,'core',today))
        unified = realized_for_day(store.accounting_records(),today,now)
        return -(legacy+unified)/start*100 < group_limit
    except Exception:
        # Source, schema, date, or numeric uncertainty must not grant new risk.
        return False


def journal_complete(store, symbol, position_id, *, clock_ms=None):
    """Proof that a fully closed lifecycle has a replayable durable fill journal.

    Accounting-record coverage of the fills table is enforced by Store. This
    does not assert that a legacy trade_log close record was written.
    """
    try:
        if clock_ms is None:
            import time
            clock_ms = lambda: int(time.time()*1000)
        now = clock_ms()
        rows = [r for r in store.accounting_records()
                if r['symbol']==symbol and r['position_id']==position_id]
        if not rows:
            return False
        realized_for_day(rows, datetime.fromtimestamp(now/1000,KST).date(), now)
        unique = {(r['symbol'],r['trade_id']):r for r in rows}
        balance = sum((number(r['qty']) * (1 if r['kind'] in ('ENTRY','ADD') else -1)
                       for r in unique.values()),Decimal(0))
        return balance == 0 and any(r['kind'] in ('REDUCE','EXIT') for r in rows)
    except Exception:
        return False


def position_cost_context(records, symbol, position, now_ms):
    """Read-only remaining weighted inventory and estimated unallocated entry fees."""
    rows=[r for r in records if r['symbol']==symbol and r['position_id']==position['position_id']]
    realized_for_day(rows,datetime.fromtimestamp(now_ms/1000,KST).date(),now_ms)
    rows=list({(r['symbol'],r['trade_id']):r for r in rows}.values())
    qty=cost=fees=Decimal(0)
    cs=None
    rate=ROUND_TRIP_FEE/2
    for row in sorted(rows,key=lambda r:r['filled_ms']):
        q=number(row['qty'],positive=True);p=number(row['price'],positive=True)
        cs=number(row['contract_size'],positive=True)
        if row['side']!=position['side']: raise ValueError('side_mismatch')
        fee=max(ROUND_TRIP_FEE,number(row.get('fee_rate',ROUND_TRIP_FEE)))/2
        rate=max(rate,fee)
        if row['kind'] in ('ENTRY','ADD'):
            qty+=q;cost+=q*p;fees+=q*p*cs*fee
        else:
            fraction=(qty-q)/qty
            cost*=fraction;fees*=fraction;qty-=q
    if qty<=0 or qty!=number(position['filled_qty']): raise ValueError('inventory_mismatch')
    return dict(cost_basis_known=True,average_entry_price=str(cost/qty),
                remaining_entry_fee_estimate_usdt=str(fees),contract_size=str(cs),
                estimated_fee_rate_per_side=str(rate))
