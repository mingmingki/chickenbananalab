"""Read-only trade-history projection of the durable CORE fill journal.

One row per exit intent, not per exchange fill. Replaying restores historical
probe exits without writing duplicate legacy records or touching live orders.
Entry/exit fees use exact order IDs when the reporting ledger has caught up;
otherwise they are explicitly estimated. Funding is not included here.
"""
import json
import sqlite3
from collections import defaultdict
from contextlib import closing
from datetime import datetime
from decimal import Decimal as D
from pathlib import Path

from core_unified_accounting import KST, realized_for_day
from core_unified_policy import number


def _order_for(row, orders, totals):
    inst = row['symbol'].split('/')[0] + '-USDT-SWAP'
    matches = [o for o in orders if o.get('inst_id') == inst and
               row['intent_id'] in (o.get('order_id'), o.get('client_order_id'))]
    if len(matches) != 1:
        return None
    order = matches[0]
    try:
        qty = number(order['qty'], positive=True)
        if abs(qty-totals[(row['symbol'],row['intent_id'])]) > max(D('1e-12'),qty*D('1e-9')):
            return None
        if order.get('fee_ccy') != 'USDT' or order.get('valuation_error'):
            return None
        number(order['fee_usdt'])
    except (KeyError, ValueError, TypeError):
        return None
    return order


def project_records(rows, orders):
    if not rows:
        return []
    # Same validation and inventory invariants as the loss gate; never fabricate
    # zero-profit records when basis/identity is unknown.
    now = int(datetime.now().timestamp()*1000)
    realized_for_day(rows, datetime.fromtimestamp(now/1000,KST).date(), now)
    unique = {(r['symbol'],r['trade_id']):r for r in rows}
    rows = sorted(unique.values(), key=lambda r:r['filled_ms'])
    totals = defaultdict(lambda:D(0))
    for r in rows:
        totals[(r['symbol'],r['intent_id'])] += number(r['qty'])
    inventory, events = {}, {}
    for r in rows:
        qty,price,cs = (number(r[k],positive=True) for k in ('qty','price','contract_size'))
        key = (r['symbol'],r['position_id'])
        s = inventory.setdefault(key,dict(qty=D(0),cost=D(0),fee=D(0),
            actual=True,added=False,entry_ms=r['filled_ms'],margin=D(0),margin_known=True))
        order = _order_for(r,orders,totals)
        actual = order is not None
        fee = (-number(order['fee_usdt'])*qty/number(order['qty']) if actual
               else qty*price*cs*number(r.get('fee_rate','.001'))/2)
        if r['kind'] in ('ENTRY','ADD'):
            s['qty'] += qty
            s['cost'] += qty*price
            s['fee'] += fee
            s['actual'] = s['actual'] and actual
            s['added'] = s['added'] or r['kind']=='ADD'
            try:
                s['margin'] += number(order['margin_usdt'],positive=True)*qty/number(order['qty'])
            except (KeyError,TypeError,ValueError):
                s['margin_known'] = False
            continue
        average = s['cost']/s['qty']
        entry_fee = s['fee']*qty/s['qty']
        gross = qty*cs*(price-average)*(1 if r['side']=='long' else -1)
        event_key = (*key,r['intent_id'])
        e = events.setdefault(event_key,dict(type='reduce',symbol=r['symbol'],side=r['side'],
            position_id=r['position_id'],intent_id=r['intent_id'],
            execution_id='core_unified:'+r['symbol']+':'+r['position_id']+':'+r['intent_id'],
            history_source='core_unified',strategy_group='core',dry_run=False,
            qty=D(0),basis=D(0),exit_value=D(0),pnl=D(0),fee=D(0),actual=True,
            entry_ms=s['entry_ms'],trade_ids=[],notional_usdt=D(0),contract_size=float(cs)))
        e['qty'] += qty
        e['basis'] += average*qty
        e['exit_value'] += price*qty
        e['pnl'] += gross
        e['fee'] += entry_fee+fee
        e['actual'] = e['actual'] and actual and s['actual']
        e['trade_ids'].append(r['trade_id'])
        e['notional_usdt'] += average*qty*cs
        e['time'] = datetime.fromtimestamp(r['filled_ms']/1000,KST).replace(tzinfo=None).isoformat(timespec='seconds')
        e['exchange_order_id'] = order['order_id'] if actual else None
        e['fixed_margin_usdt'] = float(s['margin']) if s['margin_known'] else None
        s['qty'] -= qty
        s['cost'] -= average*qty
        s['fee'] -= entry_fee
        if not s['qty']:
            e['type'] = 'close'
        e['reason'] = ('추가진입 후 전량 청산' if s['added'] else '25% 선행진입 전량 청산') if e['type']=='close' else 'CORE 부분 감축'
    out = []
    for e in events.values():
        qty = e.pop('qty')
        e['amount'] = float(qty)
        e['entry_price'] = float(e.pop('basis')/qty)
        e['close_price'] = float(e.pop('exit_value')/qty)
        e['fee_source'] = 'okx_order_fees' if e.pop('actual') else 'estimated'
        e['pnl_source'] = 'confirmed_fills'
        e['okx_net_pnl'] = None
        e['funding_fee'] = None
        e['reason'] += ' · 펀딩비 별도' if e['fee_source']=='okx_order_fees' else ' · 수수료 추정/펀딩비 별도'
        for k in ('pnl','fee','notional_usdt'):
            e[k] = float(e[k])
        e['fixed_return_pct'] = e['pnl']/e['fixed_margin_usdt']*100 if e['fixed_margin_usdt'] else None
        out.append(e)
    return sorted(out,key=lambda r:(r['time'],r['execution_id']))


def load_records(user_dir):
    path = Path(user_dir)/'core_unified.sqlite3'
    if not path.exists():
        return []
    # Read-only snapshot: no schema migrations, state writes, or exchange calls.
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=5)) as db:
        rows = [json.loads(r[0]) for r in db.execute('SELECT payload FROM accounting ORDER BY rowid')]
    try:
        orders = json.loads((Path(user_dir)/'okx_margin_return_state.json').read_text()).get('orders',[])
    except FileNotFoundError:
        orders = []
    return project_records(rows, orders)
