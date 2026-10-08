from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import candidate_c_hybrid_live_adapter as live


@pytest.mark.parametrize("symbol", ["DOGE/USDT:USDT", "SOL/USDT:USDT"])
def test_verified_entry_exposes_candidate_c_notification_event(
    tmp_path, monkeypatch, symbol,
):
    intent = SimpleNamespace(
        kind="EntryIntent",
        account_id="acct-1",
        symbol=symbol,
        side="long",
        setup_id="setup-1",
        config_version_id="7",
        config_hash="cfg-hash",
        raw_stop_price=95.0,
        raw_target_price=110.0,
    )
    record = SimpleNamespace(
        intent_id="entry-1",
        requested_quantity=2.0,
        contract_size=10.0,
        lot_step=0.1,
        min_contracts=0.1,
        tick_size=0.01,
        max_contracts=100.0,
        reserved_risk_usdt=10.0,
        reservation_id="reservation-1",
        attach_algo_cl_ord_id="attach-1",
        protective_order_type="oco",
        strategy_policy={},
    )
    client = MagicMock()
    client.fetch_position.return_value = {
        "side": "long",
        "contracts": 2.0,
        "entry_price": 100.0,
        "position_id": "position-1",
        "entry_timestamp_ms": 1234,
    }
    client.fetch_pending_protection_algo_ids.return_value = ["algo-1"]
    ledger = MagicMock()
    epoch_store = MagicMock()
    cfg = SimpleNamespace(user_dir=str(tmp_path))

    monkeypatch.setattr(
        live,
        "_verify_protection_with_retry",
        lambda *args, **kwargs: {"ok": True, "oco_count": 1},
    )
    monkeypatch.setattr(live.trade_log, "record_open", lambda *args, **kwargs: None)

    result = live._verify_and_finalize_entry(
        cfg,
        client,
        intent,
        snapshot={},
        amount_coin=20.0,
        order={"status": "closed", "filled": 2.0, "average": 100.0},
        ledger=ledger,
        epoch_store=epoch_store,
        record=record,
        gate_result="rule_based_no_gpt_review",
    )

    assert result["notification_event"] == {
        "event_type": "entry",
        "symbol": symbol,
        "side": "long",
        "price": 100.0,
        "contracts": 2.0,
        "amount_coin": 20.0,
        "stop_price": 95.0,
        "target_price": 110.0,
        "intent_id": "entry-1",
    }
    restarted_store = live.notification_delivery.NotificationDeliveryStore(
        str(tmp_path), symbol,
    )
    assert restarted_store.claim_pending() == [result["notification_event"]]


def _managed_epoch(symbol):
    return live.cem.PositionEpochState(
        account_id="acct-1",
        symbol=symbol,
        side="long",
        entry_intent_id="entry-1",
        raw_entry_price=100.0,
        exchange_position_id="position-1",
        exchange_entry_timestamp_ms=1234,
        contract_size=10.0,
        original_contracts=2.0,
        remaining_contracts=2.0,
        remaining_reserved_risk_usdt=20.0,
        remaining_gross_notional_usdt=2000.0,
        protective_algo_ids=["algo-1"],
    )


def test_verified_partial_reduce_exposes_notification_event(tmp_path, monkeypatch):
    symbol = "DOGE/USDT:USDT"
    intent = SimpleNamespace(
        kind=live.dec.INTENT_REDUCE,
        symbol=symbol,
        reason_code="structural_derisk",
    )
    parent = SimpleNamespace(intent_id="entry-1", protective_algo_ids=["algo-1"])
    action = SimpleNamespace(intent_id="reduce-1")
    epoch = _managed_epoch(symbol)
    position = {
        "side": "long",
        "contracts": 2.0,
        "raw_entry_price": 100.0,
        "position_id": "position-1",
        "entry_timestamp_ms": 1234,
    }
    client = MagicMock()
    client.fetch_position.return_value = {
        "side": "long",
        "contracts": 1.0,
        "entry_price": 100.0,
        "position_id": "position-1",
        "entry_timestamp_ms": 1234,
    }
    client.amend_protective_stop.return_value = {"ok": True}
    client.exchange.market.return_value = {"precision": {"amount": 0.1}}
    client.fetch_protection_order_by_algo_id.return_value = {"sz": 1.0}
    cfg = SimpleNamespace(user_dir=str(tmp_path), logger=MagicMock())

    monkeypatch.setattr(
        live,
        "_resolve_candidate_c_reduce_pnl",
        lambda *args, **kwargs: {
            "gross_pnl": 0.0,
            "fee": 1.0,
            "net_pnl": None,
            "exit_price": 105.0,
            "funding_fee": None,
            "source": "estimated",
            "fee_source": "order_id_matched",
        },
    )
    monkeypatch.setattr(live.trade_log, "record_reduce", lambda *args, **kwargs: None)

    result = live._finalize_managed(
        cfg,
        client,
        intent,
        MagicMock(),
        MagicMock(),
        parent,
        epoch,
        position,
        2.0,
        1.0,
        action,
        {"order": {"id": "order-1", "average": 105.0}},
    )

    assert result["notification_event"] == {
        "event_type": "reduce",
        "symbol": symbol,
        "side": "long",
        "entry_price": 100.0,
        "price": 105.0,
        "contracts": 1.0,
        "amount_coin": 10.0,
        "remaining_contracts": 1.0,
        "gross_pnl_usdt": 50.0,
        "fee_usdt": 1.0,
        "net_pnl_usdt": 49.0,
        "reason": "structural_derisk",
        "intent_id": "reduce-1",
    }
    restarted_store = live.notification_delivery.NotificationDeliveryStore(
        str(tmp_path), symbol,
    )
    assert restarted_store.claim_pending() == [result["notification_event"]]

    partial_exit_intent = SimpleNamespace(
        kind=live.dec.INTENT_EXIT,
        symbol=symbol,
        reason_code="four_hour_invalidation",
    )
    partial_exit_action = SimpleNamespace(intent_id="exit-partial-1")
    partial_exit_result = live._finalize_managed(
        cfg,
        client,
        partial_exit_intent,
        MagicMock(),
        MagicMock(),
        parent,
        _managed_epoch(symbol),
        position,
        2.0,
        1.0,
        partial_exit_action,
        {"order": {"id": "order-2", "average": 105.0}},
    )

    assert partial_exit_result["pending"] is True
    assert partial_exit_result["notification_event"]["event_type"] == "reduce"
    assert partial_exit_result["notification_event"]["intent_id"] == "exit-partial-1"

    monkeypatch.setattr(
        live.notification_delivery.NotificationDeliveryStore,
        "enqueue",
        MagicMock(side_effect=OSError("disk unavailable")),
    )
    failed_store_result = live._finalize_managed(
        cfg,
        client,
        intent,
        MagicMock(),
        MagicMock(),
        parent,
        _managed_epoch(symbol),
        position,
        2.0,
        1.0,
        action,
        {"order": {"id": "order-1", "average": 105.0}},
    )

    assert failed_store_result["executed"] is True
    assert failed_store_result.get("critical") is not True
    assert failed_store_result["notification_event"]["intent_id"] == "reduce-1"


