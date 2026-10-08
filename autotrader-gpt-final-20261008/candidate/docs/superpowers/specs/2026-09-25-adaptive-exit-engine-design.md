# Adaptive Exit Engine Design

## Status
Design approved in chat on 2026-09-25. Written-spec review is required before implementation.

## Goal
Replace fixed or quasi-fixed SL/TP rules with a deterministic adaptive exit layer shared by CORE and Candidate C. The engine combines current market structure, volatility, account risk, observed trade outcomes, and bounded Gemini assessments while preserving hard risk controls and replayability.

Success means:
- one wide-stop trade cannot consume an outsized share of account risk;
- profitable trends can extend without forcing a fixed TP too early;
- weak trades tighten or de-risk sooner without churn-heavy over-management;
- Gemini and self-learning can influence exit policy without direct price/order authority;
- every live decision can be replayed from stored inputs.

## Current state
- CORE uses fixed percentage defaults (`STOP_LOSS_PCT`, `TAKE_PROFIT_PCT`) plus separate loss-defense and Gemini+GPT position-management logic.
- Candidate C is volatility-aware but still uses fixed policy multipliers: initial stop 4H ATR x 3.5, trailing 4H ATR x 6.0, +1R profit lock, +2R 25% partial take profit.
- Candidate C already rejects stop updates that would loosen protection.
- Existing self-learning primarily affects entry allow/hold and confidence; it does not select SL/TP geometry.
- Gemini held-position review supplies thesis weakening/invalidation evidence but does not own SL/TP prices.
- Lifecycle reconstruction already joins REDUCE + final CLOSE and can be extended with MAE/MFE and exit-policy evidence.

## Non-goals
- Gemini must never return executable SL/TP prices.
- Self-learning must not directly mutate live risk limits or promote itself to live authority.
- Do not weaken kill switch, daily-loss limits, exchange-native protection verification, max leverage, max order notional, or emergency close logic.
- Active-position stops must never move away from the protected side after entry.
- No look-ahead data may enter live or replay calculations.

## Architecture
Introduce a pure `AdaptiveExitEngine` that consumes an immutable `AdaptiveExitContext` and returns an `AdaptiveExitPlan`. It performs no exchange calls and writes no files. CORE and Candidate C adapters translate their current snapshots into the common context, then execution layers decide whether to place/update orders.

The engine has five deterministic stages:
1. build market-structure and volatility features;
2. determine structural invalidation distance;
3. apply bounded AI and learning modifiers;
4. derive risk-capped position sizing and target ladder;
5. apply monotonic protection and execution eligibility checks.

The engine must support `SHADOW`, `ADVISORY`, and `LIVE_BOUNDED` modes. Initial rollout is SHADOW only.

## Input contract
`AdaptiveExitContext` contains only point-in-time data available at the decision timestamp:
- symbol, side, entry price, current quantity, effective entry costs;
- account equity, realized daily PnL, open risk, group risk, leverage and order caps;
- confirmed 5m/1h/4h candles and indicators;
- ATR and realized-volatility percentile;
- recent confirmed swing high/low and structural support/resistance;
- trend alignment and regime state;
- current stop, initial stop, high/low water mark, realized partial PnL;
- Gemini structured assessment;
- validated learning overlay, if any;
- lifecycle policy version and calculation timestamp.

Missing optional AI/learning inputs must not invalidate deterministic market/risk calculation. Missing essential market/risk inputs must fail closed for new entries and must retain the existing tighter stop for held positions.

## Gemini contract
Gemini returns structured state only:
- `thesis_state`: `intact | weakening | invalidated`;
- `confidence`: 0..1;
- `trend_persistence`: `low | medium | high`;
- `volatility_risk`: `low | medium | high`;
- `target_extension`: `deny | neutral | allow`;
- short textual rationale for audit.

Gemini must not return raw stop prices, target prices, quantities, leverage, or loss budgets. Invalid/missing Gemini output is ignored and the deterministic base plan remains authoritative.

## Initial stop calculation
The stop represents thesis invalidation, not a desired fixed percentage loss.

For LONG:
- structural candidate = confirmed support/swing-low minus a volatility noise buffer;
- ATR candidate distance = bounded adaptive ATR multiplier x selected ATR;
- raw stop distance must be at least the deterministic noise floor needed to avoid normal volatility;
- if the structural invalidation point is farther away, preserve that geometry and reduce position size instead of pulling the stop closer merely to fit a desired size.

SHORT is the exact mirror.

The existing Candidate C initial ATR multiplier 3.5 is the prior, not a permanent constant. In SHADOW, the adaptive multiplier is recorded alongside the legacy 3.5 result. No live widening is allowed until replay evidence is validated.

## Dollar-risk invariant
Stop geometry and position size are separate decisions.

`planned_loss_usdt = effective_notional x stop_distance_fraction + estimated_exit_costs`

