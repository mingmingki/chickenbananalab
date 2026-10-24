# Adaptive Exit Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a deterministic adaptive SL/TP and risk-capped exit engine shared by CORE and Candidate C, using market structure, volatility, bounded Gemini assessments, and shadow-validated self-learning without allowing AI to directly set executable prices.

**Architecture:** Add a pure `AdaptiveExitEngine` with versioned hard policy bounds and immutable point-in-time inputs. First wire it in SHADOW/ADVISORY only, enrich lifecycle analytics with MAE/MFE evidence, replay legacy versus adaptive behavior, then add disabled-by-default `LIVE_BOUNDED` adapters for Candidate C and CORE. Existing exchange/order/protection adapters remain authoritative.

**Tech Stack:** Python 3.12, pytest, dataclasses, JSONL audit logs, existing OKX execution adapters, existing Gemini/GPT decision pipeline, existing lifecycle/self-learning modules.

**Spec:** `docs/superpowers/specs/2026-09-25-adaptive-exit-engine-design.md`

## Global Constraints
- Production source of truth for implementation baseline: `/opt/autotrader-releases/candidate_c_sizing_selector_20260925T2305KST`.
- Build and test in an isolated copy/worktree; do not modify `/opt/autotrader` in place.
- No active-position stop may move away from the protected side after entry.
- Gemini must never return or directly control executable SL/TP prices.
- Self-learning cannot promote itself to live authority or mutate hard risk limits.
- Missing AI/learning data must fall back deterministically to the non-AI structural plan.
- Kill switch, daily-loss limits, leverage cap, order-notional cap, and exchange-native protection verification remain authoritative.
- No look-ahead data may enter live or replay calculations.
- Initial rollout is SHADOW only; production activation requires a separate operator approval.

## Review Focus
- Missing/stale ATR or structure inputs must produce an explicit safe fallback, never a fabricated price.
- Fixed-margin positions with wide structural stops must shrink quantity to obey dollar-risk cap.
- Repeated 5-minute reviews must never loosen an already-installed stop.
- Gemini timeout/invalid JSON/confidence=None must have zero direct order-side effect.
- Restart/replay must reproduce the same adaptive plan hash from the same point-in-time inputs.

---

### Task 1: Versioned policy and immutable plan contracts

**Files:**
- Create: `adaptive_exit_policy.py`
- Create: `adaptive_exit_engine.py`
- Test: `tests/test_adaptive_exit_policy.py`
- Test: `tests/test_adaptive_exit_engine_contract.py`

**Interfaces:**
- Produces: `AdaptiveExitContext`, `GeminiExitAssessment`, `LearningExitSuggestion`, `AdaptiveExitPlan` dataclasses.
- Produces: `production_adaptive_exit_policy() -> dict` and `validate_adaptive_exit_policy(value: object) -> dict`.
- Produces: `AdaptiveExitEngine(policy: dict).plan(ctx: AdaptiveExitContext) -> AdaptiveExitPlan`.

- [ ] **Step 1: Write failing policy schema tests**

```python
def test_policy_rejects_unknown_fields_and_out_of_bounds_modifiers():
    policy = production_adaptive_exit_policy()
    policy["unknown"] = 1
    with pytest.raises(AdaptiveExitPolicyError):
        validate_adaptive_exit_policy(policy)
```

- [ ] **Step 2: Write failing immutable-contract tests**
```python
def test_context_and_plan_are_frozen_and_hashable():
    ctx = AdaptiveExitContext(symbol="BTC/USDT:USDT", side="long", entry_price=100.0,
                              current_quantity=1.0, current_stop=None, equity_usdt=1000.0,
                              trade_risk_budget_usdt=10.0, atr=2.0)
    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.entry_price = 101.0
```

- [ ] **Step 3: Run contract tests and verify RED**

Run: `pytest -q tests/test_adaptive_exit_policy.py tests/test_adaptive_exit_engine_contract.py`
Expected: import/definition failures for the new modules.

- [ ] **Step 4: Implement the minimal versioned policy and frozen dataclasses**

Define hard bounds for initial ATR multiplier, trailing ATR multiplier, profit-lock R, TP1/TP2 R, target fractions, minimum post-cost reward/risk, maximum Gemini modifier, maximum learning modifier, and per-epoch reduction cooldown. Validate exact keys and finite numeric ranges.