def test_verified_full_close_exposes_notification_event(tmp_path, monkeypatch):
    symbol = "SOL/USDT:USDT"
    intent = SimpleNamespace(
        kind=live.dec.INTENT_EXIT,
        symbol=symbol,
        reason_code="four_hour_invalidation",
    )
    parent = SimpleNamespace(intent_id="entry-1", protective_algo_ids=["algo-1"])
    action = SimpleNamespace(intent_id="exit-1")
    epoch = _managed_epoch(symbol)
    position = {
        "side": "long",
        "contracts": 2.0,
        "raw_entry_price": 100.0,
        "position_id": "position-1",
        "entry_timestamp_ms": 1234,
        "unrealized_pnl": 90.0,
    }
    client = MagicMock()
    client.fetch_position.return_value = None
    client.fetch_pending_protection_algo_ids.side_effect = [["algo-1"], []]
    client.exchange.fetch_open_orders.return_value = []
    ledger = MagicMock()
    ledger.pending_intents.return_value = []
    cfg = SimpleNamespace(user_dir=str(tmp_path))

    monkeypatch.setattr(
        live,
        "_resolve_candidate_c_close_pnl",
        lambda *args, **kwargs: {
            "gross_pnl": 100.0,
            "fee": 2.0,
            "net_pnl": 98.0,
            "exit_price": 110.0,
            "funding_fee": 0.0,
            "source": "okx_realized",
        },
    )
    monkeypatch.setattr(live.trade_log, "record_close", lambda *args, **kwargs: None)

    result = live._finalize_managed(
        cfg,
        client,
        intent,
        ledger,
        MagicMock(),
        parent,
        epoch,
        position,
        2.0,
        2.0,
        action,
        {"order": {"id": "order-2", "average": 110.0}},
    )

    assert result["notification_event"] == {
        "event_type": "close",
        "symbol": symbol,
        "side": "long",
        "entry_price": 100.0,
        "price": 110.0,
        "contracts": 2.0,
        "amount_coin": 20.0,
        "gross_pnl_usdt": 100.0,
        "fee_usdt": 2.0,
        "net_pnl_usdt": 98.0,
        "funding_fee_usdt": 0.0,
        "reason": "four_hour_invalidation",
        "intent_id": "exit-1",
    }
    restarted_store = live.notification_delivery.NotificationDeliveryStore(
        str(tmp_path), symbol,
    )
    assert restarted_store.claim_pending() == [result["notification_event"]]


