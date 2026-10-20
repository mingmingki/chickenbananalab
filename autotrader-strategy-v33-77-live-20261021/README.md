# CORE v33-77 protected strategy trial
This source bundle implements a user-directed strategy trial. Positive live profitability and a complete closed-loop strategy backtest have not been established.

Runtime source: 8285c4c2a66bb3f07d3c92822a02d0e675e0a841. The patch applies to archived production source snapshot 9226917, not the repository's unrelated main application.

Changes:
- Evaluate the original entry SL/TP plan after costs, with confirmed multi-timeframe evidence; no margin or leverage enlargement.
- With an actual full-size protective stop and known lifecycle MFE, defer discretionary full exits/loss reductions only while upper structure and confirmed recovery remain valid.
- Preserve independent GPT review for explicit thesis invalidation, unknown evidence, profit giveback and current quote shocks.
- Preserve emergency protection, physical stops, bounded partials, lifecycle receipts and order locks.
- Pin timestamps before all CORE candle fetches, including fast review and supplemental frames; normalize UTC and exclude every post-snapshot row.

Validation: 2442 passing tests, 23 existing failures, 27 passing subtests. No added failing test names. The focused management/cycle regression set passes 181 tests. The full suite is not green; existing failures include missing Node and legacy fixture/client contracts.

This public bundle contains source and synthetic tests only. Private ledgers, account settings, model input/output archives and operating position data are excluded. Deployment and profitability are separate claims.

Operational acceptance: the user-directed release was activated and both existing engines resumed successfully. This does not establish profitability.
