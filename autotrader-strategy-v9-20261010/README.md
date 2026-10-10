# v9 strategy and protection correction

Bounded changes to v8: preserve the lifecycle risk denominator and exclude pre-fill/prior-trade MFE; share observed same-trade giveback/reduction evidence with the existing two position-review calls; permit existing deterministic breakdown defense during ordinary post-reduction cooldown while corrupt state/ordinary/AI-only signals remain blocked; correct nested TP/notional dashboard fields and distinguish latest entry plan from actual protection.

Entry chasing thresholds, sizes, provider exceptions, learning activation and existing positions/protective orders are not changed by deployment. No new provider request is added. Historical profitability is unverified; this patch is not a profit guarantee or parameter optimization.

Use the existing integrated-audit CHECKPOINT.md to resume; the single-use deployment operator guards exact v8 release/hash baseline, active engines, unchanged settings and exact exchange position/OCO snapshots, with rollback. Private live replay data and account state are not included here.
