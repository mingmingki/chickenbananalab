# v33-1 AI exit / MFE PnL evidence audit (no trading policy change)

Read-only ledger inspection found CORE 374 lifetime completed cycles (net -473.23 USDT), Candidate C 45 (-268.07).
Since Oct 10 CORE 58 cycles net +2.62 USDT; last 24h CORE 17 cycles net -69.64 USDT.
Among last-24h CORE 10 AI full-close lifecycles, all 10 had net losses (-69.21 USDT).

AI exit continuation shadow since Oct 10: 35 AI exits, but just 5 had directly verified journal close_price and continuous post-close bar paths with protection info suitable for a conservative holding counterfactual. 30 exits lacked verified close_price; the 30-minute hold policy's estimated improvement of +29.07 USDT on the *5* eligible transactions is dominated by one XRP +36.89 USDT example; other four together were negative. This is not credible evidence to suppress AI exits.

Audit of all 138 completed AI full closes: 28 had no recorded close_price, gross pnl==0 placeholder but old backfill falsely invented a price as entry_price.
Version v33-1 changes only exit_reentry_shadow backfill and evidence fields, rejects non-verified price-based counterfactuals, never touches exchange orders, CORE/Candidate C policy, stop/TP, entries, configured leverage.
Append-only shadow may receive corrected rows on next existing analyst backfill. No original journal is rewritten; verified realized net and actual reentries remain the source of truth. Monitoring before and after deployment needs to verify unchanged OCO for every live position.
