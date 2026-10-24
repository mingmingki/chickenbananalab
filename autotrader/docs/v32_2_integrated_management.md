# CORE v32-2 Integrated management authority (LIVE limited)
Order-of-operation diagram:
- existing emergency-close-near-stop guard first (exchange SL/TP always live)
- unresolved CORE entry reconciliation (existing)
- REDUCE fill -> OCO reconciliation, if pending, stop discretionary run
- ADD fill -> OCO reconciliation, if pending, stop discretionary run
- discretionary MFE/partial-profit/ratcheting and Gemini/AI only after both clear
- generic external-close journaling delayed until both clear to avoid duplicate accounting
- user-facing symbol state: `core_recovery_priority`: READY / RECOVERY_PENDING / STATE_UNKNOWN
- market decision action precedence beyond recovered state remains unchanged.
- no new orders, price changes, risk limits, account-level restrictions or stop widening.
- unknown/corrupted source is fail-closed for new discretionary actions, existing exchange protection remains.
- known limitation: this does not make OKX reduce+OCO amend a single atomic exchange transaction; a crash between steps still requires replay and user notification.
