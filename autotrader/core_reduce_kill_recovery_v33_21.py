"""Fail-closed, exact-reason CORE reduce latch reconciliation. No trading orders."""
def try_clear(cfg, client, symbol):
    import candidate_c_hybrid_ownership as cc_ownership
    import core_kill_switch
    import reduce_v2_state
    import trade_log
    expected = f"{symbol} POSITION_AI_REDUCE_50 보호주문 재설정 실패"
    if core_kill_switch.get_reason(cfg.user_dir) != expected:
        return False
    # Existing account-level lock prevents concurrent broker order transitions.
    with cc_ownership.account_order_lock(cfg.user_dir):
        if core_kill_switch.get_reason(cfg.user_dir) != expected:
            return False
        try:
            if client.fetch_position() is not None:
                return False
            pending=(reduce_v2_state.get(cfg.user_dir,symbol) or {}).get("pending_order")
            if pending is not None:
                return False
            if trade_log.last_unclosed_open(cfg.user_dir,symbol) is not None:
                return False
            orders=client.exchange.fetch_open_orders(client.symbol)
            algos=client.fetch_pending_protection_algo_ids()
            if not isinstance(orders,list) or orders:
                return False
            if not isinstance(algos,list) or algos:
                return False
            # Re-read flat: reject a position that appears during broker queries.
            if client.fetch_position() is not None:
                return False
            cleared=core_kill_switch.clear_reconciled_flat_reduce(
                cfg.user_dir,symbol,verified_exchange_flat=True,
                no_open_orders=True,no_live_algos=True,no_pending_reduces=True)
            if cleared:
                cfg.logger.warning("[%s] CORE_REDUCE_FLAT_KILL_RECOVERED_V33_21: exchange flat, no regular/algo orders, journal closed",symbol)
            return cleared
        except Exception as exc:
            cfg.logger.warning("[%s] CORE_REDUCE_FLAT_KILL_RECOVERY_DEFERRED_V33_21: %s",symbol,type(exc).__name__)
            return False