- [ ] **Step 5: Add deterministic policy hash and plan hash**

Use sorted compact JSON serialization and SHA-256. The same context + policy must reproduce the same `plan_hash`.

- [ ] **Step 6: Run tests and commit**

Run: `pytest -q tests/test_adaptive_exit_policy.py tests/test_adaptive_exit_engine_contract.py`
Expected: PASS.

```bash
git add adaptive_exit_policy.py adaptive_exit_engine.py tests/test_adaptive_exit_policy.py tests/test_adaptive_exit_engine_contract.py
git commit -m "feat: add adaptive exit contracts and policy"
```

### Task 2: Structural stop geometry and dollar-risk-capped sizing

**Files:**
- Modify: `adaptive_exit_engine.py`
- Modify: `risk_manager.py:14-49,95-100`
- Modify: `execution_units.py:179-235`
- Test: `tests/test_adaptive_exit_risk.py`

**Interfaces:**
- Produces: `compute_structural_stop(ctx, policy) -> StopGeometry`.
- Produces: `solve_risk_capped_size(ctx, stop_price, exposure_cap_notional) -> RiskSizing`.
- Existing CORE/Candidate C sizing functions remain unchanged unless adaptive mode is explicitly requested.

- [ ] **Step 1: Write failing LONG/SHORT symmetry and wide-stop sizing tests**

```python
def test_fixed_margin_wide_stop_shrinks_notional_to_risk_budget():
    ctx = fixture_ctx(side="long", entry=100.0, structural_support=90.0,
                      atr=2.0, equity=3000.0, risk_budget=30.0,
                      configured_margin=500.0, leverage=5.0)
    plan = engine.plan(ctx)
    assert plan.configured_notional == 2500.0
    assert plan.planned_loss_usdt <= 30.0
    assert plan.effective_notional < 2500.0
```

- [ ] **Step 2: Write failing minimum-size rejection and cost-budget tests**

Pin `planned_loss_usdt = notional * stop_distance_fraction + estimated_exit_costs` and reject entries where exchange minimum quantity would exceed the risk budget.

- [ ] **Step 3: Run risk tests and verify RED**
Run: `pytest -q tests/test_adaptive_exit_risk.py`
Expected: failures because structural geometry/risk sizing are absent.
- [ ] **Step 4: Implement structural stop selection**

For LONG, derive the invalidation candidate from confirmed support/swing-low minus bounded volatility noise; for SHORT mirror the calculation. Compare against the bounded ATR-distance prior and preserve farther structural invalidation when justified. Missing essential structure/ATR returns `entry_allowed=False` rather than inventing a stop.

- [ ] **Step 5: Implement risk-capped sizing without changing legacy defaults**

Add opt-in helpers only. `FIXED_MARGIN` uses `min(configured_notional, risk_allowed_notional, order_cap)`; `VARIABLE_RISK` solves notional from the dollar-risk budget then applies leverage/order caps. Do not change `calculate_position_size()` or `sl_tp_prices()` behavior for legacy mode.

- [ ] **Step 6: Run tests and commit**

Run: `pytest -q tests/test_adaptive_exit_risk.py tests/test_adaptive_exit_policy.py tests/test_adaptive_exit_engine_contract.py`
Expected: PASS.

```bash
git add adaptive_exit_engine.py risk_manager.py execution_units.py tests/test_adaptive_exit_risk.py
git commit -m "feat: add structural stop and risk capped sizing"
```

### Task 3: Adaptive target ladder, profit lock, trailing, and monotonic stop

**Files:**
- Modify: `adaptive_exit_engine.py`
- Reuse: `candidate_c_exit_management.py:84-215`
- Test: `tests/test_adaptive_exit_targets.py`
- Test: `tests/test_adaptive_exit_monotonic.py`

**Interfaces:**
- Produces: `TargetLeg(price, fraction, reason)` and `AdaptiveExitPlan.tp1/tp2/runner_fraction`.
- Produces: `apply_monotonic_stop(side, current_stop, proposed_stop) -> StopDecision`.

- [ ] **Step 1: Write failing target-ladder tests**

