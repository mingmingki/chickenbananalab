# CORE v33-77 candidate — not promoted
This review bundle contains runtime changes and reproducible research tools. It is not an operating release.

Runtime changes:
- Use confirmed candle evidence with an as-of timestamp pinned before market-data collection.
- Supply confirmed multi-timeframe structure, real swing prices and proposed-plan post-cost economics to entry verification.
- Keep independent, veto-only GPT management verification with lifecycle/quantity/action approval receipts.
- Preserve existing hard stops, emergency protections, account risk limits and order-lock/deduplication rules.

The runtime patch is relative to the archived production snapshot identified locally as 9226917. The runtime candidate source commit is ea332ce; additional research tools are in 0a633e0. Apply and validate against the correct snapshot before any deployment.

Economic promotion is blocked. Recorded-entry selection and fixed-rule paper screens are not full closed-loop AI strategy backtests. Review the source and tests; do not activate this candidate on a live account from this bundle.

Verification: the final full suite completed 2391 passing tests, 23 failures, and 27 passing subtests. There are 22 failures already present in the baseline. The additional failure asserts a fixed XRP risk factor against changing live ledger data: the unchanged allocator module returns the same current result in baseline and candidate. The new management, economics, scorer, and trend research tests pass. Existing failures include unavailable Node and old fixture/client contract mismatches. Do not describe the full suite as green.

Research assumptions and limits are documented in the scripts. Historical replay can make paid provider API calls when explicitly run with the script execution option; price collection uses public exchange GET requests. These tools have no exchange order authority. No replay runs automatically from importing this bundle.
