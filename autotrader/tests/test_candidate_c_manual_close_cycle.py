from types import SimpleNamespace

import candidate_c_hybrid_cycle as cycle
import candidate_c_manual_close as cmc
import candidate_c_reversal_state_machine as rsm


class FakeLedger:
    def __init__(self, pending=None):
        self._pending = list(pending or [])
    def refresh(self):
        return None
    def pending_intents(self):
        return list(self._pending)


class FakeEpochStore:
    def __init__(self):
        self.epoch = SimpleNamespace(
            protective_algo_ids=["algo-1"], current_stop_price=104.0, target_price=None,
        )
    def get(self, _position_id):
        return self.epoch


class FakeReversalStore:
    def __init__(self, machine):
        self.machine = machine
        self.persisted = []
    def get(self, _symbol):
        return self.machine
    def persist(self, symbol, tick):
        self.persisted.append((symbol, tick))


def _state(machine, pending=None):
    return SimpleNamespace(
        intent_ledger=FakeLedger(pending),
        epoch_store=FakeEpochStore(),
        reversal_store=FakeReversalStore(machine),
    )


def _cfg(tmp_path):
    return SimpleNamespace(
        user_dir=str(tmp_path), CANDIDATE_C_LIVE_EXECUTE=True,
        REENTRY_COOLDOWN_MINUTES=15, logger=None,
    )


def _position():
    return {
        "side": "long", "position_id": "entry-1", "contracts": 1.35,
        "raw_entry_price": 110.67, "initial_stop_price": 104.0,
        "high_water": 114.0, "effective_entry_price": 110.67,
        "entry_fee_usdt": 0.1, "contract_size": 1.0,
        "fee_rate": 0.0005, "spread_bps": 0.0, "slippage_bps": 0.0,
    }


def test_manual_close_uses_exit_intent_and_confirms_15m_cooldown(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    req = cmc.reserve(cfg.user_dir, "SOL/USDT:USDT", position_epoch="entry-1", now=100.0)
    machine = rsm.SymbolReversalMachine(
        "SOL/USDT:USDT", rsm._SymbolState(state=rsm.State.LONG)
    )
    state = _state(machine)

    monkeypatch.setattr(cycle.cycle_recon, "reconcile_managed_position",
                        lambda *a, **k: {"critical": False, "reason": "ok"})
    monkeypatch.setattr(cycle, "_current_position_from_epoch", lambda _s: _position())
    seen = {}
    def execute(_cfg, _client, intent, **kwargs):
        seen["intent"] = intent
        seen["position"] = kwargs["current_position"]
        return {"intent_kind": intent.kind, "executed": True, "flat_confirmed": True}
    monkeypatch.setattr(cycle.live, "execute_intent", execute)

    result = cycle.run_manual_close_request(
        cfg, object(), "SOL/USDT:USDT", state=state,
        account_id="acct", config_version_id="v1", config_hash="hash",
        strategy_policy={"schema_version": 1}, now_ms=101000,
    )

    assert seen["intent"].kind == "ExitIntent"
    assert seen["intent"].reason_code == "manual_close_15m"
    assert seen["intent"].position_epoch == "entry-1"
    assert seen["position"]["position_id"] == "entry-1"
    assert result["flat_confirmed"] is True
    assert machine.state == rsm.State.FLAT
    status = cmc.status(cfg.user_dir, "SOL/USDT:USDT", now=101.0)
    assert status["record"]["close_id"] == req["close_id"]
    assert status["record"]["release_at"] == 1001.0
    assert status["block_reason"] == "candidate_manual_close_cooldown"


def test_definite_manual_close_rejection_restores_original_side(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    req = cmc.reserve(cfg.user_dir, "SOL/USDT:USDT", position_epoch="entry-1", now=100.0)
    machine = rsm.SymbolReversalMachine(
        "SOL/USDT:USDT", rsm._SymbolState(state=rsm.State.LONG)
    )
    state = _state(machine)
    monkeypatch.setattr(cycle.cycle_recon, "reconcile_managed_position",
                        lambda *a, **k: {"critical": False, "reason": "ok"})
    monkeypatch.setattr(cycle, "_current_position_from_epoch", lambda _s: _position())
    monkeypatch.setattr(cycle.live, "execute_intent",
                        lambda *a, **k: {"intent_kind": "ExitIntent", "executed": False,
                                        "reason": "management_action_rejected_existing_protection_preserved"})

    result = cycle.run_manual_close_request(
        cfg, object(), "SOL/USDT:USDT", state=state,
        account_id="acct", config_version_id="v1", config_hash="hash",
        strategy_policy={"schema_version": 1}, now_ms=101000,
    )
    assert result["executed"] is False
    assert machine.state == rsm.State.LONG
    status = cmc.status(cfg.user_dir, "SOL/USDT:USDT", now=102.0)
    assert status["record"]["close_id"] == req["close_id"]
    assert status["record"]["status"] == "failed"
    assert status["block_reason"] is None


def test_unresolved_management_waits_without_second_submission(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    cmc.reserve(cfg.user_dir, "SOL/USDT:USDT", position_epoch="entry-1", now=100.0)
    machine = rsm.SymbolReversalMachine(
        "SOL/USDT:USDT", rsm._SymbolState(state=rsm.State.LONG)
    )
    state = _state(machine, [SimpleNamespace(kind="ReduceIntent")])
    monkeypatch.setattr(cycle.cycle_recon, "reconcile_managed_position",
                        lambda *a, **k: {"critical": False, "reason": "ok"})
    monkeypatch.setattr(cycle, "_current_position_from_epoch", lambda _s: _position())
    monkeypatch.setattr(cycle.live, "execute_intent",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not submit")))

    result = cycle.run_manual_close_request(
        cfg, object(), "SOL/USDT:USDT", state=state,
        account_id="acct", config_version_id="v1", config_hash="hash",
        strategy_policy={"schema_version": 1}, now_ms=101000,
    )
    assert result["pending"] is True
    assert result["reason"] == "manual_close_waiting_for_management_reconcile"
    assert machine.state == rsm.State.LONG
    assert cmc.block_reason(cmc.get(cfg.user_dir, "SOL/USDT:USDT"), now=102.0) == "candidate_manual_close_unresolved"


def test_stop_update_pending_does_not_block_manual_exit(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    cmc.reserve(cfg.user_dir, "SOL/USDT:USDT", position_epoch="entry-1", now=100.0)
    machine = rsm.SymbolReversalMachine(
        "SOL/USDT:USDT", rsm._SymbolState(state=rsm.State.LONG)
    )
    state = _state(machine, [SimpleNamespace(kind="StopUpdateIntent")])
    monkeypatch.setattr(cycle.cycle_recon, "reconcile_managed_position",
                        lambda *a, **k: {"critical": False, "reason": "ok"})
    monkeypatch.setattr(cycle, "_current_position_from_epoch", lambda _s: _position())
    called = {"n": 0}
    def execute(*a, **k):
        called["n"] += 1
        return {"intent_kind": "ExitIntent", "executed": True, "flat_confirmed": True}
    monkeypatch.setattr(cycle.live, "execute_intent", execute)
    result = cycle.run_manual_close_request(
        cfg, object(), "SOL/USDT:USDT", state=state,
        account_id="acct", config_version_id="v1", config_hash="hash",
        strategy_policy={"schema_version": 1}, now_ms=101000,
    )
    assert called["n"] == 1
    assert result["flat_confirmed"] is True