```python
def test_low_post_cost_rr_rejects_new_entry_instead_of_forcing_tp():
    ctx = fixture_ctx(near_resistance=101.0, entry=100.0, structural_stop=97.0,
                      estimated_roundtrip_cost=0.4)
    plan = engine.plan(ctx)
    assert plan.entry_allowed is False
    assert plan.reason_code == "post_cost_rr_below_minimum"
```
- [ ] **Step 2: Write failing monotonic-stop tests**

```python
@pytest.mark.parametrize("side,current,proposed,accepted", [
    ("long", 95.0, 94.0, 95.0), ("long", 95.0, 96.0, 96.0),
    ("short", 105.0, 106.0, 105.0), ("short", 105.0, 104.0, 104.0),
])
def test_active_stop_never_loosens(side, current, proposed, accepted):
    assert apply_monotonic_stop(side, current, proposed).effective_stop == accepted
```

- [ ] **Step 3: Write failing cost-aware profit-lock and duplicate-reduction tests**

Require break-even-after-cost stop, minimum market-noise spacing, and a per-epoch evidence/cooldown key so the same weakening signal cannot trigger repeated 25% reductions.

- [ ] **Step 4: Run tests and verify RED**
Run: `pytest -q tests/test_adaptive_exit_targets.py tests/test_adaptive_exit_monotonic.py`
Expected: failures before target/monotonic implementation.

- [ ] **Step 5: Implement TP1/TP2/runner and tightening-only management**

TP1 uses nearer structure/current R/MFE prior; TP2 uses continuation structure/trend persistence; runner has no fixed final target. Adaptive profit-lock/trailing thresholds stay inside policy bounds and reuse Candidate C cost-aware break-even math where applicable.

- [ ] **Step 6: Run tests and commit**

```bash
pytest -q tests/test_adaptive_exit_targets.py tests/test_adaptive_exit_monotonic.py
git add adaptive_exit_engine.py tests/test_adaptive_exit_targets.py tests/test_adaptive_exit_monotonic.py
git commit -m "feat: add adaptive targets and monotonic protection"
```

### Task 4: Bounded Gemini overlay with no price authority

**Files:**
- Modify: `gemini_analyzer.py:229-315`
- Modify: `adaptive_exit_engine.py`
- Test: `tests/test_adaptive_exit_gemini.py`

**Interfaces:**
- Gemini output: `thesis_state`, `confidence`, `trend_persistence`, `volatility_risk`, `target_extension`, `reasoning` only.
- Produces: `apply_gemini_overlay(base_plan, assessment, policy) -> AdaptiveExitPlan`.
- [ ] **Step 1: Write failing parser/authority tests**

```python
def test_gemini_cannot_return_prices_or_raise_risk():
    raw = {"thesis_state":"weakening", "confidence":0.9,
           "trend_persistence":"low", "volatility_risk":"high",
           "target_extension":"deny", "stop_price":88.0}
    parsed = parse_adaptive_exit_assessment(raw)
    assert not hasattr(parsed, "stop_price")
    adjusted = apply_gemini_overlay(base_plan(), parsed, policy())
    assert adjusted.trade_risk_budget_usdt <= base_plan().trade_risk_budget_usdt
```

- [ ] **Step 2: Add timeout/invalid JSON/None-confidence tests**

All malformed or unavailable Gemini responses must map to `assessment=None`; the deterministic base plan must remain byte-for-byte equivalent except for audit metadata recording the AI failure.

- [ ] **Step 3: Run tests and verify RED**
Run: `pytest -q tests/test_adaptive_exit_gemini.py`
Expected: parser/overlay definitions absent.

- [ ] **Step 4: Extend held-position Gemini schema and implement bounded overlay**

Keep current `thesis_intact|weakening|invalidated` semantics backward-compatible; add optional structured fields. Overlay may tighten/reduce/deny extension only within hard policy bounds. It may widen an initial stop only before entry and only when resulting size preserves the same dollar-risk budget.

- [ ] **Step 5: Run tests and commit**

```bash
pytest -q tests/test_adaptive_exit_gemini.py tests/test_adaptive_exit_risk.py tests/test_adaptive_exit_monotonic.py
git add gemini_analyzer.py adaptive_exit_engine.py tests/test_adaptive_exit_gemini.py
git commit -m "feat: add bounded gemini exit overlay"
```

