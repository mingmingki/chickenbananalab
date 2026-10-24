"""Future-only CORE manual RISK exposure cap. Does not change shared sizing rules."""
import math
import risk_manager


def cap_manual_risk_quantity(cfg, client, symbol, side, requested_amount,
                             price, stop_price, equity, roundtrip_cost_rate=0.001):
    """Cost-inclusive planned loss at final quote; never increase original exposure.

    Same 0.001 modeled round-trip rate as automatic CORE. Market-order slippage
    means pre-submit loss caps do not guarantee realized stop-fill loss.
    """
    values = (requested_amount, price, stop_price, equity, roundtrip_cost_rate)
    if (side not in ('long', 'short') or
            any(not isinstance(v, (float, int)) or not math.isfinite(v) for v in values) or
            min(requested_amount, price, stop_price, equity) <= 0 or
            roundtrip_cost_rate < 0 or
            (side == 'long' and stop_price >= price) or
            (side == 'short' and stop_price <= price)):
        return 0.0, None
    risk_pct = float(getattr(cfg, 'RISK_PER_TRADE_PCT', 0))
    if not math.isfinite(risk_pct) or risk_pct <= 0:
        return 0.0, None
    budget = equity * risk_pct / 100.0
    risk_per_coin = abs(price - stop_price) + price * roundtrip_cost_rate
    if not math.isfinite(risk_per_coin) or risk_per_coin <= 0:
        return 0.0, None
    allowed_amount = min(requested_amount, budget / risk_per_coin)
    amount = risk_manager.quantize_coin_amount_to_market(client, symbol, allowed_amount)
    planned_loss = amount * risk_per_coin
    if amount <= 0 or amount > requested_amount + 1e-10 or planned_loss > budget + 1e-8:
        return 0.0, None
    return amount, {
        'manual_risk_cost_inclusive': True,
        'risk_equity_usdt': equity,
        'risk_budget_usdt': budget,
        'risk_cost_rate': roundtrip_cost_rate,
        'risk_reference_price': price,
        'risk_stop_price': stop_price,
        'risk_quantity_coin': amount,
        'risk_planned_loss_usdt': planned_loss,
    }
