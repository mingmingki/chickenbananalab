"""Process-safe test-only off-host anchor provider."""
from __future__ import annotations

import json
import os
import time

import candidate_c_off_host_anchor as anchor
import candidate_c_account_exclusivity as exclusivity
import process_lock


class FileAnchorProvider:
    provider_name = "test-file-anchor"

    def __init__(self, root):
        self.root = str(root)
        os.makedirs(self.root, exist_ok=True)

    def _path(self, account_id, strategy_id):
        safe = f"{account_id}--{strategy_id}".replace("/", "_")
        return os.path.join(self.root, safe + ".json")

    def _lease_path(self, account_id, strategy_id):
        safe = f"lease--{account_id}--{strategy_id}".replace("/", "_")
        return os.path.join(self.root, safe + ".json")

    def acquire_lease(self, account_id, strategy_id, *, holder_id, ttl_seconds):
        path = self._lease_path(account_id, strategy_id)
        with process_lock.locked(self.root, "fake_remote_control"):
            current = None
            if os.path.exists(path):
                with open(path, encoding="utf-8") as stream:
                    current = json.load(stream)
            now_ms = int(time.time() * 1000)
            if (current and current.get("state") == "HELD"
                    and current.get("expires_at_ms", 0) > now_ms):
                try:
                    os.kill(int(str(current.get("holder_id")).split("-", 1)[0]), 0)
                    holder_alive = True
                except (ValueError, ProcessLookupError):
                    holder_alive = False
                except PermissionError:
                    holder_alive = True
                if holder_alive:
                    raise anchor.AnchorConflictError("remote lease already held")
            token = int((current or {}).get("fencing_token", 0)) + 1
            payload = {
                "state": "HELD", "fencing_token": token,
                "holder_id": holder_id,
                "expires_at_ms": now_ms + int(ttl_seconds * 1000),
            }
            process_lock.save_json_atomic(path, payload)
            return anchor.AnchorLease(
                account_id, strategy_id, token, holder_id,
                payload["expires_at_ms"], str(token), self.provider_name,
            )

    def validate_lease(self, lease):
        path = self._lease_path(lease.account_id, lease.strategy_id)
        with process_lock.locked(self.root, "fake_remote_control"):
            with open(path, encoding="utf-8") as stream:
                current = json.load(stream)
            if (
                current.get("state") != "HELD"
                or current.get("fencing_token") != lease.fencing_token
                or current.get("holder_id") != lease.holder_id
                or current.get("expires_at_ms") != lease.expires_at_ms
                or current["expires_at_ms"] <= int(time.time() * 1000)
            ):
                raise anchor.AnchorConflictError("stale or expired remote lease")
            return lease

    def release_lease(self, lease):
        self.validate_lease(lease)
        path = self._lease_path(lease.account_id, lease.strategy_id)
        with process_lock.locked(self.root, "fake_remote_control"):
            process_lock.save_json_atomic(path, {
                "state": "RELEASED", "fencing_token": lease.fencing_token,
                "holder_id": lease.holder_id,
                "expires_at_ms": lease.expires_at_ms,
            })

    def read(self, account_id, strategy_id):
        path = self._path(account_id, strategy_id)
        with process_lock.locked(self.root, "fake_remote"):
            if not os.path.exists(path):
                return None
            with open(path, encoding="utf-8") as stream:
                return anchor.AnchorRecord(**json.load(stream))

    def compare_and_swap(
        self, account_id, strategy_id, *, expected_generation,
        expected_sequence, expected_head_hash, new_sequence, new_head_hash,
    ):
        path = self._path(account_id, strategy_id)
        with process_lock.locked(self.root, "fake_remote"):
            current = None
            if os.path.exists(path):
                with open(path, encoding="utf-8") as stream:
                    current = anchor.AnchorRecord(**json.load(stream))
            actual = (
                current.generation if current else None,
                current.sequence if current else 0,
                current.head_hash if current else None,
            )
            if actual != (
                expected_generation, expected_sequence, expected_head_hash,
            ):
                raise anchor.AnchorConflictError("stale test remote generation")
            result = anchor.AnchorRecord(
                account_id, strategy_id, new_sequence, new_head_hash,
                str(new_sequence), self.provider_name,
            )
            process_lock.save_json_atomic(path, result.as_dict())
            return result

    def compare_and_swap_fenced(self, account_id, strategy_id, *, lease, **kwargs):
        with process_lock.locked(self.root, "fake_remote_control"):
            self.validate_lease(lease)
            return self.compare_and_swap(account_id, strategy_id, **kwargs)


def install(monkeypatch, tmp_path, *, account_id="test-account"):
    provider = FileAnchorProvider(tmp_path / "off-host-anchor")
    monkeypatch.setattr(
        anchor, "configured_provider_from_environment", lambda: provider,
    )
    monkeypatch.setenv("CANDIDATE_C_ANCHOR_ACCOUNT_ID", account_id)
    # Production LIVE entry now requires fresh, explicit evidence that the
    # account and four Candidate symbols are operationally exclusive.  Tests
    # using this fake provider make that assumption explicit rather than
    # weakening the production fail-closed boundary.
    now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
    process_lock.save_json_atomic(
        exclusivity.evidence_path(str(tmp_path)),
        exclusivity.canonical_evidence(
            account_id=account_id,
            actor="pytest-fake-operator",
            confirmed_at_utc=now.isoformat().replace("+00:00", "Z"),
            snapshot_sha256="1" * 64,
            retention_evidence_digest="2" * 64,
        ),
    )
    return provider