### Task 5: Append-only audit log and deterministic replay contract

**Files:**
- Create: `adaptive_exit_log.py`
- Modify: `adaptive_exit_engine.py`
- Test: `tests/test_adaptive_exit_log.py`

**Interfaces:**
- Produces: `append_plan(user_dir, record: dict) -> None`.
- Produces: `load_recent(user_dir, limit: int) -> list[dict]`.
- Audit key: `(symbol, decision_timestamp, policy_hash, input_snapshot_hash, mode)`.
- [ ] **Step 1: Write failing append/replay tests**

```python
def test_same_snapshot_replays_same_plan_hash(tmp_path):
    first = engine.plan(ctx)
    append_plan(tmp_path, first.audit_record())
    replayed = engine.plan(ctx)
    assert replayed.plan_hash == first.plan_hash
```

- [ ] **Step 2: Test duplicate suppression and corrupt-tail containment**

Duplicate audit keys do not append twice. A corrupt final JSONL line is reported in diagnostics and must not create execution authority or alter previous valid records.

- [ ] **Step 3: Run tests and verify RED**
Run: `pytest -q tests/test_adaptive_exit_log.py`
Expected: missing module/functions.

- [ ] **Step 4: Implement append-only audit records**

Each record must include source candle timestamps, structural/ATR candidates, rejected alternatives, Gemini/learning modifiers, configured/effective margin, notional, planned loss, estimated fees, post-cost R/R, final target ladder, policy hash, input hash, and mode.

- [ ] **Step 5: Run tests and commit**

```bash
pytest -q tests/test_adaptive_exit_log.py tests/test_adaptive_exit_engine_contract.py
git add adaptive_exit_log.py adaptive_exit_engine.py tests/test_adaptive_exit_log.py
git commit -m "feat: audit adaptive exit plans"
```

### Task 6: Lifecycle MAE/MFE evidence and learning shadow

**Files:**
- Modify: `trade_learning_lifecycle.py`
- Modify: `trade_learning_features.py`
- Create: `adaptive_exit_learning.py`
- Test: `tests/test_adaptive_exit_lifecycle_features.py`
- Test: `tests/test_adaptive_exit_learning.py`

**Interfaces:**
- Extends featured lifecycle with `mae_r`, `mfe_r`, `mfe_giveback_pct`, `time_to_mae`, `time_to_mfe`, `initial_stop_distance`, `effective_stop_distance`, `partial_reduction_count`, `fee_cost`, and observed Gemini states.
- Produces: `suggest_overlay(featured_lifecycles, prior_policy) -> LearningExitSuggestion` with `authority="SHADOW"|"VALIDATED"|"LIVE_BOUNDED"`.
- [ ] **Step 1: Write failing no-lookahead MAE/MFE tests**

Use candle fixtures whose post-exit future movement is deliberately extreme; assertions must prove lifecycle MAE/MFE uses only candles between entry and actual exit.

- [ ] **Step 2: Write failing learning-authority tests**

```python
def test_shadow_learning_cannot_change_live_plan():
    suggestion = LearningExitSuggestion(authority="SHADOW", initial_atr_delta=-0.4)
    plan = engine.plan(fixture_ctx(learning=suggestion))
    assert plan.live_parameters == plan.base_parameters
    assert plan.shadow_parameters != plan.base_parameters
```

- [ ] **Step 3: Add minimum-sample, shrinkage, adjacent-window stability tests**

A suggestion is not `VALIDATED` without configured sample count, out-of-sample improvement, and stability across adjacent windows. No code path may auto-promote `VALIDATED` to `LIVE_BOUNDED`.

- [ ] **Step 4: Run tests and verify RED**
Run: `pytest -q tests/test_adaptive_exit_lifecycle_features.py tests/test_adaptive_exit_learning.py`
Expected: new evidence/learning interface absent.

- [ ] **Step 5: Implement feature enrichment and bounded suggestions**

Persist evidence IDs and the prior/shrunk suggestion. Missing data remains `unknown`/`None`; never infer MAE/MFE from text logs or fabricate absent candle ranges.

- [ ] **Step 6: Run tests and commit**

