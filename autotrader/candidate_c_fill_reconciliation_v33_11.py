"""v33-11 exact Candidate C reduce-fill evidence.

No matching by proximity. A complete owned terminal reduce-only order and
all of its uniquely identified OKX fills are mandatory. Funding and entry
fees are *not* attributed here, so net is 'ex_funding_and_entry_fees'.
"""
from decimal import Decimal,InvalidOperation
import math

def d(v,*,positive=False):
    if isinstance(v,bool) or v is None:
        return None
    try:
        n=Decimal(str(v))
        if not n.is_finite() or (positive and n<=0):
            return None
        return n
    except (InvalidOperation,ValueError,TypeError):
        return None

def verified_reduce(symbol,side,entry_price,contracts,contract_size,
                    order_id,client_order_id,order,fills):
    if side not in ('long','short') or not symbol or not order_id or not client_order_id:
        return None
    entry=d(entry_price,positive=True)
    qty=d(contracts,positive=True)
    unit=d(contract_size,positive=True)
    if None in (entry,qty,unit) or not isinstance(order,dict) or not isinstance(fills,list):
        return None
    raw=order.get('info') or {}
    oid=str(order_id);cid=str(client_order_id)
    if (str(raw.get('ordId') or order.get('id') or '')!=oid
        or str(raw.get('clOrdId') or '')!=cid
        or str(raw.get('reduceOnly')).lower()!='true'
        or order.get('status') not in ('closed','filled')
        or str(raw.get('state') or '') not in ('filled','')
        or str(raw.get('side') or order.get('side') or '')!=('buy' if side=='short' else 'sell')):
        return None
    filled=d(order.get('filled'),positive=True)
    if filled is None or abs(filled-qty)>Decimal('.00000001'):
        return None
    if not fills or len(fills)>=100:
        return None
    observed={}
    for row in fills:
        if not isinstance(row,dict):return None
        info=row.get('info') or {}
        tid=str(info.get('tradeId') or row.get('id') or '')
        o=str(info.get('ordId') or row.get('order') or '')
        side_actual=str(info.get('side') or row.get('side') or '')
        if not tid or tid in observed or o!=oid or side_actual!=('buy' if side=='short' else 'sell'):
            return None
        if str(info.get('feeCcy') or '').upper()!='USDT':return None
        qty_i=d(info.get('fillSz') or row.get('amount'),positive=True)
        px_i=d(info.get('fillPx') or row.get('price'),positive=True)
        fee_signed=d(info.get('fee'))
        pnl_i=d(info.get('fillPnl'))
        if None in (qty_i,px_i,fee_signed,pnl_i):return None
        observed[tid]=(qty_i,px_i,fee_signed,pnl_i)
    volume=sum((z[0] for z in observed.values()),Decimal(0))
    if abs(volume-qty)>Decimal('.00000001'):
        return None
    vwap=sum((z[0]*z[1] for z in observed.values()),Decimal(0))/volume
    signed=(Decimal(1) if side=='long' else Decimal(-1))
    gross_exchange=sum((z[3] for z in observed.values()),Decimal(0))
    expected_gross=(vwap-entry)*volume*unit*signed
    if abs(expected_gross-gross_exchange)>max(Decimal('.00001'),abs(gross_exchange)*Decimal('.000001')):
        return None
    signed_fee=sum((z[2] for z in observed.values()),Decimal(0))
    net=gross_exchange+signed_fee
    return dict(order_id=oid,client_order_id=cid,trade_ids=sorted(observed),
        filled_contracts=float(volume),vwap=float(vwap),
        price_basis='verified_order_fills',
        realized_pnl_basis='okx_fill_pnl_plus_fee_ex_funding_and_entry_fees',
        gross_pnl=float(gross_exchange),
        fee=float(-signed_fee),net_ex_funding_and_entry_fees=float(net),
        fill_count=len(observed))

def fetch_verified_reduce(client,*,symbol,side,entry_price,contracts,contract_size,
                          order_id,client_order_id,since_ms):
    try:
        order=client.exchange.fetch_order(str(order_id),symbol)
        fills=client.fetch_trades_for_order(str(order_id),int(since_ms),limit=100)
        return verified_reduce(symbol,side,entry_price,contracts,contract_size,
                               order_id,client_order_id,order,fills)
    except Exception:
        return None
