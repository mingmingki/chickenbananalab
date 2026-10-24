# Exit/Re-entry Self-learning Shadow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend learning with exit/re-entry evidence without changing entry, exit, reduce, or re-entry execution authority.

**Architecture:** Keep `learning_adapter.evaluate_entry()` unchanged. Add a separate append-only exit/re-entry learning stream that consumes resolved `exit_reentry_shadow` rows and produces pattern evidence/checkpoints only. No exit/re-entry pattern may become `LIVE_BOUNDED` in this version.

**Tech Stack:** Python 3.10+, existing learning JSONL/state/checkpoint patterns, pytest.

**Spec:** `docs/superpowers/specs/2026-09-25-exit-reentry-loss-control-design.md`

## Global Constraints
- Entry learner LIVE interface remains untouched.
- Exit/re-entry learner is SHADOW ONLY: no order mutation, no close/reduce change, no re-entry veto, no `LIVE_BOUNDED` promotion.
- Use lifecycle Net and churn-cycle Net, never final-close-only Net.
- Duplicate exit/re-entry evidence must be idempotently suppressed.
- No commit, push, or deploy without separate explicit approval.

## Review Focus
1. Restart/re-run must not duplicate resolved samples or checkpoint contributions.
2. Missing next lifecycle keeps churn evidence unresolved rather than zero.
3. A profitable re-entry and a losing re-entry must contribute opposite observed directions without changing execution.
4. `LIVE_BOUNDED` must remain impossible for this evidence stream even when sample thresholds are exceeded.
5. Existing `learning_adapter.evaluate_entry()` return values must be byte-for-byte/structure compatible with current tests.

---
### Task 1: Shadow evidence stream
**Files:** Create `learning_exit_reentry.py`; modify `ai_strategy_review.py` or the deterministic checkpoint preparation path only to call it; test `tests/test_learning_exit_reentry.py`.

**Interfaces:** `ingest_resolved(user_dir, resolved_exit_rows) -> list[dict]`; `summarize_evidence(user_dir) -> dict[str, dict]`; `recent_samples(user_dir, limit=50) -> list[dict]`.

- [ ] **Step 1: Write RED tests** for feature extraction (`exit_reason`, assessment, correction flag, side, symbol, regime, re-entry 30/60/120m, outcome, next lifecycle Net, churn-cycle Net), idempotency, and unresolved samples.
- [ ] **Step 2: Run RED:** `python -m pytest tests/test_learning_exit_reentry.py -q`.
- [ ] **Step 3: Implement append-only sample storage** keyed by stable exit sample id + next lifecycle id when resolved.
- [ ] **Step 4: Implement evidence aggregation** with sample count, resolved count, churn-cycle Net sum, recent direction, and coverage. Do not reuse entry `policy_benefit_net` semantics.
- [ ] **Step 5: Run GREEN:** `python -m pytest tests/test_learning_exit_reentry.py -q`.

### Task 2: Checkpoint/report integration with zero authority
**Files:** Modify `ai_strategy_review.py`, `learning_state.py` only if a separate namespace accessor is required, `web_app.py`, `analysis_report.py`; test `tests/test_learning_exit_reentry.py`, `tests/test_learning_read_only_contract.py`, `tests/test_analysis_report.py`.

- [ ] **Step 1: Write RED authority tests** monkeypatching order/close/reduce/re-entry functions to raise if called; ingest/checkpoint/report must complete without invoking them.
- [ ] **Step 2: Add separate state namespace** `exit_reentry_shadow` with states limited to `DISCOVERY`, `SHADOW_LEARNING`, `VALIDATED_SHADOW`, `REJECTED`; no code path may emit `LIVE_BOUNDED`.
- [ ] **Step 3: Add report fields** for resolved sample count, churn loss/profit totals, repeated same-side re-entry patterns, and latest evidence. Label them `Shadow only · 실전 영향 없음`.
- [ ] **Step 4: Assert entry learner compatibility:** `python -m pytest tests/test_learning_adapter.py tests/test_core_self_learning_gate.py tests/test_learning_read_only_contract.py tests/test_learning_exit_reentry.py tests/test_analysis_report.py -q`.
- [ ] **Step 5: Save evidence only** to `/tmp/exit_reentry_learning_shadow.diff`; no commit/push/deploy.