```bash
pytest -q tests/test_adaptive_exit_lifecycle_features.py tests/test_adaptive_exit_learning.py
git add trade_learning_lifecycle.py trade_learning_features.py adaptive_exit_learning.py tests/test_adaptive_exit_lifecycle_features.py tests/test_adaptive_exit_learning.py
git commit -m "feat: learn adaptive exit evidence in shadow"
```

### Task 7: Candidate C SHADOW integration without execution changes

**Files:**
- Modify: `candidate_c_decision_engine.py:263-404`
- Modify: `candidate_c_exit_management.py:84-215`
- Modify: `candidate_c_strategy_policy.py`
- Test: `tests/test_candidate_c_adaptive_exit_shadow.py`

**Interfaces:**
- Candidate C legacy `Intent` remains authoritative in SHADOW.
- Adaptive plan is calculated from the same as-of snapshot and appended to audit log only.
- [ ] **Step 1: Write failing zero-impact shadow tests**

Replay the 2026-09-23 DOGE lifecycle and assert the legacy Candidate C intent stream is identical with adaptive mode OFF versus SHADOW. Also assert a shadow record contains configured/effective notional and planned loss.

- [ ] **Step 2: Add restart policy-snapshot tests**

An existing position epoch keeps the policy hash captured at entry. Restart must not replace it with a newer policy or loosen the current exchange stop.

- [ ] **Step 3: Run Candidate C shadow tests and verify RED**
Run: `pytest -q tests/test_candidate_c_adaptive_exit_shadow.py`
Expected: adaptive shadow fields/integration absent.

- [ ] **Step 4: Wire same-snapshot adaptive calculation after legacy decision**

Do not alter priority order, `4h_full_exit`, `1h_structural_derisk`, current +2R partial, profit lock, trailing, risk reservation, or stop-loosening rejection. Record what adaptive policy would have done at the same timestamp.

- [ ] **Step 5: Run Candidate C regression suite**
Run: `pytest -q tests/test_candidate_c_*.py`
Expected: all existing Candidate C tests remain green plus new shadow tests.

- [ ] **Step 6: Commit**

```bash
git add candidate_c_decision_engine.py candidate_c_exit_management.py candidate_c_strategy_policy.py tests/test_candidate_c_adaptive_exit_shadow.py
git commit -m "feat: shadow adaptive exits for candidate c"
```

### Task 8: CORE SHADOW integration without execution changes

**Files:**
- Modify: `trader.py:3375-3495,4290-4325,4861-5045`
- Modify: `config.py:236-260`
- Test: `tests/test_core_adaptive_exit_shadow.py`

**Interfaces:**
- Legacy CORE continues using `risk_manager.calculate_position_size()` and `risk_manager.sl_tp_prices()` in SHADOW.
- Adaptive plan consumes the same confirmed market snapshot plus Gemini/learning state and is audit-only.

- [ ] **Step 1: Write failing legacy-parity shadow test**

```python
def test_core_shadow_does_not_change_order_arguments(monkeypatch):
    legacy = capture_entry_args(adaptive_mode="OFF")
    shadow = capture_entry_args(adaptive_mode="SHADOW")
    assert shadow == legacy
```
- [ ] **Step 2: Write failing held-position tightening shadow test**

For a position with current stop, assert adaptive SHADOW can propose a tighter stop but the production `protection_target` and exchange calls remain unchanged. A looser proposal must be logged as `rejected_stop_loosen`.

- [ ] **Step 3: Write failing Gemini outage parity test**

Force `analyze_held_position()` timeout/exception and assert legacy execution path and hard 0.50R/0.75R/0.90R loss-defense behavior are unchanged.

- [ ] **Step 4: Run tests and verify RED**
Run: `pytest -q tests/test_core_adaptive_exit_shadow.py`
Expected: adaptive mode/config/audit integration absent.

- [ ] **Step 5: Add `ADAPTIVE_EXIT_MODE=OFF|SHADOW|ADVISORY|LIVE_BOUNDED` defaulting to OFF**

Only OFF/SHADOW are used in this task. SHADOW evaluates and logs after the point-in-time data are assembled but before any order mutation; no additional exchange market-data calls are introduced solely for adaptive calculations.

- [ ] **Step 6: Run CORE focused regressions**

