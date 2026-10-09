"""Notification boundaries: CORE gate diagnostics; Candidate C fills only.

Retain full internal audit evidence. Telegram is a curated stream, not the order
journal. This module must never change trading, positions, or safety controls.
"""
import config

CORE = frozenset(config.CORE_SYMBOLS)
CANDIDATE_C = frozenset(("DOGE/USDT:USDT", "SOL/USDT:USDT"))
CANDIDATE_C_TRADE_EVENTS = frozenset(("entry", "reduce", "close"))

# A normal GPT WAIT/REJECT is a model decision, not an infrastructure issue.
CORE_GATE_ISSUES = frozenset((
    "GPT_ERROR", "TIMEOUT_BYPASS", "NO_RESPONSE_BYPASS",
    "ORDER_PENDING", "ORDER_FAILED",
))
CORE_CRITICAL_LOCAL_REASONS = (
    "core_kill_switch", "unresolved_entry", "protection_",
    "exchange_fill_unconfirmed", "entry_ownership:", "original filled lifecycle unconfirmed",
)

def is_core_event(event):
    return (event.get("symbol") in CORE
            and (event.get("engine") or "CORE") == "CORE")

def should_send_core_telegram(event):
    if not is_core_event(event):
        return False
    status = str(event.get("status") or "")
    if status == "FILLED":
        return str(event.get("reason") or "") == "protected_fill_confirmed"
    if status in CORE_GATE_ISSUES:
        return True
    if status == "LOCAL_BLOCKED":
        reason = str(event.get("reason") or "").lower()
        return any(word in reason for word in CORE_CRITICAL_LOCAL_REASONS)
    return False

def should_send_candidate_c_telegram(event):
    return (event.get("symbol") in CANDIDATE_C
            and event.get("event_type") in CANDIDATE_C_TRADE_EVENTS)