For every new order:
- `planned_loss_usdt <= trade_risk_budget_usdt`;
- `trade_risk_budget_usdt` is capped by account, group, daily-loss and absolute-risk limits;
- leverage changes notional, never the allowed dollar loss;
- exchange minimum size that cannot satisfy the risk budget causes the entry to be rejected.

For `FIXED_MARGIN`, the configured margin is a target/max exposure, not permission to exceed the risk cap. Effective margin becomes the lower of the fixed-margin target and the margin allowed by structural-stop risk. The dashboard must show both configured and effective margin.

For `VARIABLE_RISK`, the structural stop is calculated first and size is solved from the permitted dollar risk, then bounded by leverage/order caps.

## Active-stop invariant
After a position is live, the exchange stop may only remain unchanged or tighten.

For LONG: `new_stop >= current_stop`.
For SHORT: `new_stop <= current_stop`.

No Gemini assessment, confidence increase, learning pattern, config reload, restart, or volatility expansion may loosen an installed stop. A looser proposed stop is logged as rejected and the current exchange protection remains authoritative.

## Adaptive target ladder
Replace a single fixed TP with a deterministic target ladder:
- `TP1`: near-term realization target based on current R, nearby structure and expected MFE;
- `TP2`: continuation target based on stronger structure/trend persistence;
- `runner`: residual quantity managed by profit-lock/trailing stop rather than a fixed final TP.

Target prices and fractions are computed deterministically. Gemini may only select a bounded modifier such as allowing/denying target extension; it cannot name prices.

If projected post-cost reward/risk is below the configured minimum, a new entry is rejected rather than forcing an unattractive TP.

## Profit lock and trailing
Profit lock remains cost-aware: once activated, the stop must protect at least break-even after estimated entry/exit fees, spread and slippage.

Activation threshold, partial-realization threshold, trailing ATR multiplier and target fractions become adaptive parameters with hard bounds. Until sufficient evidence exists, current Candidate C values (+1R lock, +2R 25% partial, 6ATR trail) remain priors in SHADOW comparison.

A tightening proposal may be triggered by:
- favorable R progress;
- structural support/resistance migration;
- volatility contraction;
- Gemini `weakening`/`invalidated` state;
- validated adverse MFE/MAE pattern evidence.

A tightening proposal must still pass monotonic-stop and minimum-market-noise checks to avoid micro-churn.

## CORE integration
CORE no longer treats 2%/4% as the final live exit geometry once Adaptive Exit reaches LIVE_BOUNDED. During SHADOW they remain the executed legacy values while adaptive alternatives are logged.

Existing 0.50R/0.75R/0.90R loss-defense and Gemini+GPT position-management gates remain independent hard safety layers. Adaptive Exit may tighten before them but may not suppress an emergency action that they require.

To address fee drag, adaptive partial reductions must include a minimum expected economic benefit after fees and a per-epoch action cooldown/duplicate-signal guard. Repeated 25% reductions without new evidence are not permitted.

## Candidate C integration
Candidate C keeps its current decision engine and exchange-protection adapter. Adaptive Exit replaces fixed policy numbers only after SHADOW validation.

The current 4H direction invalidation, protection verification, durable epoch state, risk reservation and stop-loosening rejection remain authoritative. The adaptive plan is versioned into the position epoch at entry so restarts continue managing the position with the same policy snapshot unless a later tightening-only migration is explicitly allowed.

## Self-learning extension
Extend lifecycle evidence with:
- initial and effective stop distance;
- ATR multiple and volatility percentile at entry;
- TP1/TP2/runner configuration;
- MAE, MFE and MFE giveback;
- time-to-MAE/time-to-MFE;
- realized R and lifecycle Net after fees/funding;
- partial-reduction count and fee cost;
- Gemini states/confidence observed through the lifecycle;
- whether stop/target changes would have improved or worsened the observed path in counterfactual shadow replay.

Learning produces bounded parameter suggestions, not orders. Suggestions are shrunk toward the global strategy prior and require minimum sample, out-of-sample/walk-forward validation, and stability checks across adjacent windows.

Learning authority remains staged:
`SHADOW_LEARNING -> VALIDATED -> LIVE_BOUNDED`.
No automatic promotion to `LIVE_BOUNDED` is allowed in this design. Promotion requires explicit operator approval and a separately recorded policy version.

## Bounded modifier policy
Market structure determines the base geometry. Gemini and learning are overlays with strict authority limits.

They may:
- tighten a stop;
- reduce size/risk;
- reduce or accelerate partial realization;
- deny target extension;
- within validated bounds, widen the *initial* structural stop only if position size is reduced so dollar risk does not increase.

They may not:
- increase maximum dollar risk;
- loosen an active stop;
- raise leverage or order cap;
- override kill/daily-loss/protection gates;
- fabricate missing market structure;
- directly submit/cancel orders.

Confidence must never be double-counted. If confidence widens an initial stop, the resulting size is reduced to keep the same risk budget; confidence must not simultaneously increase both stop distance and dollar risk.

