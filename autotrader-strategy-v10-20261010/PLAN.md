# Strategy authority V10 implementation plan
Goal: implement the approved explicit group authority contract.
Execution: native, reuse V9; small phases and checkpoints.
1. Candidate: chart-only flag overrides historical AI toggles at config AND entry boundary. Retain chart/risk geometry and all reduce/exit/OCO reconciliation. Tests prohibit both AI calls even old flags true.
2. CORE: scoped AI policy and explicit Gemini plan selection for approval/timeout; no chart fallback; pre-AI shared risk contract and metrics; final freshness observation only. Tests long/short approval/timeout/wait/reject and hard limits.
3. CORE held: enable periodic AI review, no chart-only reductions/stop/proximity exits, no local strategy downgrade of approved reduce/close; retain lifecycle/bar/fill/protection idempotency and capped staged reductions.
4. UI/API state identifies both authorities and timeout/negative verdict behavior.
5. Red/green focused tests, all five complete suites, independent review, read-only real-log replay. Then unique durable V10 deployment with exact V9 baseline and setting migration backup. Preserve positions/OCO, once-only restart/resume, postverify/checkpoint.