def test_external_close_is_queued_once_for_notification(tmp_path, monkeypatch):
    import candidate_c_cycle_reconciliation as cycle_recon
    import candidate_c_notification_delivery as delivery

    symbol = "DOGE/USDT:USDT"
    record = SimpleNamespace(
        intent_id="entry-1",
        kind=live.dec.INTENT_ENTRY,
        remote_submission_finalized=True,
    )
    epoch = _managed_epoch(symbol)
    ledger = MagicMock()
    ledger.find_protected_entry.return_value = (record, False)
    ledger.pending_intents.return_value = []
    epoch_store = MagicMock()
    epoch_store.get.return_value = epoch
    machine = MagicMock()
    reversal_store = MagicMock()
    reversal_store.get.return_value = machine
    store = delivery.NotificationDeliveryStore(str(tmp_path), symbol)
    store.initialize()
    state = SimpleNamespace(
        intent_ledger=ledger,
        epoch_store=epoch_store,
        reversal_store=reversal_store,
        notification_events=[],
        notification_store=store,
    )
    client = MagicMock()
    client.fetch_position.return_value = None
    client.exchange.fetch_open_orders.return_value = []
    client.fetch_pending_protection_algo_ids.return_value = []
    client.fetch_realized_close_for_position.return_value = {
        "gross_pnl": -50.0,
        "fee": 1.0,
        "net_pnl": -51.0,
        "exit_price": 95.0,
        "funding_fee": 0.0,
        "source": "okx_realized",
    }
    cfg = SimpleNamespace(
        user_dir=str(tmp_path),
        CANDIDATE_C_LIVE_EXECUTE=True,
    )

    monkeypatch.setattr(
        cycle_recon.adapter,
        "reconcile_pending_management",
        lambda *args, **kwargs: {"pending": False},
    )
    monkeypatch.setattr(
        cycle_recon.recon,
        "classify_external_position_change",
        lambda **kwargs: "exchange_stop_loss",
    )
    result = cycle_recon.reconcile_managed_position(
        cfg, client, symbol, state, tick_ms=123456,
    )

    assert result == {"reason": "external_flat_reconciled", "flat_confirmed": True}
    assert state.notification_events == [{
        "event_type": "close",
        "symbol": symbol,
        "side": "long",
        "entry_price": 100.0,
        "price": 95.0,
        "contracts": 2.0,
        "amount_coin": 20.0,
        "gross_pnl_usdt": -50.0,
        "fee_usdt": 1.0,
        "net_pnl_usdt": -51.0,
        "funding_fee_usdt": 0.0,
        "reason": "exchange_stop_loss",
        "intent_id": "external-close:entry-1",
    }]
    claimed = store.claim_pending()
    assert len(claimed) == 1
    assert claimed[0]["intent_id"] == "external-close:entry-1"

    state.notification_events.clear()
    (tmp_path / "trades_log.jsonl").write_text(
        '{"type":"close","execution_id":"external-close:entry-1"}\n',
        encoding="utf-8",
    )

    replayed = cycle_recon.reconcile_managed_position(
        cfg, client, symbol, state, tick_ms=123457,
    )

    assert replayed == {"reason": "external_flat_reconciled", "flat_confirmed": True}
    assert state.notification_events == []

    failure_dir = tmp_path / "notification-build-failure"
    failure_dir.mkdir()
    failure_store = delivery.NotificationDeliveryStore(
        str(failure_dir), symbol,
    )
    failure_store.initialize()
    failure_state = SimpleNamespace(
        intent_ledger=ledger,
        epoch_store=epoch_store,
        reversal_store=reversal_store,
        notification_events=[],
        notification_store=failure_store,
    )
    failure_cfg = SimpleNamespace(
        user_dir=str(failure_dir),
        CANDIDATE_C_LIVE_EXECUTE=True,
        logger=MagicMock(),
    )
    failed_builder = MagicMock(
        side_effect=OSError("notification journal unavailable"),
    )
    monkeypatch.setattr(
        cycle_recon.notification_delivery,
        "trade_event",
        failed_builder,
    )

    failure_result = cycle_recon.reconcile_managed_position(
        failure_cfg, client, symbol, failure_state, tick_ms=123458,
    )

    assert failure_result == {
        "reason": "external_flat_reconciled",
        "flat_confirmed": True,
    }
    assert failure_state.notification_events == []
    failed_builder.assert_called_once()


def test_hybrid_state_has_isolated_notification_queue():
    import candidate_c_hybrid_cycle as hybrid_cycle

    first = hybrid_cycle.HybridEngineState(
        MagicMock(), MagicMock(), MagicMock(), MagicMock(),
    )
    second = hybrid_cycle.HybridEngineState(
        MagicMock(), MagicMock(), MagicMock(), MagicMock(),
    )

    first.notification_events.append({"event_type": "entry"})
    assert second.notification_events == []


def test_recovered_entry_queues_notification(monkeypatch):
    import candidate_c_cycle_reconciliation as cycle_recon

    symbol = "SOL/USDT:USDT"
    record = SimpleNamespace(
        account_id="acct-1",
        symbol=symbol,
        setup_id="setup-1",
        config_version_id="7",
        config_hash="cfg-hash",
        requested_side="long",
        cl_ord_id="entry-1",
        requested_stop_price=95.0,
        requested_target_price=110.0,
        requested_quantity=2.0,
        contract_size=10.0,
    )
    position = {"side": "long", "contracts": 2.0, "entry_price": 100.0}
    client = MagicMock()
    client.fetch_order_status_by_client_id.return_value = {
        "status": "closed",
        "filled": 2.0,
        "average": 100.0,
    }
    event = {"event_type": "entry", "symbol": symbol, "intent_id": "entry-1"}
    state = SimpleNamespace(
        intent_ledger=MagicMock(),
        epoch_store=MagicMock(),
        reversal_store=MagicMock(),
        notification_events=[],
    )
    cfg = SimpleNamespace(CANDIDATE_C_LIVE_EXECUTE=True)

    monkeypatch.setattr(
        cycle_recon.adapter,
        "_verify_and_finalize_entry",
        lambda *args, **kwargs: {
            "executed": True,
            "notification_event": event,
        },
    )

    result = cycle_recon._recover_entry_fill(
        cfg, client, record, state, position, tick_ms=123456,
    )

    assert result["executed"] is True
    assert state.notification_events == [event]


