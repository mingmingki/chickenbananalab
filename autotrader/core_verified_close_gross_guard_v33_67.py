"""v33-67 CORE close gross anomaly guard.

Only a VERIFIED full close-order fill bundle with a zero historical gross can
override that gross. Do not touch nonzero gross, reduces, unverified receipts,
unknown contract sizes, fee allocation, funding, or order paths.

Gross computed from the OKX position's current average entry price and the
exchange-provided contract size, not from old open journal price.
"""
from decimal import Decimal, InvalidOperation

def dec(v):
    if v is None or isinstance(v,bool):
        raise ValueError("missing")
    d=Decimal(str(v))
    if not d.is_finite():raise ValueError("nonfinite")
    return d

def reconcile_zero_gross(resolved, position, proof, *, contract_size):
    if not (isinstance(resolved,dict) and isinstance(position,dict)
            and isinstance(proof,dict)):
        return resolved,None
    if resolved.get("source")!="okx_realized":
        return resolved,None
    try:
        orig=dec(resolved["gross_pnl"])
        fee=dec(resolved["fee"])
        current_net=dec(resolved["net_pnl"])
        if abs(orig)>Decimal("0.0000001"):return resolved,None
        if proof.get("price_basis")!="verified_order_fills":return resolved,None
        trade_ids=proof.get("trade_ids")
        if not isinstance(trade_ids,list) or not trade_ids or len(set(map(str,trade_ids)))!=len(trade_ids):
            return resolved,None
        if not str(proof.get("order_id") or ""):return resolved,None
        size=dec(contract_size)
        qty=dec(position.get("contracts"))
        filled=dec(proof.get("filled_contracts"))
        entry=dec(position.get("entry_price"))
        exit_px=dec(proof.get("vwap"))
        if size<=0 or qty<=0 or qty!=filled or entry<=0 or exit_px<=0:
            return resolved,None
        if position.get("side") not in ("long","short"):return resolved,None
        sign=Decimal(1) if position["side"]=="long" else Decimal(-1)
        gross=sign*(exit_px-entry)*qty*size
        if abs(gross)<Decimal("0.0000001"):return resolved,None
        # Do not recompute old fees/funding: net gross adjustment preserves
        # prior allocation and prevents double charging trade-entry costs.
        net=current_net+(gross-orig)
        adjusted=dict(resolved,gross_pnl=float(gross),net_pnl=float(net))
        metadata=dict(rule="v33-67_verified_zero_gross_only",
            original_history_gross=float(orig),verified_fill_price=float(exit_px),
            verified_position_entry=float(entry),position_contracts=float(qty),
            exchange_contract_size=float(size),corrected_gross=float(gross),
            preserved_prior_fee=float(fee),net_delta=float(gross-orig),
            fill_order_id=str(proof["order_id"]),fill_trade_ids=sorted(map(str,trade_ids)))
        return adjusted,metadata
    except (ValueError,TypeError,InvalidOperation,KeyError,OverflowError):
        return resolved,None
