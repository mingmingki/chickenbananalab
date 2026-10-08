"""Durable at-most-once delivery state for Candidate C notifications."""
from __future__ import annotations

import hashlib
import os

import process_lock

_SCHEMA = 1
_EVENT_TYPES = {"entry", "reduce", "close"}


def event_id(event: dict) -> str:
    event_type = event.get("event_type")
    intent_id = event.get("intent_id")
    if event_type not in _EVENT_TYPES:
        raise ValueError(f"unsupported notification event: {event_type!r}")
    if not isinstance(intent_id, str) or not intent_id:
        raise ValueError("notification intent_id is required")
    return f"{event_type}:{intent_id}"


class NotificationDeliveryStore:
    def __init__(self, user_dir: str, symbol: str):
        digest = hashlib.sha256(symbol.encode("utf-8")).hexdigest()[:16]
        self.user_dir = user_dir
        self.symbol = symbol
        self.path = os.path.join(
            user_dir, f"candidate_c_notification_delivery_{digest}.json",
        )
        self.lock_key = f"candidate_c_notification_delivery_{digest}"

    @staticmethod
    def _empty() -> dict:
        return {"schema": _SCHEMA, "records": {}}

    def _load(self) -> dict:
        data = process_lock.load_json_or_default(
            self.path,
            self._empty,
            corrupted_error_prefix="Candidate C notification delivery state",
        )
        if data.get("schema") != _SCHEMA or not isinstance(data.get("records"), dict):
            raise RuntimeError("Candidate C notification delivery state schema invalid")
        return data

    def initialize(self, *, suppressed=None) -> bool:
        """Atomically create initial state, including any rollout baseline."""
        suppressed = tuple(suppressed or ())
        with process_lock.locked(self.user_dir, self.lock_key):
            if os.path.exists(self.path):
                self._load()
                return False
            data = self._empty()
            for event_type, intent_id, reason in suppressed:
                identity = event_id({
                    "event_type": event_type,
                    "intent_id": intent_id,
                })
                data["records"][identity] = {
                    "status": "suppressed",
                    "reason": reason,
                }
            process_lock.save_json_atomic(self.path, data)
            return True

    def enqueue(self, event: dict) -> bool:
        identity = event_id(event)
        if event.get("symbol") != self.symbol:
            raise ValueError("notification symbol does not match delivery store")
        with process_lock.locked(self.user_dir, self.lock_key):
            data = self._load()
            if identity in data["records"]:
                return False
            data["records"][identity] = {
                "status": "pending",
                "event": dict(event),
            }
            process_lock.save_json_atomic(self.path, data)
            return True

    def suppress(self, event_type: str, intent_id: str, *, reason: str) -> bool:
        identity = event_id({
            "event_type": event_type,
            "intent_id": intent_id,
        })
        with process_lock.locked(self.user_dir, self.lock_key):
            data = self._load()
            if identity in data["records"]:
                return False
            data["records"][identity] = {
                "status": "suppressed",
                "reason": reason,
            }
            process_lock.save_json_atomic(self.path, data)
            return True

    def contains(self, event_type: str, intent_id: str) -> bool:
        identity = event_id({
            "event_type": event_type,
            "intent_id": intent_id,
        })
        with process_lock.locked(self.user_dir, self.lock_key):
            return identity in self._load()["records"]

    def claim_pending(self) -> list[dict]:
        """Durably mark pending events attempted before returning them for I/O."""
        with process_lock.locked(self.user_dir, self.lock_key):
            data = self._load()
            claimed = []
            for record in data["records"].values():
                if record.get("status") != "pending":
                    continue
                event = record.get("event")
                if not isinstance(event, dict):
                    raise RuntimeError("pending notification event missing")
                claimed.append(dict(event))
                record["status"] = "attempted"
            if claimed:
                process_lock.save_json_atomic(self.path, data)
            return claimed



def _coin_amount(contracts, epoch):
    contract_size = getattr(epoch, "contract_size", None)
    if contracts is None or contract_size is None:
        return None
    return contracts * contract_size


def trade_event(
    user_dir: str,
    event_type: str,
    execution_id: str,
    epoch,
    *,
    remaining_contracts=None,
) -> dict | None:
    """Rebuild a notification from the durable Candidate C trade journal."""
    row_type = {
        "entry": "open",
        "reduce": "reduce",
        "close": "close",
    }.get(event_type)
    if row_type is None:
        raise ValueError(f"unsupported notification event: {event_type!r}")
    rows = process_lock.load_jsonl_skip_corrupted(
        os.path.join(user_dir, "trades_log.jsonl"),
    )
    row = next((
        item for item in reversed(rows)
        if item.get("type") == row_type
        and item.get("execution_id") == execution_id
        and item.get("strategy_group") == "candidate_c"
    ), None)
    if row is None:
        return None
    contracts = row.get("amount")
    if event_type == "entry":
        return {
            "event_type": "entry",
            "symbol": row.get("symbol"),
            "side": row.get("side"),
            "price": row.get("price"),
            "contracts": contracts,
            "amount_coin": _coin_amount(contracts, epoch),
            "stop_price": row.get("sl_price"),
            "target_price": row.get("tp_price"),
            "intent_id": execution_id,
        }

    gross_pnl = row.get("pnl")
    fee = row.get("fee")
    net_pnl = row.get("okx_net_pnl")
    if net_pnl is None and gross_pnl is not None and fee is not None:
        net_pnl = gross_pnl - fee
    event = {
        "event_type": event_type,
        "symbol": row.get("symbol"),
        "side": row.get("side"),
        "entry_price": row.get("entry_price"),
        "price": row.get("close_price"),
        "contracts": contracts,
        "amount_coin": _coin_amount(contracts, epoch),
        "gross_pnl_usdt": gross_pnl,
        "fee_usdt": fee,
        "net_pnl_usdt": net_pnl,
        "reason": row.get("reason"),
        "intent_id": execution_id,
    }
    if event_type == "reduce":
        event["remaining_contracts"] = remaining_contracts
    else:
        event["funding_fee_usdt"] = row.get("funding_fee")
    return event
