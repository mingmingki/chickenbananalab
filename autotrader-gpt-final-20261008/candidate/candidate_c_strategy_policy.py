"""Immutable, system-managed Candidate C position-management policy."""
from __future__ import annotations
import copy, hashlib, json
from decimal import Decimal, InvalidOperation

PROFIT_LOCK_CONFIRMATION = "CONFIRMED_5M_CLOSE"
PARTIAL_TP_CONFIRMATION = "CONFIRMED_5M_CLOSE"
DERISK_UNIT = "PERCENT_0_100"
REVERSAL_MODE = "FOUR_HOUR_DIRECTION_MISMATCH"
_POLICY_V1 = {
    "schema_version": 1,
    "source_file": "candidate_c_preregistration_v2.json",
    "source_sha256": "848b94bfedcb4f7c2a2269bd5c3f76c5bf7548be5b9b3f541dee3091ab0c163c",
    "initial_stop_atr_multiplier": "3.5",
    "trailing_atr_multiplier": "6.0",
    "profit_lock_activation_r": "1.0",
    "profit_lock_confirmation": PROFIT_LOCK_CONFIRMATION,
    "derisk_pct": "50",
    "derisk_unit": DERISK_UNIT,
    "reversal_invalidation_mode": REVERSAL_MODE,
}

_POLICY_V2 = {
    "schema_version": 2,
    "source_file": "candidate_c_preregistration_v3.json",
    "source_sha256": "faa635c379885addaaed5747f0f53e253365f527ac0b979562ba0220d5b451f0",
    "initial_stop_atr_multiplier": "3.5",
    "trailing_atr_multiplier": "6.0",
    "profit_lock_activation_r": "1.0",
    "profit_lock_confirmation": PROFIT_LOCK_CONFIRMATION,
    "partial_take_profit_activation_r": "2.0",
    "partial_take_profit_pct": "25",
    "partial_take_profit_confirmation": PARTIAL_TP_CONFIRMATION,
    "derisk_pct": "50",
    "derisk_unit": DERISK_UNIT,
    "reversal_invalidation_mode": REVERSAL_MODE,
}

class StrategyPolicyError(ValueError):
    pass

def production_strategy_policy() -> dict:
    return copy.deepcopy(_POLICY_V2)

def policy_sha256(policy: dict) -> str:
    return hashlib.sha256(
        json.dumps(policy, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
def _validate_exact(value, expected):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise StrategyPolicyError("strategy_policy schema mismatch")
    numeric = {
        "initial_stop_atr_multiplier", "trailing_atr_multiplier",
        "profit_lock_activation_r", "partial_take_profit_activation_r",
        "partial_take_profit_pct", "derisk_pct",
    }
    for field, wanted in expected.items():
        if field == "schema_version":
            if type(value[field]) is not int or value[field] != wanted:
                raise StrategyPolicyError(f"strategy_policy {field} invalid")
        elif field in numeric:
            try:
                number=Decimal(str(value[field]))
            except (InvalidOperation,TypeError,ValueError) as exc:
                raise StrategyPolicyError(f"strategy_policy {field} invalid") from exc
            if not number.is_finite() or str(value[field]) != wanted:
                raise StrategyPolicyError(f"strategy_policy {field} invalid")
        elif value[field] != wanted:
            raise StrategyPolicyError(f"strategy_policy {field} invalid")
    return copy.deepcopy(value)

def validate_strategy_policy(value: object) -> dict:
    version=value.get("schema_version") if isinstance(value,dict) else None
    if version == 1:
        return _validate_exact(value,_POLICY_V1)
    if version == 2:
        return _validate_exact(value,_POLICY_V2)
    raise StrategyPolicyError("strategy_policy schema_version invalid")

def requires_risk_per_trade_pct(sizing_mode: str) -> bool:
    if sizing_mode not in {"FIXED_MARGIN","FIXED_NOTIONAL","VARIABLE_RISK"}:
        raise StrategyPolicyError(f"unknown sizing mode: {sizing_mode!r}")
    return sizing_mode == "VARIABLE_RISK"

ADAPTIVE_EXIT_MODES = frozenset({"OFF", "SHADOW", "ADVISORY", "LIVE_BOUNDED"})

def validate_adaptive_exit_mode(value: object) -> str:
    mode = str(value or "OFF").upper()
    if mode not in ADAPTIVE_EXIT_MODES:
        raise StrategyPolicyError(f"adaptive_exit_mode invalid: {value!r}")
    return mode
