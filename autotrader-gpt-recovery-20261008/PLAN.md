# CORE GPT entry recovery implementation plan

Goal: implement the user-specified CORE entry policy and durable outcome notifications on the archived v4 source, preserving its final safety checks.
Architecture: typed GPT error provenance; distinct raw/gate/order states; final order checks remain authoritative. SQLite event outbox delivers through existing Telegram; durable client order receipts prevent repeat submission.
Execution: inline using debugging, TDD, verification and writing-plans skills. User explicitly authorized implementation/deployment and waived repeat review/approval prompts.

- [x] Recover Git/handoff/artifacts; attempt existing gcloud, direct SSH and authorized local connector.
- [x] Write RED tests for typed timeout-only CORE bypass, original exit plan, approved order routing and final safety failures.
- [x] Implement gate policy and paid-shadow opt-in; retain Gemini trigger timing and held-position reviews.
- [x] Write RED tests and implement durable events, Telegram bounded delivery/dedup, order receipts and follow-up states.
- [ ] Replay actual cases when source logs become available; never manufacture historical attribution.
- [x] Run targeted integration and all available Oct5 regression comparisons/parity/hash; record failures and missing current production dependencies.
- [ ] Full current production regression and strategy hash qualification (latest dependencies unavailable).
- [x] Review and build source-only patch; baseline hash preflight rejects mismatched sources.
- [ ] Exact running source audit, full current qualification/deployment/rollback (network blocked).
- [ ] Verify LIVE, Telegram receiver, positions/protections, source hashes/PID; publish only related files and report uncompleted actions accurately.

Blocked external actions: local DNS/network restricted, direct SSH denied; Desktop Commander start_process auto-review rejected (approval policy never). LIVE full source and Oct8 incident logs unavailable. No production state/config/order changes made.
