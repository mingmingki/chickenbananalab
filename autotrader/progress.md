# SDD ledger — plan: docs/superpowers/plans/2026-09-20-core-unified-position.md

Current ruling: user explicitly separates stopping/deploying from starting trading himself. Finish integration, stop the systemd service (not dashboard /api/stop, which closes positions), deploy a verified release, leave service stopped. No order API or trading start is authorized for agent execution.
Current execution ruling: dispatching-parallel-agents skill applied to independent market/AI and live execution files with explicit ownership; root owns loop/config/legacy routing and deployment. A final gpt-6-astra review found four concrete integration gaps, now being fixed with targeted regressions: accounting/daily-loss visibility, tick precision, terminal partial recovery, emergency fill recovery.
Integration checkpoint: real feed/AI/controller and trader routing implemented. Local75 tests pass1skip; isolated VM175 tests and27subtests pass in5.46s. This precedes final review fixes and is not deployment completion.

Task 6: executor kernel 7 behavioral RED failures → GREEN; local suite 26/26. Production OKX port, owned-protection proof, actual emergency/manual routing remain incomplete, so this is not deploy-ready LIVE.
Investigation: reducer MARK overwrote RECONCILING and reducer terminal FILL cleared pending before protection, unlike durable Store. Reproducing those boundary faults before changing the reducer; one owner of completion is protection confirmation.
Ruling: actual trading activation is left to the operator; do not enable LIVE or submit financial orders from this session. Code-only staging must be explicitly described as inactive, not a live deployment.

Baseline: live release core_medium_reversal_live_20260920T0310Z; remote suite 100 passed in 5.36s. No live changes made.

Ruling: user now requests deployment, superseding the plan's separate deployment-consent step; safe verification remains required. A test pass is not profitability evidence.
Ruling: production release has no git metadata and git is unavailable on VM. Use isolated source directory; do not commit/push. Skill git ledger helpers cannot run against this release; maintain this ledger manually.
Ruling: local runtime lacks pytest. Write executable unittest cases (pytest-compatible) and run locally with unittest, then run full pytest in isolated VM source. No dependency installation required.
Pre-flight 1→2→3: policy version, IDs and UTC ms shared. Candidate schema must include snapshot_id/generation/expires_ms and executable bid/ask.
Pre-flight 4→5→6: state quantity stored as decimal strings; pending reservation does not change filled quantity. Fill application is the sole quantity mutator.
Pre-flight 6→7: account order lock, manual controls and owner must all be re-read before submission. Unknown state must never resubmit.
Pre-flight 3→7→8: AI callbacks cannot submit; event timestamps split queue/model/order latency.
Task 1: complete locally. 4 policy tests RED (four assertions) → GREEN. Quantity units at boundary confirmed: legacy sizing returns coin amounts, OKX positions/order status return contracts. Full local suite 4/4.
Task 2: complete locally. Candidate API shell RED → GREEN. Full local suite 7/7. No production integration yet.
Task 3: initial broker tests RED→GREEN; full local suite 11/11. Broader replacement/stop integration still required.
Task 4: initial state tests RED→GREEN; full local suite 16/16. Adoption and order adapter integration still required.
Task 5: initial store tests RED→GREEN; full local suite 19/19. Terminal partial cancellation deliberately remains reconciliation-blocked.
Ruling: fill deduplication key is (intent_id, trade_id), not trade_id alone, since exchange IDs can be scoped by instrument; every intent belongs to one symbol. If wrong, collision could otherwise suppress a real fill.

Task 4 follow-up: version boundary, partial-terminal retention, protection-confirmed stage, unknown-order MARK and ADOPT restriction tests RED→GREEN; local suite29/29.
Task 7 kernel only: 5 tests RED→GREEN; local suite34/34. Risk tick progresses while AI waits; OFF no work; SHADOW no production dependencies; clock regression invalidates. This is not production routing.
Ruling: retain explicit LIVE rejection until real execution adapters and ownership handoff are qualified. Cost: requested operational deployment remains blocked, rather than risk two order writers or an unprotected fill.
Ruling: do not delete this no-git workspace at skill finish; archive code/ledger durably instead. Cost if wrong: extra retained source, preferable to irreversible loss of uncommitted work.

