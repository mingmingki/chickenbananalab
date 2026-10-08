# Exit/Re-entry Loss Control Design

## Goal
Reduce churn and loss amplification after AI-driven exits without weakening existing hard risk controls, while making exit quality and lifecycle economics observable before any self-learning influence is allowed.

## Current evidence
- In the recent 72h window, 42 completed lifecycles had aggregate Net -305.08 USDT.
- 26 lifecycles ending with `position_ai_close_all` contributed -203.44 USDT, but this is association, not proof that closing caused the loss.
- 8 same-symbol/same-side reentries occurred within 60 minutes after AI CLOSE; 5 of the next lifecycles lost money and their combined Net was -30.31 USDT.
- Current `position_ai_close_all` protection is a fixed direction-agnostic `REENTRY_COOLDOWN_MINUTES` lock only; no thesis revalidation is required after expiry.
- Self-learning v1 acts only at new-entry time and cannot directly evaluate exit or re-entry policy.
- Strategy analysis already has completed-lifecycle accounting, but operational close-only views can still understate reduce losses.
- Restart produced three simultaneous OKX 50011/429 protection-query failures at 16:24:01, with no repeat after 16:24:02.

## Non-goals
- Do not weaken 0.75R hard close, 0.90R emergency close, SL/TP, daily-loss limits, kill switch, protection verification, or GPT Entry Gate.
- Do not make self-learning LIVE as part of this work.
- Do not infer that `position_ai_close_all` itself is harmful from observational P&L alone.
- Do not add new external API calls solely for shadow/counterfactual analytics.
- Do not change Candidate C order logic or its parity-pinned execution adapter in the breakout-failure work.
- Do not commit, push, or deploy unless separately requested.

## Subproject A — Durable same-side thesis re-entry gate
`position_ai_close_all` will create a durable re-entry thesis record rather than relying only on in-memory cooldown state.

For the first 15 minutes after AI CLOSE, retain the existing direction-agnostic block. For the closed side specifically, impose a minimum 30-minute block. After 30 minutes, same-side re-entry remains blocked until deterministic thesis recovery is demonstrated and the normal Gemini/GPT entry path approves the trade.

LONG thesis recovery requires a confirmed 1H structure of `close > EMA20 > EMA50` plus two consecutive confirmed 5m closes above EMA20. SHORT is the exact mirror: `close < EMA20 < EMA50` plus two consecutive confirmed 5m closes below EMA20. These conditions use data already fetched by the normal CORE cycle; they must not trigger extra exchange requests.

An `invalidated` exit never clears merely because time elapsed. It clears only on deterministic thesis recovery. `weakening`/other AI CLOSE reasons follow the same minimum 30-minute rule for v1; no separate looser path is introduced until forward evidence justifies one.

Persist records in a dedicated append/atomic state file so restart cannot erase the gate. State includes symbol, closed side, close time, close reason, Gemini assessment if known, `minimum_until`, status, recovery evidence, and clear time.

Opposite-side entries are not subject to the 30-minute same-side thesis gate; they remain subject to the existing 15-minute direction-agnostic AI-close lock and all normal reversal/entry safeguards.

## Subproject B — Exit and re-entry counterfactual shadow
Create an append-only shadow journal for every `position_ai_close_all` event.

Track the actual exit lifecycle Net and observe post-exit market outcomes at +15, +30, +60, and +120 minutes using already-fetched/cached candle data. Store the nearest confirmed observation at or after each horizon, never fabricate an exact fill.

Track whether price would have crossed the original SL/TP after the exit, maximum favorable/adverse excursion when the available candle range supports it, and whether a same-symbol/same-side re-entry occurred within 30/60/120 minutes. Link the next completed lifecycle and its full lifecycle Net when available.

The shadow output must distinguish: `exit_saved_loss`, `exit_missed_recovery`, `reentry_profitable`, `reentry_loss`, and `unresolved`. These labels are analytical only and cannot change live orders.

## Subproject C — Lifecycle P&L as strategy-analysis default
All strategy-quality and loss-cause reports must use completed lifecycle economic P&L: every REDUCE event plus final CLOSE plus recorded fees/funding/adjustment for that lifecycle.

Operational account/cashflow panels remain authoritative for account equity and capital-flow reporting. Do not replace account-bills/cashflow accounting with lifecycle accounting; instead label the two bases explicitly.

Close-only Net remains available as a diagnostic column, but it must not be the default number used for strategy comparisons, reason attribution, symbol attribution, or AI-exit analysis.

Add reconciliation fields: `lifecycle_net`, `final_close_net`, `reduce_net`, `difference_due_to_reduces`, and coverage/unmatched counts.

