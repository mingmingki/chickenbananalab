"""Order-ID-bound verified CORE close fill VWAP; read-only."""
from decimal import Decimal,InvalidOperation
import math

def num(value,positive=False):
    if value is None or isinstance(value,bool):raise ValueError('missing')
    val=Decimal(str(value))
    if not val.is_finite() or (positive and val<=0):raise ValueError('invalid')
    return val

def identity(unified,raw=None):
    if unified is not None and raw is not None and str(unified)!=str(raw):
        raise ValueError('id_conflict')
    out=unified if unified is not None else raw
    if not out or isinstance(out,bool):raise ValueError('id_missing')
    return str(out)

def verified_close_fills(client,position,receipt,*,start_ms):
    try:
        return _verified(client,position,receipt,start_ms)
    except (AttributeError,ValueError,TypeError,KeyError,OverflowError,InvalidOperation):
        return None

def _verified(client,position,receipt,start_ms):
    if not isinstance(receipt,dict) or not isinstance(position,dict):return None
    side=position.get('side')
    symbol=getattr(client,'symbol',None)
    if side not in ('long','short') or not isinstance(symbol,str) or not symbol.endswith('/USDT:USDT'):return None
    order_id=identity(receipt.get('id'),(receipt.get('info') or {}).get('ordId'))
    if receipt.get('symbol') not in (None,symbol):return None
    expected=num(position.get('contracts'),True)
    started=num(start_ms,True)
    if started!=started.to_integral_value():return None
    order=client.exchange.fetch_order(order_id,symbol)
    if not isinstance(order,dict) or order.get('status')!='closed':return None
    if identity(order.get('id'),(order.get('info') or {}).get('ordId'))!=order_id:return None
    order_side='sell' if side=='long' else 'buy'
    if order.get('symbol') not in (None,symbol) or order.get('side')!=order_side:return None
    if num(order.get('filled'),True)!=expected or num(order.get('remaining') or 0)!=0:return None
    rows=client.fetch_trades_for_order(order_id,int(started)-10000,limit=100)
    if not isinstance(rows,list) or not rows:return None
    unique={}
    for item in rows:
        if not isinstance(item,dict):return None
        info=item.get('info') or {}
        if identity(item.get('order'),info.get('ordId'))!=order_id:return None
        if item.get('symbol')!=symbol or item.get('side')!=order_side:return None
        trade_id=identity(item.get('id'),info.get('tradeId'))
        qty=num(item.get('amount'),True);price=num(item.get('price'),True)
        at=num(item.get('timestamp'),True)
        if at!=at.to_integral_value() or at<started-10000:return None
        item_value=(qty,price,int(at))
        if trade_id in unique and unique[trade_id]!=item_value:return None
        unique[trade_id]=item_value
    filled=sum((x[0] for x in unique.values()),Decimal(0))
    if filled!=expected:return None
    vwap=sum((x[0]*x[1] for x in unique.values()),Decimal(0))/filled
    if not math.isfinite(float(vwap)):return None
    return dict(order_id=order_id,trade_ids=sorted(unique),vwap=float(vwap),
                filled_contracts=float(filled),
                first_fill_timestamp_ms=min(x[2] for x in unique.values()),
                last_fill_timestamp_ms=max(x[2] for x in unique.values()),
                price_basis='verified_order_fills')
