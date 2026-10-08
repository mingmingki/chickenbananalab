# Independent candidate code review

**Verdict: Ready for the coordinator's final release checks.** No outstanding Critical, Important, or Minor findings remain in the reviewed candidate. Three Important defects and two Minor presentation defects were found, reproduced, fixed by the coordinator, and reviewed again. This verdict covers source behavior and offline focused verification; it does not claim remote deployment, real exchange/Telegram delivery, or whole-suite release checks were performed by this reviewer.

## Scope and evidence

Candidate file contents were compared with the preserved live v4 production snapshot, without Git SHAs. Reviewed scope: config, CORE entry policy/events/orders, affected trader entry/reversal/recovery functions, OKX identity/fill wrappers, GPT analyzer, Telegram formatter/outbox, entry logs/UI, Gemini/OpenAI usage metadata and operating cost summary. Separately owned unified engine behavior is outside this timeout implementation. Preserved production was read only. The reviewer made no candidate source edits, spawned no agents, and made no real provider, exchange-order, or Telegram network calls.

Final independent focused verification: **147 passed in 2.50 seconds** across `test_core_entry_pipeline`, `test_core_gpt_timeout_policy`, `test_core_entry_notifications`, `test_ai_cost_metadata_recovery`, `test_core_manual_entry`, `test_core_manual_entry_cooldown_ui`, `test_dashboard_manual_entry`, `test_operating_cost_dashboard_wiring_v2`. Existing virtualenv, offline tools, isolated candidate project and temporary user directories were used. Earlier stable metadata/timeout/dashboard selection: 43 passed. An interim run during fixture migration had 127 passing and five fixture/assertion failures; those are now corrected and included in the final green selection. No tests were deleted to obtain green results.

## Numbered findings and disposition

1. **Important, resolved — first unprotected full fill had no recovery authority.** A fresh terminal full fill with missing protection could never obtain a bound position identity, because binding formerly depended on successful protection verification. Original offline reproduction left an exposed position with no protection, no close calls after three reconciliation rounds, and an unresolved receipt. Current `core_entry_orders.py:121` establishes original-order fill proof from durable preflight-flat evidence, exchange client/order identity, complete real fills, exact quantity/weighted average, position creation window and latest trade ID. `trader.py:5972` rechecks bound lifecycle and latest trade before one deterministic emergency close. Unknown proof remains frozen; arbitrary current exposure is never adopted. Positive, missing/foreign/later lifecycle, partial and mutation cases are verified in `tests/test_core_entry_pipeline.py:405`.

2. **Important, resolved — protected recovery could adopt a later lifecycle.** Protection shape and an old attached algo ID formerly let an unbound original receipt journal OPEN for a later same-side/size position even when strict fill proof rejected it. Original independent reproduction bound `new-lifecycle:2000000:short` with latest trade `foreign-fill` and journaled one OPEN against an old order. Current `trader.py:5831` applies proven lifecycle/latest-trade authority to successful finalization and pending adoption; old protection alone cannot bind a receipt. Re-running that reproduction now gives False, no bound identity, no journal and an unresolved receipt. Both nonterminal and terminal old-order cases are verified at `tests/test_core_entry_pipeline.py:452`.

3. **Important, resolved — terminal receipt could commit before its terminal event.** Process death between FILLED receipt update and durable FILLED event formerly prevented restart reconstruction because terminal receipts were excluded from pending recovery. Current `core_entry_events.py:50` commits terminal receipt and terminal event in one SQLite BEGIN IMMEDIATE transaction. Definite rejection evidence remains in a nonterminal receipt until its terminal event is committed. Re-running the process-death reproduction now leaves FILLED_UNJOURNALED, then restart creates exactly one FILLED event, one total OPEN journal row and terminal FILLED receipt. Crash recovery regression is verified at `tests/test_core_entry_pipeline.py:468`.

4. **Minor, resolved — entry history table column mismatch.** The six-header/five-cell mismatch was corrected to five headers, with the combined execution status/reason label matching the event renderer. Dynamic event cells use textContent.

5. **Minor, resolved — original plan preservation/display.** `_entry_plan_context` is now persisted in the receipt, including original quantity/contracts and SL/TP. The Telegram formatter displays the original plan when final quantity/prices differ, so restart recovery retains the original proposal and execution plan.

## Additional recovery review

Partial-fill recovery uses actual fill evidence rather than treating current position size as proof. Before emergency close, residual cancellation must be followed by an independently terminal original-order read. A raced additional fill may update the latest trade guard only when complete original-order fills prove the new quantity/average and the lifecycle is unchanged. A foreign mutation remains frozen. The paired original/foreign cancellation-race regression at `tests/test_core_entry_pipeline.py:488` passes; a confirmed close is not dispatched a second time after recovery.

Terminal event insertion and receipt transition share the same durable transaction. Event-key dedup remains decision ID plus phase; no automatic resend occurs for DELIVERY_UNKNOWN or process-death SENDING notifications. A notification failure does not convert a protected fill into a trade failure. Durable order reservations retain the original client ID and unresolved prior receipts block a new decision's entry.

## Test migration assessment

Seven baseline main test files were modified; no baseline main `.py` files were deleted. Inspected changes are legitimate contract migration: real CORE symbols/action/confidence, TraderState instead of incomplete stubs, exchange client/order IDs and lifecycle/fill evidence in fake clients, ORDER_EXECUTED to FILLED, local preflight failures remaining LOCAL_BLOCKED, and explicitly disabled GPT becoming fail closed. Existing quantity/risk/cap/chase/freshness and reversal-no-close assertions remain. Tests that deliberately raised at the mock submission boundary now assert uncertain ORDER_PENDING while retaining their quantity assertions. Additional proof fixtures use actual helper behavior rather than assuming protection shape authorizes a position lifecycle. Test counts alone are not treated as coverage evidence.