Run: `pytest -q tests/test_core_adaptive_exit_shadow.py tests/test_core_medium_reversal.py tests/test_core_profit_lock.py tests/test_live.py tests/test_service.py`
Expected: new shadow test passes; pre-existing baseline failures, if any, must match the untouched active-release baseline exactly.

- [ ] **Step 7: Commit**

```bash
git add trader.py config.py tests/test_core_adaptive_exit_shadow.py
git commit -m "feat: shadow adaptive exits for core"
```

### Task 9: Dashboard observability and ADVISORY mode

**Files:**
- Modify: `web_app.py:500-620`
- Modify: `templates/dashboard.html`
- Test: `tests/test_adaptive_exit_dashboard.py`

**Interfaces:**
- `/api/state` adds read-only `adaptive_exit` per symbol.
- Dashboard shows mode, stop source, configured/effective margin, planned loss USDT, TP1/TP2/runner, Gemini state, learning state, latest rejection/change reason.

- [ ] **Step 1: Write failing API-shape and escaping tests**

Assert absent adaptive records render as `status="NO_DATA"`; malformed audit data cannot inject HTML and cannot make the page fail.
- [ ] **Step 2: Write failing ADVISORY test**

ADVISORY displays the adaptive plan and delta versus legacy but still sends legacy order/protection arguments. UI must label it as advisory and never imply the adaptive plan is active.

- [ ] **Step 3: Run tests and verify RED**
Run: `pytest -q tests/test_adaptive_exit_dashboard.py`
Expected: `adaptive_exit` state absent.

- [ ] **Step 4: Implement API aggregation and dashboard cards**

Keep account/cashflow and lifecycle accounting labels separate. Show configured margin and effective risk-capped margin side-by-side so a 500 USDT fixed-margin target can visibly shrink when structural-stop risk requires it.

- [ ] **Step 5: Run tests and commit**

```bash
pytest -q tests/test_adaptive_exit_dashboard.py tests/test_candidate_c_sizing_selector_ui.py
git add web_app.py templates/dashboard.html tests/test_adaptive_exit_dashboard.py
git commit -m "feat: show adaptive exit advisory state"
```

### Task 10: Historical/recent replay and policy comparison report

**Files:**
- Create: `adaptive_exit_replay.py`
- Create: `adaptive_exit_report.py`
- Test: `tests/test_adaptive_exit_replay.py`
- Test: `tests/test_adaptive_exit_report.py`

**Interfaces:**
- Produces: `replay_lifecycle(user_dir, lifecycle, policy) -> ReplayResult` using only data timestamped at or before each decision point.
- Produces: `compare_policies(results) -> dict` with legacy/adaptive lifecycle Net, PF, max loss, tail quantiles, MAE/MFE capture, giveback, action count, fee/gross ratio, stop-out/post-stop excursion, and risk violations.

- [ ] **Step 1: Write failing no-lookahead replay test**

Alter candles after each historical decision timestamp and assert the already-computed adaptive decision for that timestamp is unchanged.

- [ ] **Step 2: Add mandatory real-sequence fixtures**

Cover the 2026-09-23 DOGE Candidate C ~2,500 USDT notional lifecycle, the 2026-09-25 DOGE/SOL partial-reduction lifecycles, representative CORE repeated-REDUCE sequences, and restart/reconciliation with existing stops.
- [ ] **Step 3: Run replay tests and verify RED**
Run: `pytest -q tests/test_adaptive_exit_replay.py tests/test_adaptive_exit_report.py`
Expected: replay/report modules absent.

- [ ] **Step 4: Implement replay with strict timestamp cutoffs**

Use stored/cached confirmed candles only. If required evidence is missing, mark the sample unresolved and exclude it from optimization statistics rather than filling gaps.

- [ ] **Step 5: Implement comparison acceptance metrics**

Report legacy and adaptive side-by-side. A candidate policy is not acceptable merely because Net is higher; require zero risk-cap violations, zero active-stop loosening, improved or non-worse tail loss, controlled fee/action count, and stability across folds/windows.

- [ ] **Step 6: Run tests and commit**

```bash
pytest -q tests/test_adaptive_exit_replay.py tests/test_adaptive_exit_report.py
git add adaptive_exit_replay.py adaptive_exit_report.py tests/test_adaptive_exit_replay.py tests/test_adaptive_exit_report.py
git commit -m "feat: replay adaptive exit policies"
```

