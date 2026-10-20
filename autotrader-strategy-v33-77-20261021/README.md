# CORE v33-77 candidate — not promoted
This review bundle contains runtime changes and reproducible research tools. It is not an operating release.

Runtime changes:
- Use confirmed candle evidence with an as-of timestamp pinned before market-data collection.
- Supply confirmed multi-timeframe structure, real swing prices and proposed-plan post-cost economics to entry verification.
- Keep independent, veto-only GPT management verification with lifecycle/quantity/action approval receipts.
- Preserve existing hard stops, emergency protections, account risk limits and order-lock/deduplication rules.

The runtime patch is relative to the archived production snapshot identified locally as9226917. The runtime candidate source commit isea332ce; additional research tools are in0a633e0. Apply and validate against the correct snapshot before any deployment.

Economic promotion is blocked. Recorded-entry selection and fixed-rule paper screens are not full closed-loop AI strategy backtests. Review the source and tests; do not activate this candidate on a live account from this bundle.

Verification: the full isolated source suite completed2379 passing tests and22 pre-existing failures with no added failing test names. The additional scorer and trend research tests pass. Existing failures include unavailable Node and old fixture/client contract mismatches. Do not describe the full suite as green.

Research assumptions and limits are documented in the scripts. All sample API and market-data calls are offline validation inputs; they have no exchange order authority.
