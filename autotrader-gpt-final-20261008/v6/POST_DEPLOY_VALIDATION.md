# Entry / Cost V6 — Actual LIVE deployment record, 2026-10-09 KST

This post-deployment record supersedes the candidate-phase status in v6/REPORT.md and DAILY_STATUS.md prior revisions. Do not conflate unit tests with natural fills or profitability.

## Verified execution
- Initial V6: 03:43:04 KST, `/opt/autotrader-releases/entry_cost_v6_20261009T034304KST`, 20 code files changed; `operation=deployed`; service PID 105555, NRestarts=0; unchanged user settings SHA.
- Final sizing-observability / parity follow-up: 03:53:10 KST, **`/opt/autotrader-releases/entry_cost_v6_20261009T035310KST`**, 2 source files changed, rollback backup `/var/lib/autotrader/deployment-backups/entry-cost-v6-20261008T185310Z`; `operation=deployed`, user settings SHA unchanged.
- Final actual service read-only: active, PID 106118, NRestarts 0.
- Final source SHA256 candidate_c_hybrid_live_adapter.py = `ac838a99e378897591ba7f0238db74325751c9754780a40df5e17a3ba96806dd`; candidate_c_runtime.py = `0ccf7cfaf6509a5d1cfc9708994c1c6e783bc3d3720004f381ac5e343bc102ea` (matching current source manifest).
- Authenticated `/api/state` and `/api/candidate_c_state` after final follow-up: CORE running (4 symbols), Candidate C running/live (2 symbols); GPT entry gate ON, confirmed typed timeout bypass ON, paid shadow OFF.
- Existing Telegram bot/recipient confirmation `ok=true`, message id **963**, 2026-10-09 03:46 KST, sent once during initial V6 release. No duplicate confirmation sent for follow-up.
- Tests at qualified candidate stage: tests 1314, gemini_checks 230, negative_guard_checks 41, rollback_checks 37, rollback_full_checks 257 = **1879 tests and 130 subtests passed**; bare pytest 1314 passed /27 subtests; JavaScript syntax 2 passed; source/event and partial parity checks passed. Local full LIVE parity is false only because production-only preregistration file is not available in local source; actual deployment operator checked full LIVE parity against deployed runtime.
- GitHub code branch `infra/autotrader-entry-cost-v6-20261009`; commit `e2ac00e` pushed. This document is a later evidence-only commit.
- Sensitive credentials, API tokens, Telegram recipient ID, live account DB, account configuration and test-generated private keys are excluded from commits.

## Incomplete evidence / observation
- Deployment and API loops are healthy, but **natural new signal→real fill→exchange protective OCO** after final follow-up has not been witnessed and audited end-to-end.
- No claim that profitability improved, −500 USDT was recovered, 50% AI cost was saved, or strategy machine learning is actively changing orders. Monthly cost estimates based on older V5 windows are not post-V6 savings.
- Some historic protective algo IDs and logs show C profit-lock updates; they do not alone prove all currently held positions have live verified SL/TP. Direct read-only position/order reconciliation remains necessary.
- Outbox delivery unknown can occur on ambiguous Telegram acknowledgement, and actual blocked-event delivery must be checked after natural events.