## Failure handling
- Gemini timeout/error: deterministic base plan only.
- Learning data error or insufficient sample: strategy prior only.
- Missing confirmed candles/ATR/structure for new entry: no new adaptive entry.
- Missing data for an existing position: retain the current tighter exchange stop and continue hard-risk monitoring.
- Protection-update failure: retain/verify existing stop; never assume the proposed stop is active.
- Restart: reload immutable policy snapshot and current exchange protection before calculating any new tightening.

## Observability
Every adaptive calculation writes an audit record containing:
- base market features and their source candle timestamps;
- base structural stop and ATR stop;
- Gemini modifier and confidence;
- learning modifier and evidence IDs;
- final stop/targets;
- configured/effective margin, notional and planned dollar risk;
- estimated fees and post-cost reward/risk;
- rejected alternatives and reason codes;
- policy version/hash.

Dashboard shows configured versus effective sizing, adaptive stop source, actual planned loss USDT, TP ladder, runner fraction, Gemini state, learning state, and why the latest stop/target was changed or rejected.

## Replay and validation
Before live influence, run historical/replay comparison between legacy and adaptive plans using only data available at each historical decision timestamp.

Mandatory fixtures include:
- the 2026-09-23 DOGE Candidate C lifecycle with approximately 2,500 USDT notional and the outsized loss;
- 2026-09-25 DOGE/SOL Candidate C lifecycles including their partial reductions;
- representative CORE lifecycle sequences with repeated REDUCE actions and high fee drag;
- restart/reconciliation cases with existing exchange-native stops.

Primary evaluation metrics:
- lifecycle Net and Profit Factor after fees;
- max lifecycle loss and tail-loss quantiles;
- MAE/MFE capture efficiency;
- realized profit giveback;
- number of reductions/close actions per lifecycle;
- fee as percentage of gross edge;
- stop-out frequency and post-stop favorable excursion;
- risk-budget violations (must be zero).

The system must not select a live policy solely because backtest Net is higher. Tail loss, fee drag, stability across windows and risk invariants are co-equal acceptance criteria.

## Test strategy
TDD is mandatory.

Unit tests:
- LONG/SHORT structural-stop symmetry;
- no-lookahead confirmed-candle contract;
- position sizing solves to the dollar-risk cap;
- FIXED_MARGIN shrinks when a wide stop would exceed risk;
- exchange min size rejects unsafe entries;
- active stop can never loosen;
- Gemini cannot provide executable prices or increase risk;
- learning cannot act without a validated bounded policy;
- profit lock is fee/cost aware;
- target ladder obeys minimum post-cost reward/risk;
- repeated reduction without new evidence is suppressed.

Integration/replay tests:
- CORE legacy execution unchanged in SHADOW mode;
- Candidate C legacy execution unchanged in SHADOW mode;
- existing protected positions survive restart unchanged;
- shadow records reproduce deterministically;
- a failed AI call has zero order-side effect;
- every order-affecting adaptive plan has an audit record and policy hash.

## Rollout
1. Build common pure engine and audit schema in SHADOW mode only.
2. Add lifecycle MAE/MFE/counterfactual evidence and adaptive learning shadow.
3. Replay recent real lifecycles and historical folds; publish legacy-vs-adaptive comparison.
4. Enable dashboard ADVISORY display while orders still use legacy logic.
5. If validated, enable Candidate C `LIVE_BOUNDED` first because it already has ATR/epoch/protection primitives.
6. Observe forward results and verify zero risk-invariant violations.
7. Enable CORE `LIVE_BOUNDED` only after separate validation of fee/churn effects.

Each live activation is independently reversible to legacy policy. Existing exchange-native protection is never removed during mode transitions.

## Implementation boundaries
Expected new modules:
- `adaptive_exit_engine.py` — pure calculation and plan types;
- `adaptive_exit_policy.py` — versioned hard bounds/prior configuration;
- `adaptive_exit_log.py` — append-only calculation audit;
- `adaptive_exit_learning.py` — shadow evidence/suggestion adapter.

Expected integration points:
- `trader.py` for CORE context/advisory/live-bounded calls;
- `candidate_c_decision_engine.py` / `candidate_c_exit_management.py` for Candidate C context and plan translation;
- `trade_learning_features.py` / lifecycle analytics for MAE/MFE evidence;
- `templates/dashboard.html` and `/api/state` for observability.

Existing exchange adapters should not be rewritten. They continue to enforce order/protection semantics after receiving validated plans.

## Acceptance gate before any live deployment
- new adaptive unit/integration tests green;
- existing Candidate C tests show no regression in SHADOW mode;
- existing CORE regression baseline shows no new failures;
- replay demonstrates zero risk-cap violations and zero active-stop loosening;
- legacy/adaptive report explicitly separates gross edge, fees, lifecycle Net and tail loss;
- Gemini/learning outage tests prove deterministic safe fallback;
- operator explicitly approves the specific policy version for LIVE_BOUNDED activation.