def test_dispatch_sends_doge_and_sol_once_outside_cycle(monkeypatch):
    import candidate_c_trader_adapter as trader_adapter

    doge_event = {
        "event_type": "entry",
        "symbol": "DOGE/USDT:USDT",
        "side": "long",
        "price": 0.25,
        "contracts": 2.0,
        "amount_coin": 20.0,
        "stop_price": 0.20,
        "target_price": 0.35,
        "intent_id": "doge-entry-1",
    }
    sol_event = {
        "event_type": "close",
        "symbol": "SOL/USDT:USDT",
        "side": "short",
        "entry_price": 150.0,
        "price": 140.0,
        "contracts": 1.0,
        "amount_coin": 1.0,
        "gross_pnl_usdt": 10.0,
        "fee_usdt": 0.2,
        "net_pnl_usdt": 9.8,
        "funding_fee_usdt": 0.0,
        "reason": "take_profit",
        "intent_id": "sol-close-1",
    }
    state = SimpleNamespace(notification_events=[doge_event, sol_event])
    cfg = SimpleNamespace(logger=MagicMock())
    sent = []
    monkeypatch.setattr(
        trader_adapter.telegram_notify,
        "send",
        lambda cfg, text: sent.append(text),
    )

    trader_adapter._dispatch_candidate_c_notifications(
        cfg, state, {"notification_event": doge_event},
    )

    assert len(sent) == 2
    assert "[Candidate C] 진입" in sent[0]
    assert "DOGE/USDT:USDT" in sent[0]
    assert "SL=0.2" in sent[0]
    assert "TP=0.35" in sent[0]
    assert "[Candidate C] 청산" in sent[1]
    assert "SOL/USDT:USDT" in sent[1]
    assert "사유=take_profit" in sent[1]
    assert "순손익=+9.80 USDT" in sent[1]
    assert state.notification_events == []


def test_dispatch_is_fail_open_and_does_not_retry(monkeypatch):
    import candidate_c_trader_adapter as trader_adapter

    event = {
        "event_type": "reduce",
        "symbol": "DOGE/USDT:USDT",
        "side": "long",
        "entry_price": 0.20,
        "price": 0.25,
        "contracts": 1.0,
        "amount_coin": 10.0,
        "remaining_contracts": 1.0,
        "gross_pnl_usdt": 0.5,
        "fee_usdt": 0.1,
        "net_pnl_usdt": 0.4,
        "reason": "structural_derisk",
        "intent_id": "reduce-1",
    }
    state = SimpleNamespace(notification_events=[event])
    cfg = SimpleNamespace(logger=MagicMock())
    monkeypatch.setattr(
        trader_adapter.telegram_notify,
        "send",
        MagicMock(side_effect=RuntimeError("telegram unavailable")),
    )

    result = {"executed": True}
    trader_adapter._dispatch_candidate_c_notifications(cfg, state, result)

    assert result == {"executed": True}
    assert state.notification_events == []
    cfg.logger.warning.assert_called_once()


def test_reconciled_management_queues_notification(monkeypatch):
    import candidate_c_cycle_reconciliation as cycle_recon

    event = {
        "event_type": "reduce",
        "symbol": "DOGE/USDT:USDT",
        "intent_id": "reduce-recovered-1",
    }
    ledger = MagicMock()
    ledger.find_protected_entry.return_value = (None, False)
    machine = SimpleNamespace(state=cycle_recon.rsm.State.FLAT)
    reversal_store = MagicMock()
    reversal_store.get.return_value = machine
    state = SimpleNamespace(
        intent_ledger=ledger,
        epoch_store=MagicMock(),
        reversal_store=reversal_store,
        notification_events=[],
    )
    monkeypatch.setattr(
        cycle_recon.adapter,
        "reconcile_pending_management",
        lambda *args, **kwargs: {
            "pending": False,
            "notification_event": event,
        },
    )

    cycle_recon.reconcile_managed_position(
        SimpleNamespace(CANDIDATE_C_LIVE_EXECUTE=True),
        MagicMock(),
        "DOGE/USDT:USDT",
        state,
        tick_ms=123456,
    )

    assert state.notification_events == [event]


