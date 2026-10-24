"""v33-45 read-only, bounded UI lookup of OKX protection without entry ownership claims.

Not imported by the Candidate C live decision engine. Never submits or changes
any exchange order. Live source parity hashes remain untouched.
"""
import math
import time
import threading
import okx_client

_CACHE = {}
_LOCK = threading.Lock()
MAX_AGE_SECONDS = 15


def _observe_unowned_exchange_protection(client, position):
    """Display-only OCO snapshot; NEVER proves the entry owner's identity.

    This helper neither mutates orders nor authorizes future entries. Only
    display prices from exactly one live, correctly sided, full-sized OCO.
    """
    if not isinstance(position, dict) or position.get("side") not in ("long", "short"):
        return None
    try:
        contracts = float(position["contracts"])
        entry = float(position["entry_price"])
        expected_side = "sell" if position["side"] == "long" else "buy"
        raw = client.fetch_pending_protection_orders()
        if not isinstance(raw, list) or len(raw) != 1:
            return None
        order = raw[0]
        if (order.get("ordType") != "oco" or order.get("state") != "live"
                or order.get("side") != expected_side
                or not order.get("algoId")):
            return None
        amount = float(order["sz"])
        sl = float(order["slTriggerPx"])
        tp = float(order["tpTriggerPx"])
        if not all(math.isfinite(x) for x in (contracts,entry,amount,sl,tp)):
            return None
        if not (contracts > 0 and entry > 0 and sl > 0 and tp > 0):
            return None
        if abs(amount-contracts) > 1e-8:
            return None
        if position["side"]=="short" and not (sl > entry > tp):
            return None
        if position["side"]=="long" and not (sl < entry < tp):
            return None
        return dict(status="EXCHANGE_OCO_OBSERVED_OWNER_UNKNOWN",
                    sl_price=sl,tp_price=tp,algo_ids=[str(order["algoId"])],
                    reason="ownership_not_verified_no_entry_release",
                    source="OKX_live_algo_GET")
    except (ValueError,TypeError,KeyError,OverflowError):
        return None
    except Exception:
        return None



def lookup(cfg, symbol, position):
    """Display-only OCO prices for an unowned exchange position; 15s cache."""
    if not isinstance(position,dict):
        return None
    key=(str(cfg.user_dir),str(symbol),str(position.get("position_id")),
         str(position.get("side")),str(position.get("contracts")),
         str(position.get("entry_price")))
    now=time.monotonic()
    with _LOCK:
        cached=_CACHE.get(key)
        if cached is not None and now-cached[0]<MAX_AGE_SECONDS:
            return cached[1]
    try:
        client=okx_client.OkxClient(symbol,cfg)
        value=_observe_unowned_exchange_protection(client,position)
    except Exception:
        value=None
    with _LOCK:
        if len(_CACHE)>64:
            _CACHE.clear()
        _CACHE[key]=(time.monotonic(),value)
    return value
