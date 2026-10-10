# Risk-adaptive partial reductions v11

Reuse completed v10 release and qualification; never replay v10 deployment.

1. CORE: Gemini risk_level + suggested_reduce_fraction; GPT decides explicit reduce_fraction 0.05..0.50 of original contracts. Hold/close remain separate. No confidence-to-size or chart veto. Existing cumulative 50% cap remains. Missing/invalid fraction fails closed.
2. Candidate C: confirmed chart risk uses adverse 1H/5m structure, 4H volatility relative to initial R, loss depth and MFE giveback. Continuous bounded fractions: profit-taking/profit-protection 10..50% original; structural loss defense 25..75% remaining. Existing trigger timing, one-shot lifecycle, hard exits and initial SL/TP unchanged. Runtime opt-in uses immutable active strategy snapshot; backtest uses same explicit opt-in.
3. Quantity execution: original vs remaining basis explicit; floor lot sizes; no partial order that empties position; durable cumulative target survives terminal partial fill/restart; same confirmed bar dedup. Existing pending v10 orders reconcile unchanged.
4. Tests first: risk long/short symmetry, changing risk changes fraction, missing/invalid AI ratios, actual adaptive CORE executor with exact same OCO ID/SL/TP and remaining size, partial fill/restart/dedup/cap; Candidate decide and backtest/live parity.
5. Qualify five existing suites once per source revision. Re-pin only qualified Candidate source hashes. No paid AI/test trades/Telegram tests.
6. Guarded unique v11 preflight/deploy with one named opt-in setting; preserve exact actual positions/OCO and all other settings; persist prepared release, dispatch record and checkpoint after each phase.

Production baseline: /opt/autotrader-releases/strategy_authority_v10_20261010T221704KST PID174157, active NRestarts0; readonly verified6 positions6 exact OCO0 pending. V10 complete1915tests+130subtests.