## Subproject D — Exit/re-entry self-learning shadow extension
Do not modify the current entry learner's LIVE decision interface. Add a separate exit/re-entry evidence stream first.

Features include exit reason, Gemini assessment (`invalidated`/`weakening`/other), correction-active state when available, side, symbol, regime, same-side re-entry within 30/60/120m, exit counterfactual outcome, next lifecycle Net, and churn-cycle Net.

A churn cycle is defined as an AI CLOSE followed by a same-symbol/same-side re-entry within the configured observation horizon; its Net includes the completed exit lifecycle and the subsequent completed lifecycle, not only their final close rows.

The first version is SHADOW ONLY: it may produce evidence/checkpoints and report pattern statistics, but it cannot promote an exit/re-entry pattern to `LIVE_BOUNDED`, cannot alter close actions, and cannot veto re-entry. Any later live influence requires a separate explicit design and deployment approval.

## Subproject E — Restart protection-query pacing
Prevent restart-time API bursts at the shared protection-query boundary rather than hiding 429s in the UI.

Serialize/pacing only the OKX pending-protection read path used by CORE and Candidate C during startup/recovery, with a process-wide lock and a small monotonic minimum spacing. Preserve existing retry/backoff and fail-safe behavior. Normal market-data/position calls are not globally throttled by this change.

The pacing implementation must prove that concurrent XRP/SOL/DOGE protection checks cannot issue simultaneous pending-algo HTTP calls, and that an individual 429 still returns UNKNOWN/fail-safe rather than being treated as verified.

## Subproject F — Candidate C breakout-failure shadow without parity impact
Do not modify parity-pinned Candidate C execution logic for this analysis feature.

Build breakout-failure evidence outside the execution adapter from existing epoch/setup records and existing monitor/candle logs. For an open Candidate C position, reconstruct the original setup breakout reference only when provenance is exact. If provenance is missing, mark the sample unresolved rather than guessing.

Record distance back below/above the original breakout level, elapsed time since entry, adverse excursion, derisk status, and final lifecycle outcome. This remains Shadow/report-only and cannot emit Reduce/Close/StopUpdate intents.

## Reporting
Add an `Exit/Re-entry` section to the analysis report showing: AI CLOSE lifecycle count/Net, close-only Net, reduce Net, same-side re-entry rates at 30/60/120m, subsequent lifecycle Net, resolved exit counterfactual counts, and churn-cycle Net.

Show re-entry thesis status per CORE symbol: minimum lock remaining, same-side thesis status, 1H recovery pass/fail, 5m consecutive recovery count, and last clear reason.

Candidate C breakout-failure shadow is displayed separately from live strategy state and labeled observational.

## Failure handling
All new analytics are fail-open with respect to existing trading behavior. Missing/invalid shadow data never blocks an otherwise valid trade.

The durable same-side thesis gate is different: once an AI CLOSE record is successfully created, ambiguity in its recovery evidence is fail-closed for same-side re-entry until evidence is deterministically available. A state-file write failure during CLOSE must not cancel or delay the CLOSE itself; it must fall back to the existing 15-minute lock and emit a high-severity diagnostic.

Startup pacing failures must preserve the current UNKNOWN/fail-safe protection semantics.

## Test strategy
TDD is required for every subproject. Key replay fixtures include the 2026-09-25 XRP sequence (10:32 close → 11:09 re-entry → 13:24 close → 14:10 re-entry → 14:26 close), BTC/ETH loss-defense exits, the PI SHORT recovery path, and the 16:24:01 three-symbol 429 burst.

Subproject A tests restart persistence, 15m all-direction block, 30m same-side minimum, 1H structure requirement, two confirmed 5m bars, LONG/SHORT symmetry, and no extra API calls.

Subproject B tests horizon resolution without lookahead, unresolved samples, lifecycle—not close-only—P&L linkage, and no order mutation.

Subproject C tests lifecycle reconciliation on positions with zero/one/two reduces and keeps account cashflow accounting separate.

Subproject D tests shadow-only authority, churn-cycle linking, duplicate suppression, and zero impact on entry/exit execution.

Subproject E tests concurrent protection reads serialize, 429 remains fail-safe, and ordinary steady-state calls are not delayed unnecessarily.

Subproject F tests exact setup provenance, unresolved fallback, and zero Candidate C order mutations.

## Rollout sequence
1. A — same-side thesis re-entry gate.
2. B + C — exit counterfactual and lifecycle reporting.
3. D — exit/re-entry learning shadow extension.
4. E — startup protection-query pacing.
5. F — Candidate C breakout-failure shadow.

Each subproject is independently testable and deployable. No later phase is required for an earlier phase to remain safe. Production deployment is a separate explicit action after validation.