def test_unprotected_entry_does_not_emit_notification(tmp_path, monkeypatch):
    intent = SimpleNamespace(
        kind=live.dec.INTENT_ENTRY,
        account_id="acct-1",
        symbol="DOGE/USDT:USDT",
        side="long",
        setup_id="setup-1",
        config_version_id="7",
        config_hash="cfg-hash",
        raw_stop_price=0.20,
        raw_target_price=0.35,
    )
    record = SimpleNamespace(
        intent_id="entry-unprotected",
        requested_quantity=2.0,
        contract_size=10.0,
        lot_step=0.1,
        min_contracts=0.1,
        tick_size=0.0001,
        max_contracts=100.0,
        reserved_risk_usdt=10.0,
        reservation_id="reservation-1",
        attach_algo_cl_ord_id="attach-1",
        protective_order_type="oco",
        strategy_policy={},
    )
    client = MagicMock()
    client.fetch_position.return_value = {
        "side": "long",
        "contracts": 2.0,
        "entry_price": 0.25,
        "position_id": "position-1",
        "entry_timestamp_ms": 1234,
    }
    client.fetch_pending_protection_algo_ids.return_value = []
    monkeypatch.setattr(
        live,
        "_verify_protection_with_retry",
        lambda *args, **kwargs: {"ok": False, "oco_count": 0},
    )
    monkeypatch.setattr(live.trade_log, "record_open", lambda *args, **kwargs: None)

    result = live._verify_and_finalize_entry(
        SimpleNamespace(user_dir=str(tmp_path)),
        client,
        intent,
        snapshot={},
        amount_coin=20.0,
        order={"status": "closed", "filled": 2.0, "average": 0.25},
        ledger=MagicMock(),
        epoch_store=MagicMock(),
        record=record,
        gate_result="rule_based_no_gpt_review",
    )

    assert result["executed"] is False
    assert result["reason"] == "critical_unprotected_position"
    assert "notification_event" not in result


def test_no_action_does_not_send_notification(monkeypatch):
    import candidate_c_trader_adapter as trader_adapter

    send = MagicMock()
    monkeypatch.setattr(trader_adapter.telegram_notify, "send", send)
    state = SimpleNamespace(notification_events=[])

    trader_adapter._dispatch_candidate_c_notifications(
        SimpleNamespace(logger=MagicMock()),
        state,
        {"intent_kind": "NoAction", "executed": False},
    )

    send.assert_not_called()