### Task 11: Candidate C LIVE_BOUNDED adapter, disabled by default

**Files:**
- Modify: `candidate_c_decision_engine.py:280-404`
- Modify: `candidate_c_hybrid_live_adapter.py:150-225`
- Modify: `candidate_c_exit_management.py`
- Test: `tests/test_candidate_c_adaptive_exit_live_bounded.py`

**Interfaces:**
- `LIVE_BOUNDED` can replace initial stop/size and tightening/target decisions only after policy version is explicitly enabled.
- Existing Candidate C intent ledger, risk reservation, protection verification, 4H invalidation, and stop-loosening rejection remain authoritative.

- [ ] **Step 1: Write failing opt-in authority tests**

Assert SHADOW/ADVISORY never change intents; `LIVE_BOUNDED` with an unapproved/missing policy hash also cannot change intents.

- [ ] **Step 2: Write failing fixed-margin risk-cap regression**

Replay the 2026-09-23 DOGE entry geometry and assert a 500 USDT configured margin is automatically reduced when the structural stop would violate the approved dollar-risk budget.
- [ ] **Step 3: Write failing restart/tighten-only tests**

A restarted position reloads its entry policy hash and exchange stop. New calculations may retain or tighten that stop only; a newer policy cannot widen it.

- [ ] **Step 4: Run tests and verify RED**
Run: `pytest -q tests/test_candidate_c_adaptive_exit_live_bounded.py`
Expected: live-bounded translation absent.

- [ ] **Step 5: Implement validated-plan-to-intent translation**

For new entry, translate approved adaptive stop/risk-capped size into existing `EntryIntent` and risk reservation. For held positions, translate only tightening/partial/exit decisions that pass existing priority and protection rules. Do not rewrite exchange adapters.

- [ ] **Step 6: Run Candidate C suite and commit**

```bash
pytest -q tests/test_candidate_c_*.py tests/test_adaptive_exit_*.py
git add candidate_c_decision_engine.py candidate_c_hybrid_live_adapter.py candidate_c_exit_management.py tests/test_candidate_c_adaptive_exit_live_bounded.py
git commit -m "feat: add bounded adaptive exits to candidate c"
```

### Task 12: CORE LIVE_BOUNDED adapter with fee/churn guards, disabled by default

**Files:**
- Modify: `trader.py:1457-1470,1674-1945,2230-2915,4290-4588`
- Modify: `risk_manager.py:14-100`
- Test: `tests/test_core_adaptive_exit_live_bounded.py`

**Interfaces:**
- Adaptive entry size/SL/target ladder are used only when `ADAPTIVE_EXIT_MODE=LIVE_BOUNDED` and policy hash is operator-approved.
- Existing 0.50R/0.75R/0.90R hard loss defense and Gemini+GPT CLOSE/REDUCE escalation may override adaptive management but adaptive logic may never suppress them.

- [ ] **Step 1: Write failing emergency-precedence tests**

If adaptive plan says HOLD while hard 0.90R emergency close fires, the actual action must remain CLOSE. If 0.50R/0.75R loss-defense requires a reduction, adaptive target logic cannot cancel it.

- [ ] **Step 2: Write failing anti-churn economic-benefit tests**

Repeated reduction is blocked unless there is new structural/AI evidence and expected post-fee economic benefit exceeds the configured threshold. Same evidence ID within cooldown must produce no new reduce order.
- [ ] **Step 3: Write failing policy-approval and legacy-fallback tests**

`LIVE_BOUNDED` without the exact approved policy hash must fall back to legacy execution and emit a high-severity diagnostic. OFF/SHADOW/ADVISORY keep legacy order arguments unchanged.

- [ ] **Step 4: Run tests and verify RED**
Run: `pytest -q tests/test_core_adaptive_exit_live_bounded.py`
Expected: live-bounded CORE translation absent.

- [ ] **Step 5: Implement CORE plan translation behind explicit mode/hash gate**

At entry, replace fixed `sl_tp_prices()` and risk sizing only inside the approved live-bounded branch. For held positions, adaptive tightening/target actions enter the existing protection-update machinery so current exchange verification and monotonic checks remain in force.

- [ ] **Step 6: Run focused CORE and adaptive suites**