Independent review (gpt-6-astra, read-only) found 3 Critical and 2 Important kernel defects; verdict not production-ready. One TDD fix pass followed, no repeat reviewer.
Final: fixed partial-fill protection bypass — test_partial_entry_is_protected_before_waiting_for_terminal and test_terminal_partial_add_also_checks_protection RED→GREEN.
Final: fixed cross-side/cross-symbol approval reuse — test_opposite_side_approval_cannot_be_used_for_intent and test_cross_symbol_approval_cannot_be_used_for_intent RED→GREEN.
Final: fixed missing protection exception escalation — test_protection_exception_still_escalates RED→GREEN.
Final: fixed risk tick suppression during wall-clock rollback — test_clock_rollback_never_stops_monotonic_risk_tick RED→GREEN.
Final: fixed stale-cache minute deduplication — test_late_cache_refresh_is_not_lost_for_whole_minute RED→GREEN.
Final local verification: python -m unittest discover -s tests -v → 41/41 PASS; compileall over all 7 new modules → exit0.
Final: Ruling: reviewer set aside absent real OKX protection ownership/reduceOnly/emergency/manual/account-lock integration; treat all as production blockers, not accepted risks. Cost: cannot deploy operating strategy yet.
Final: Ruling: reviewer set aside real AI timeouts, authentic market freshness, legacy routing and durable ownership; retain LIVE rejection until those are qualified. Cost: async kernel tests do not yet improve live latency.
Final: Ruling: reviewer set aside profitability/replay/real latency due missing evidence; make no economic or live performance claim. Cost: strategy benefit remains unknown.
Final: Ruling: reviewer did not review untouched baseline. Fresh isolated baseline pytest ran 100/100 (6.68s, exit0); no existing source edits. Cost: not a complete audit of old behavior.
Deferred minors: none reported.
Final isolated VM verification: /tmp/core-unified-verify.8opBb1mM source-only copy plus seven new modules; pytest → 141 passed,14 subtests passed in4.93s,exit0; compileall exit0. Active symlink unchanged at core_medium_reversal_live_20260920T0310Z. This is co-installation regression verification, not production-adapter integration. No operating deployment completed.

Continuation: user requested proceeding. Added durable owner table/CAS, read-only two-inventory handoff with flat/order/protection/pending proof, generation invalidation on rollback, no fallback to legacy if unified exposure exists without owner.
Continuation: added strict OKX position lifecycle identity (posId + entry time + side), exact owned stop ID/coverage proof, and order/fill normalization that never accepts the request's client ID as response evidence.
Continuation: LegacyInventoryPort now reads real existing OKX client/CCXT interfaces and existing CORE reduce/add/manual ledgers; queries all positions to avoid hiding hedged exposure. No exchange mutation methods are exposed.
Ruling: handoff currently permits only verified-flat/no-orders/no-protection inventory; it does not guess R/MFE or adopt a held legacy position. Cost: existing held positions cannot use new management until a separately verified adoption path exists.
Ruling: incomplete fill pages remain reconciliation-blocked; 100-row fetch is not claimed as exhaustive pagination. Cost: large multi-fill orders may need further retrieval before completion.
Continuation TDD: handoff initial4 failures→GREEN; OKX contract5 failures→GREEN; inventory8 failures→GREEN; missing-owner fallback regression1 failure→GREEN. Previous tests retained.
Continuation independent targeted review: 3 Important defects,1 Minor; no Critical in inactive modules. One fix pass reproduced4 failures then corrected zero-fill foreign-order identity, unresolved zero-exposure ownership transfer, and contradictory/unknown posSide protection proof.
Ruling: strict protection proof currently accepts only explicitly evidenced net position mode; do not infer net mode from a long/short label or silently support hedge mode. Cost: adapters lacking raw posSide cannot confirm protection.
Final: minor (deferred): matched_ids diagnostic may include recorded IDs whose stops were rejected from coverage; it is not used for cancellation or execution. Coverage calculation remains strict.
Final: Ruling: reviewer declined live writing/runtime/AI/market/deployment, pagination beyond declared blocking, economics and unrelated baseline/kernel. These remain unqualified; no claim of complete operational integration or profitable behavior. Cost: requested live behavior is still unavailable.
Continuation final verification: local65/65; isolated VM165 passed,27 subtests passed in5.54s,exit0; compileall exit0. Active release and trader.py baseline hash unchanged. No actual order calls, owner transfer against production, live-mode setting change, service restart, commit or push.