## Considered and declined findings

- **Timeout expansion/forged approval:** bypass requires a valid CORE Gemini LONG/SHORT candidate, entry_gate purpose, TIMEOUT raw result, timeout reason, exact APITimeoutError type, confirmed flag and explicit config opt-in. WAIT/REJECT, authentication/quota/rate limit, connection/server, empty response and parse errors remain blocking. TIMEOUT is retained separately from TIMEOUT_BYPASS; no approval is forged.
- **Timeout exit-price substitution:** timeout selects the original Gemini/local validated SL/TP. GPT revised exit plans are used only on the genuine approval path.
- **Risk or reversal weakening:** final quote/direction, daily loss, exchange floor/minimum, post-cost RR, approved budget/equity, freshness, chase and leverage target cap remain. Replacement validation occurs before existing-position close and again after close. Unknown lifecycle never authorizes close.
- **Telegram coupling/retry duplicates:** send requests run in a daemon outbox worker, with bounded timeout and one bounded definite-429 retry. Crash SENDING becomes DELIVERY_UNKNOWN; uncertain delivery is never blindly resent. Notification exceptions do not decide whether a protected fill succeeded.
- **Paid Shadow/timing change:** paid CORE GPT Shadow defaults OFF and requires explicit opt-in. Existing review interval, GPT entry timeout and retry ceiling are unchanged. No paid validation was used.
- **Cost claims:** retry ceiling is separate from actual retries; unknown measurements remain null. Repeated input hashes are not asserted to be waste or measured savings. Cost projections explicitly use logged estimates, not invoice amounts or verified per-model tariffs.
- **Dashboard authorization/injection:** endpoint authentication remains; no login bypass was introduced. Dynamic added event content uses textContent.
- **Unified coverage:** reviewed implementation is the legacy CORE owner path. No claim is made that the separately owned unified engine implements the timeout exception.

## Practical limits

Missing, contradictory, incomplete or truncated fill history intentionally freezes unresolved exposure for operator review. This is a conservative limit, not authority to close an unproven newer position. Actual account migration, engine/service state preservation, remote exchange protection, real Telegram confirmation and all complete release suites are the coordinator's separate final checks. This reviewer did not mutate the server or run paid calls.

## Final read-only API follow-up

The post-review change at `web_app.py:666` adds only `core_gpt_entry_timeout_bypass` and `core_paid_shadow_enabled` to the existing authenticated `/api/state` settings response, read from the current user's `ctx.cfg`. Both are already defined boolean UserConfig attributes. No setter, trading action, provider request, credential exposure or authorization change is introduced. All 16 other reviewed source hashes remain unchanged. The final `web_app.py` source compiles successfully; the existing event API addition is unchanged. **Verdict remains Ready.** Whole-suite and deployment outcomes reported by the coordinator are separate evidence, not this reviewer's independent remote verification.

## Reviewed source hashes

- `config.py`: `628ed019558317081ac09db837f4cf8186f51272dafed37e3e2a801f7f4c41a7`
- `core_entry_policy.py`: `ca0f8b95fa443249c900377a93d98341e11427fb7596b9dca83c9c2729d44ae3`
- `core_entry_events.py`: `cede96e0cb470743e5c19cff18d22f90cc60af388af97b31185c07e0d015f56a`
- `core_entry_orders.py`: `01766615569cd7617ea59a88114faac1f3150ddefdcc677496be23934453c1b7`
- `trader.py`: `0218e6f6138a070fa79c2aebd9b608730105e4b7f6bd9a2c9ce8fc3b4e557190`
- `okx_client.py`: `c0b97e29945a46af5a7f609cd53a0ad902b701bd0a58e72cdb8de9296bad08d0`
- `openai_analyzer.py`: `bde12af913a921552f1670af474cc4f51d90cdff07ca83f17e6754aac823f290`
- `telegram_notify.py`: `3dfe4c7429f350818f624c3e39ddeee8b7f730a7d1eb46918c96e032a6a1fafa`
- `gpt_shadow_log.py`: `7bc92ed8a4e77b0b14255cc4fa861200d63f68af65ba691f4c37938b5d5550c6`
- `web_app.py`: `7f07e8c72304971192c2efaf161e86ecfd145924cc7fbb021a02b7e304597025`
- `templates/dashboard.html`: `1addd301c0926af229e5c1bf81a1169ba8abe598a1333cb17fde6e0f50617ac1`
- `static/core_entry_status.js`: `30f6d9e774471f0aca17d80bfe32f6e71df0cc94f8d23c2d778d0e9231a3f44e`
- `usage_log.py`: `6e1cd89e30fe72ccbf500e44fb7a4b06e36220bd764b509c8cdc4e880f942218`
- `gpt_latency_log.py`: `b1024fc7bd2f1993f044dab8e63a8960903ef73d2f9e954cc784216def519be8`
- `operating_costs.py`: `224f5a29b53c2020a2d61762ff5808be4145a05a2589f4b774a9ac2ff000c253`
- `gemini_analyzer.py`: `0a2d47e6d1ad4f01bb34dc2d3bd24c459d7d8ebf5fa202da1691218d8b5f4969`
- `static/operating_observability.js`: `97e83f34b73a3c27def89a0cf02e1231a08fb0969f03a0b5742b24c97ea0a898`
