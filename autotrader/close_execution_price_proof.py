"""Project proven owned close fills onto a final close price without changing PnL."""
import math


def proof_for_remaining_position(evidence, lifecycle_id, remaining_contracts):
    """Only strict owned_algo_order_fills evidence may authorize this price."""
    if not isinstance(evidence, dict) or evidence.get("source") != "owned_algo_order_fills":
        return None
    if evidence.get("reason") not in ("stop_loss", "take_profit"):
        return None
    if not lifecycle_id or evidence.get("lifecycle_id") != lifecycle_id:
        return None
    oid, ids, px = evidence.get("order_id"), evidence.get("trade_ids"), evidence.get("exit_price")
    if not isinstance(oid, str) or not oid.strip():
        return None
    if not isinstance(ids, list) or not ids or any(not isinstance(x, str) or not x.strip() for x in ids):
        return None
    if len(set(ids)) != len(ids):
        return None
    if isinstance(px, bool) or not isinstance(px, (int, float)) or not math.isfinite(px) or px <= 0:
        return None
    if (isinstance(remaining_contracts, bool) or not isinstance(remaining_contracts, (int,float))
            or not math.isfinite(remaining_contracts) or remaining_contracts <= 0):
        return None
    # The upstream OKX matcher proves exit-order fill quantity equals the
    # remaining position; missing timestamps and fee data are not invented.
    return dict(order_id=oid, trade_ids=list(ids), vwap=float(px),
                filled_contracts=float(remaining_contracts), algo_id=evidence.get("algo_id"),
                lifecycle_id=lifecycle_id, price_basis="verified_owned_protection_fills")