def test_prior_reduce_row_does_not_mask_final_close(tmp_path, monkeypatch):
    import candidate_c_cycle_reconciliation as cycle_recon

    symbol = "SOL/USDT:USDT"
    record = SimpleNamespace(
        intent_id="entry-1",
        kind=live.dec.INTENT_ENTRY,
        remote_submission_finalized=True,
    )
    action = SimpleNamespace(
        intent_id="exit-1",
        kind=live.dec.INTENT_EXIT,
        position_epoch="entry-1:exit-attempt",
    )
    epoch = _managed_epoch(symbol)
    ledger = MagicMock()
    ledger.find_protected_entry.return_value = (record, False)
    ledger.pending_intents.return_value = [action]
    epoch_store = MagicMock()
    epoch_store.get.return_value = epoch
    machine = MagicMock()
    reversal_store = MagicMock()
    reversal_store.get.return_value = machine
    state = SimpleNamespace(
        intent_ledger=ledger,
        epoch_store=epoch_store,
        reversal_store=reversal_store,
        notification_events=[],
    )
    client = MagicMock()
    client.fetch_position.return_value = None
    client.exchange.fetch_open_orders.return_value = []
    client.fetch_pending_protection_algo_ids.return_value = []
    client.fetch_realized_close_for_position.return_value = {
        "gross_pnl": 25.0,
        "fee": 0.5,
        "net_pnl": 24.5,
        "exit_price": 112.0,
        "funding_fee": 0.0,
        "source": "okx_realized",
    }
    cfg = SimpleNamespace(
        user_dir=str(tmp_path),
        CANDIDATE_C_LIVE_EXECUTE=True,
    )
    (tmp_path / "trades_log.jsonl").write_text(
        '{"type":"reduce","execution_id":"exit-1"}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        cycle_recon.adapter,
        "reconcile_pending_management",
        lambda *args, **kwargs: {"pending": False},
    )
    monkeypatch.setattr(
        cycle_recon.recon,
        "classify_external_position_change",
        lambda **kwargs: "take_profit",
    )
    cycle_recon.reconcile_managed_position(
        cfg, client, symbol, state, tick_ms=123456,
    )

    rows = [
        __import__("json").loads(line)
        for line in (tmp_path / "trades_log.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [row["type"] for row in rows] == ["reduce", "close"]
    assert rows[-1]["execution_id"] == "external-close:entry-1"
    assert state.notification_events[0]["event_type"] == "close"
    assert state.notification_events[0]["intent_id"] == "external-close:entry-1"


def test_notification_delivery_store_claims_once_across_restart(tmp_path):
    import candidate_c_notification_delivery as delivery

    event = {
        "event_type": "close",
        "symbol": "SOL/USDT:USDT",
        "intent_id": "exit-1",
        "net_pnl_usdt": 12.3,
    }
    store = delivery.NotificationDeliveryStore(
        str(tmp_path), "SOL/USDT:USDT",
    )

    assert store.initialize() is True
    assert store.enqueue(event) is True
    assert store.claim_pending() == [event]

    reloaded = delivery.NotificationDeliveryStore(
        str(tmp_path), "SOL/USDT:USDT",
    )
    assert reloaded.initialize() is False
    assert reloaded.claim_pending() == []
    assert reloaded.enqueue(event) is False
    assert reloaded.claim_pending() == []


def test_dispatch_deduplicates_event_across_restart(tmp_path, monkeypatch):
    import candidate_c_notification_delivery as delivery
    import candidate_c_trader_adapter as trader_adapter

    event = {
        "event_type": "entry",
        "symbol": "DOGE/USDT:USDT",
        "side": "long",
        "price": 0.25,
        "contracts": 2.0,
        "amount_coin": 20.0,
        "stop_price": 0.20,
        "target_price": 0.35,
        "intent_id": "entry-durable-1",
    }
    sent = []
    monkeypatch.setattr(
        trader_adapter.telegram_notify,
        "send",
        lambda cfg, text: sent.append(text),
    )
    store = delivery.NotificationDeliveryStore(
        str(tmp_path), "DOGE/USDT:USDT",
    )
    store.initialize()
    cfg = SimpleNamespace(logger=MagicMock())

    first_state = SimpleNamespace(
        notification_events=[],
        notification_store=store,
    )
    trader_adapter._dispatch_candidate_c_notifications(
        cfg, first_state, {"notification_event": event},
    )

    reloaded_store = delivery.NotificationDeliveryStore(
        str(tmp_path), "DOGE/USDT:USDT",
    )
    reloaded_store.initialize()
    second_state = SimpleNamespace(
        notification_events=[],
        notification_store=reloaded_store,
    )
    trader_adapter._dispatch_candidate_c_notifications(
        cfg, second_state, {"notification_event": event},
    )

    assert len(sent) == 1


def test_first_store_creation_suppresses_existing_open_entry(
    tmp_path, monkeypatch,
):
    import candidate_c_trader_adapter as trader_adapter

    existing = SimpleNamespace(intent_id="existing-entry-1")
    ledger = MagicMock()
    ledger.find_protected_entry.return_value = (existing, False)
    cfg = SimpleNamespace(user_dir=str(tmp_path), logger=MagicMock())
    original_save = (
        trader_adapter.notification_delivery.process_lock.save_json_atomic
    )
    save_spy = MagicMock(wraps=original_save)
    monkeypatch.setattr(
        trader_adapter.notification_delivery.process_lock,
        "save_json_atomic",
        save_spy,
    )

    store = trader_adapter._build_candidate_c_notification_store(
        cfg, "SOL/USDT:USDT", ledger,
    )

    assert store is not None
    assert store.contains("entry", "existing-entry-1") is True
    assert store.contains("entry", "future-entry-2") is False
    assert save_spy.call_count == 1


def test_first_store_creation_does_not_suppress_unfilled_entry(tmp_path):
    import candidate_c_trader_adapter as trader_adapter

    pending = SimpleNamespace(
        intent_id="unfilled-entry-1",
        kind=live.dec.INTENT_ENTRY,
    )
    ledger = MagicMock()
    ledger.find_protected_entry.return_value = (None, False)
    ledger.pending_intents.return_value = [pending]
    cfg = SimpleNamespace(user_dir=str(tmp_path), logger=MagicMock())

    store = trader_adapter._build_candidate_c_notification_store(
        cfg, "DOGE/USDT:USDT", ledger,
    )

    assert store is not None
    assert store.contains("entry", pending.intent_id) is False
    ledger.pending_intents.assert_not_called()


def test_atomic_baseline_failure_does_not_publish_empty_store(
    tmp_path, monkeypatch,
):
    import os
    import candidate_c_notification_delivery as delivery

    store = delivery.NotificationDeliveryStore(
        str(tmp_path), "SOL/USDT:USDT",
    )
    monkeypatch.setattr(
        delivery.process_lock,
        "save_json_atomic",
        MagicMock(side_effect=OSError("disk unavailable")),
    )

    with pytest.raises(OSError, match="disk unavailable"):
        store.initialize(suppressed=[
            ("entry", "existing-entry-1", "rollout"),
        ])

    assert os.path.exists(store.path) is False


def test_reconciliation_queue_persists_before_dispatch(tmp_path):
    import candidate_c_cycle_reconciliation as cycle_recon
    import candidate_c_notification_delivery as delivery

    store = delivery.NotificationDeliveryStore(
        str(tmp_path), "DOGE/USDT:USDT",
    )
    store.initialize()
    state = SimpleNamespace(
        notification_events=[],
        notification_store=store,
    )
    event = {
        "event_type": "reduce",
        "symbol": "DOGE/USDT:USDT",
        "intent_id": "reduce-before-restart",
    }

    cycle_recon._queue_notification(
        SimpleNamespace(logger=MagicMock()), state, event,
    )

    reloaded = delivery.NotificationDeliveryStore(
        str(tmp_path), "DOGE/USDT:USDT",
    )
    assert reloaded.claim_pending() == [event]


def test_recovered_reduce_reconstructs_notification_from_journal(
    tmp_path, monkeypatch,
):
    import candidate_c_intent_ledger as intent_ledger
    import trade_log

    symbol = "DOGE/USDT:USDT"
    action = SimpleNamespace(
        kind=live.dec.INTENT_REDUCE,
        position_epoch="entry-1:reduce-attempt",
        account_id="acct-1",
        symbol=symbol,
        requested_quantity=1.0,
        requested_stop_price=None,
        cl_ord_id="reduce-client-1",
        intent_id="reduce-1",
    )
    parent = SimpleNamespace(
        intent_id="entry-1",
        kind=live.dec.INTENT_ENTRY,
        strategy_id="candidate_c",
        account_id="acct-1",
        symbol=symbol,
        state=intent_ledger.IntentState.PROTECTION_PENDING.value,
        protective_algo_ids=["algo-1"],
    )
    epoch = _managed_epoch(symbol)
    epoch.remaining_contracts = 1.0
    ledger = MagicMock()
    ledger.pending_intents.return_value = [action]
    ledger.get.return_value = parent
    epoch_store = MagicMock()
    epoch_store.get.return_value = epoch
    client = MagicMock()
    client.symbol = symbol
    client.fetch_position.return_value = {
        "side": "long",
        "contracts": 1.0,
        "entry_price": 100.0,
        "position_id": "position-1",
        "entry_timestamp_ms": 1234,
    }
    client.fetch_pending_protection_algo_ids.return_value = ["algo-1"]
    client.exchange.fetch_open_orders.return_value = []
    client.fetch_order_status_by_client_id.return_value = {
        "status": "closed",
        "filled": 1.0,
    }
    client.fetch_protection_order_by_algo_id.return_value = {"sz": 1.0}
    client.exchange.market.return_value = {"precision": {"amount": 0.1}}
    cfg = SimpleNamespace(
        user_dir=str(tmp_path),
        CANDIDATE_C_LIVE_EXECUTE=True,
    )
    trade_log.record_reduce(
        str(tmp_path), symbol, "long", 100.0, 1.0, 50.0,
        reason="structural_derisk", dry_run=False, fee=1.0,
        pnl_source="estimated", close_price=105.0,
        strategy_group="candidate_c", execution_id="reduce-1",
    )

    result = live.reconcile_pending_management(
        cfg, client, ledger, epoch_store,
    )

    assert result["reconciled"] is True
    assert result["notification_event"] == {
        "event_type": "reduce",
        "symbol": symbol,
        "side": "long",
        "entry_price": 100.0,
        "price": 105.0,
        "contracts": 1.0,
        "amount_coin": 10.0,
        "remaining_contracts": 1.0,
        "gross_pnl_usdt": 50.0,
        "fee_usdt": 1.0,
        "net_pnl_usdt": 49.0,
        "reason": "structural_derisk",
        "intent_id": "reduce-1",
    }

    monkeypatch.setattr(
        live.notification_delivery,
        "trade_event",
        MagicMock(side_effect=OSError("notification journal unavailable")),
    )
    failed_notification = live.reconcile_pending_management(
        cfg, client, ledger, epoch_store,
    )

    assert failed_notification["reconciled"] is True
    assert failed_notification["pending"] is False
    assert failed_notification.get("critical") is not True
    assert "notification_event" not in failed_notification


def test_journaled_close_is_recovered_when_delivery_not_attempted(
    tmp_path, monkeypatch,
):
    import candidate_c_cycle_reconciliation as cycle_recon
    import candidate_c_notification_delivery as delivery
    import trade_log

    symbol = "SOL/USDT:USDT"
    record = SimpleNamespace(
        intent_id="entry-1",
        kind=live.dec.INTENT_ENTRY,
        remote_submission_finalized=True,
    )
    action = SimpleNamespace(
        intent_id="exit-1",
        kind=live.dec.INTENT_EXIT,
        position_epoch="entry-1:exit-attempt",
    )
    epoch = _managed_epoch(symbol)
    ledger = MagicMock()
    ledger.find_protected_entry.return_value = (record, False)
    ledger.pending_intents.return_value = [action]
    epoch_store = MagicMock()
    epoch_store.get.return_value = epoch
    reversal_store = MagicMock()
    reversal_store.get.return_value = MagicMock()
    store = delivery.NotificationDeliveryStore(str(tmp_path), symbol)
    store.initialize()
    state = SimpleNamespace(
        intent_ledger=ledger,
        epoch_store=epoch_store,
        reversal_store=reversal_store,
        notification_events=[],
        notification_store=store,
    )
    client = MagicMock()
    client.fetch_position.return_value = None
    client.exchange.fetch_open_orders.return_value = []
    client.fetch_pending_protection_algo_ids.return_value = []
    cfg = SimpleNamespace(
        user_dir=str(tmp_path),
        CANDIDATE_C_LIVE_EXECUTE=True,
        logger=MagicMock(),
    )
    trade_log.record_close(
        str(tmp_path), symbol, "long", 100.0, 2.0, 100.0,
        reason="manual_close_15m", dry_run=False, fee=2.0,
        pnl_source="okx_realized", close_price=110.0,
        okx_net_pnl=98.0, funding_fee=0.0,
        strategy_group="candidate_c", execution_id="exit-1",
    )
    monkeypatch.setattr(
        cycle_recon.adapter,
        "reconcile_pending_management",
        lambda *args, **kwargs: {"pending": False},
    )

    cycle_recon.reconcile_managed_position(
        cfg, client, symbol, state, tick_ms=123456,
    )

    assert state.notification_events == [{
        "event_type": "close",
        "symbol": symbol,
        "side": "long",
        "entry_price": 100.0,
        "price": 110.0,
        "contracts": 2.0,
        "amount_coin": 20.0,
        "gross_pnl_usdt": 100.0,
        "fee_usdt": 2.0,
        "net_pnl_usdt": 98.0,
        "reason": "manual_close_15m",
        "intent_id": "exit-1",
        "funding_fee_usdt": 0.0,
    }]
    assert store.claim_pending() == state.notification_events

    state.notification_events = []
    failing_store = MagicMock()
    failing_store.contains.return_value = False
    state.notification_store = failing_store
    monkeypatch.setattr(
        cycle_recon.notification_delivery,
        "trade_event",
        MagicMock(side_effect=OSError("notification journal unavailable")),
    )

    recovered = cycle_recon.reconcile_managed_position(
        cfg, client, symbol, state, tick_ms=123457,
    )

    assert recovered == {
        "reason": "external_flat_reconciled",
        "flat_confirmed": True,
    }
    assert state.notification_events == []
    state.intent_ledger.mark_terminal.assert_called()
    state.epoch_store.discard.assert_called_with(record.intent_id)


def test_protection_catch_up_queues_new_entry_notification(
    tmp_path, monkeypatch,
):
    import candidate_c_cycle_reconciliation as cycle_recon
    import candidate_c_notification_delivery as delivery
    import trade_log

    symbol = "DOGE/USDT:USDT"
    record = SimpleNamespace(
        intent_id="entry-catch-up-1",
        kind=live.dec.INTENT_ENTRY,
        remote_submission_finalized=True,
    )
    epoch = _managed_epoch(symbol)
    epoch.entry_intent_id = record.intent_id
    ledger = MagicMock()
    ledger.find_protected_entry.return_value = (record, False)
    epoch_store = MagicMock()
    epoch_store.get.return_value = epoch
    reversal_store = MagicMock()
    machine = MagicMock()
    machine.state = cycle_recon.rsm.State.LONG
    reversal_store.get.return_value = machine
    store = delivery.NotificationDeliveryStore(str(tmp_path), symbol)
    store.initialize()
    state = SimpleNamespace(
        intent_ledger=ledger,
        epoch_store=epoch_store,
        reversal_store=reversal_store,
        notification_events=[],
        notification_store=store,
    )
    client = MagicMock()
    client.fetch_position.return_value = {
        "side": "long",
        "contracts": 2.0,
        "entry_price": 100.0,
        "position_id": "position-1",
        "entry_timestamp_ms": 1234,
    }
    client.exchange.fetch_open_orders.return_value = []
    client.fetch_pending_protection_algo_ids.return_value = ["algo-1"]
    cfg = SimpleNamespace(
        user_dir=str(tmp_path),
        CANDIDATE_C_LIVE_EXECUTE=True,
        logger=MagicMock(),
    )
    trade_log.record_open(
        str(tmp_path), symbol, "long", 100.0, 2.0, False,
        sl_price=95.0, tp_price=110.0,
        strategy_group="candidate_c",
        execution_id=record.intent_id,
    )
    monkeypatch.setattr(
        cycle_recon.adapter,
        "reconcile_pending_management",
        lambda *args, **kwargs: {"pending": False},
    )
    monkeypatch.setattr(
        cycle_recon.ownership,
        "validate_candidate_c_position_owner",
        lambda *args, **kwargs: {
            "allowed": True,
            "entry_record": record,
            "needs_protected_catch_up": ["algo-1"],
        },
    )
    monkeypatch.setattr(
        cycle_recon.order_safety,
        "verify_protection",
        lambda *args, **kwargs: {"ok": True},
    )

    cycle_recon.reconcile_managed_position(
        cfg, client, symbol, state, tick_ms=123456,
    )

    assert state.notification_events == [{
        "event_type": "entry",
        "symbol": symbol,
        "side": "long",
        "price": 100.0,
        "contracts": 2.0,
        "amount_coin": 20.0,
        "stop_price": 95.0,
        "target_price": 110.0,
        "intent_id": record.intent_id,
    }]
    assert store.claim_pending() == state.notification_events

    state.notification_events = []
    failing_store = MagicMock()
    failing_store.contains.return_value = False
    state.notification_store = failing_store
    machine.reset_mock()
    monkeypatch.setattr(
        cycle_recon.notification_delivery,
        "trade_event",
        MagicMock(side_effect=OSError("notification journal unavailable")),
    )

    reconciled = cycle_recon.reconcile_managed_position(
        cfg, client, symbol, state, tick_ms=123457,
    )

    assert reconciled["reason"] == "owned_position_reconciled"
    assert reconciled.get("critical") is not True
    assert state.notification_events == []
    machine.reconcile_authoritative_position.assert_called_once()


def test_malformed_pnl_formats_as_unknown_instead_of_dropping_alert():
    import telegram_notify

    text = telegram_notify.format_candidate_c_event({
        "event_type": "close",
        "symbol": "SOL/USDT:USDT",
        "side": "long",
        "price": 110.0,
        "contracts": 1.0,
        "amount_coin": 1.0,
        "gross_pnl_usdt": "not-a-number",
        "net_pnl_usdt": "not-a-number",
        "reason": "manual_close_15m",
        "intent_id": "exit-1",
    })

    assert "순손익=UNKNOWN USDT" in text
