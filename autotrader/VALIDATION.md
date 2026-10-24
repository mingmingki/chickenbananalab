# Integrated CORE verification

Implemented live controller, market/AI adapters, symbol loop routing, durable legacy-writer guards, explicit manual-close routing, and cost-aware daily-loss accounting. OFF remains the default; deployment prepares the approved account's feature setting only after stopping the service. Deployment never starts trading.

Independent review found four integration defects. Targeted regressions and fixes cover realized-loss accounting, stop tick rounding, terminal partial/rejected orders and emergency order fill recovery. Follow-up corrections preserve PROBE after zero-fill ADD, accumulate actual partial reductions toward each 25% stage, and allow explicit manual closure of terminal emergency residuals. Unknown orders remain reserved without resubmission. The legacy 90% stop-distance /25-second emergency confirmation is retained.

Local final suite: 111 tests passed, one native-indicator dependency skip. Bridge tests are now included under tests/ (27 tests). Earlier isolated VM integration checkpoint: 175 passed,27 subtests in5.46s, before final review corrections. Final VM result is recorded in progress.md and the deployment report.

All execution tests use deterministic substitutes. VM tests run in a separate source-only directory with AUTOTRADER_PROJECT_DIR redirected there. No production credentials or live order calls are used. Passing tests do not establish profitability or actual exchange latency.

See OPERATIONS.md for startup, ownership transition, accounting display limits, and recovery constraints. Preserve all account state and exchange protections.