Run: `pytest -q tests/test_core_adaptive_exit_live_bounded.py tests/test_core_adaptive_exit_shadow.py tests/test_adaptive_exit_*.py tests/test_core_medium_reversal.py tests/test_core_profit_lock.py tests/test_live.py tests/test_service.py`
Expected: no new failures compared with the untouched active-release baseline.

- [ ] **Step 7: Commit**

```bash
git add trader.py risk_manager.py tests/test_core_adaptive_exit_live_bounded.py
git commit -m "feat: add bounded adaptive exits to core"
```

### Task 13: End-to-end validation, release packaging, and activation gate

**Files:**
- Create: `docs/adaptive_exit_validation.md`
- Modify only if tests expose defects: files owned by Tasks 1-12.

**Interfaces:**
- Produces a release candidate with default adaptive execution disabled.
- Produces an evidence report that an operator can use to approve or reject a specific policy hash for Candidate C and later CORE.

- [ ] **Step 1: Establish untouched production-release baseline**

Run the same focused suites against `/opt/autotrader-releases/candidate_c_sizing_selector_20260925T2305KST` and record existing failures/counts before comparing the implementation workspace.
- [ ] **Step 2: Run complete adaptive/Candidate C suites**

Run: `pytest -q tests/test_adaptive_exit_*.py tests/test_candidate_c_*.py`
Expected: all new adaptive and Candidate C tests pass.

- [ ] **Step 3: Run the broader `tests/` baseline comparison**

Run: `pytest -q tests`
Expected: no failures beyond those reproduced on the untouched production release. Any new failure blocks release packaging.

- [ ] **Step 4: Run replay comparison and write validation report**

The report must include legacy/adaptive lifecycle Net, PF, maximum lifecycle loss, tail-loss quantiles, action/reduction count, fee/gross ratio, risk violations, stop-loosening violations, unresolved samples, policy hash, and exact commands used. It must explicitly call out the 2026-09-23 DOGE tail-loss fixture.

- [ ] **Step 5: Verify production-safe defaults**

Assert deployed configuration defaults to OFF or SHADOW, no existing position protection is mutated by service restart, Gemini/learning failures preserve deterministic fallback, and no policy can become LIVE_BOUNDED without the exact operator-approved hash.

- [ ] **Step 6: Package a new immutable release without switching `/opt/autotrader`**

Copy the validated workspace to a timestamped `/opt/autotrader-releases/adaptive_exit_<timestamp>` directory, preserve shared `.venv` and user-state symlink conventions, and run the targeted suite inside that release. Do not change the active symlink or restart services in this step.

- [ ] **Step 7: Stop at operator activation gate**

Present the validation report and exact candidate policy hash. Candidate C activation requires explicit approval after reviewing replay/forward-shadow evidence. CORE activation requires a later independent approval after Candidate C forward evidence and CORE fee/churn validation.

## Execution Workspace Procedure
1. Detect whether an existing isolated worktree/workspace already exists.
2. Because the local Git checkout is heavily dirty and does not safely represent the active release, do not edit it in place for implementation.
3. Create an isolated implementation workspace from the active production release under `/home/bagmingi/autotrader-work/adaptive_exit_engine_<timestamp>` on the VM, or use a native worktree only if it can be proven to match the active release byte-for-byte for touched files.
4. Record SHA-256 for each touched baseline production file before editing.
5. Use TDD task-by-task; do not change the production symlink during implementation or validation.
6. Apply only reviewed diffs to the immutable release candidate.
7. No push or production activation occurs as part of this implementation plan unless separately approved.

## Final Definition of Done
- Deterministic Adaptive Exit Engine exists with versioned policy/hash.
- FIXED_MARGIN and VARIABLE_RISK both obey the same dollar-risk invariant.
- Active stops cannot loosen under any AI, learning, restart, or config path.
- Target ladder and profit lock are cost-aware and bounded.
- Gemini has state/advisory authority only, never raw price/order authority.
- Learning is evidence-driven and cannot auto-promote itself to live.
- Candidate C and CORE SHADOW modes are execution-identical to legacy.
- Replay shows zero risk-cap and stop-loosening violations.
- LIVE_BOUNDED code paths are disabled until exact policy-hash approval.
- Existing exchange protection and hard emergency controls remain authoritative.
