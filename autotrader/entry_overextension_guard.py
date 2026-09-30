"""Shared hard guard for extreme directional chase entries."""

from __future__ import annotations

import math

HARD_EXTENSION_ATR = 3.0
COMBO_EXTENSION_ATR = 2.5
DIRECTIONAL_24H_PCT = 5.0

BLOCK_REASON = "entry_overextended_extreme"
DATA_REASON = "entry_overextension_data_unavailable"


def _finite_number(value) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(float(value))


def evaluate(*, side: str, entry_price, ema20_1h, atr14_4h, reference_24h_price) -> dict:
    values = (entry_price, ema20_1h, atr14_4h, reference_24h_price)
    if side not in ("long", "short") or not all(_finite_number(v) for v in values):
        return {"allowed": False, "reason": DATA_REASON, "extension_atr": None,
                "directional_24h_pct": None}
    entry_price = float(entry_price)
    ema20_1h = float(ema20_1h)
    atr14_4h = float(atr14_4h)
    reference_24h_price = float(reference_24h_price)
    if entry_price <= 0 or atr14_4h <= 0 or reference_24h_price <= 0:
        return {"allowed": False, "reason": DATA_REASON, "extension_atr": None,
                "directional_24h_pct": None}

    sign = 1.0 if side == "long" else -1.0
    extension_atr = sign * (entry_price - ema20_1h) / atr14_4h
    directional_24h_pct = sign * (entry_price / reference_24h_price - 1.0) * 100.0

    blocked = (
        extension_atr >= HARD_EXTENSION_ATR
        or (
            extension_atr >= COMBO_EXTENSION_ATR
            and directional_24h_pct >= DIRECTIONAL_24H_PCT
        )
    )
    return {
        "allowed": not blocked,
        "reason": BLOCK_REASON if blocked else "ok",
        "extension_atr": extension_atr,
        "directional_24h_pct": directional_24h_pct,
        "thresholds": {
            "hard_extension_atr": HARD_EXTENSION_ATR,
            "combo_extension_atr": COMBO_EXTENSION_ATR,
            "directional_24h_pct": DIRECTIONAL_24H_PCT,
        },
    }
