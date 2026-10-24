# Candidate C Breakout-failure Shadow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Measure Candidate C breakout failure after entry without changing the parity-pinned Candidate C execution path.

**Architecture:** Build a read-only shadow analyzer outside `candidate_c_trader_adapter.py`/decision execution. It consumes existing setup/epoch provenance and already-recorded monitor/candle data, reconstructs the original breakout reference only when exact provenance exists, and links the observation to the completed lifecycle outcome.

**Tech Stack:** Python 3.10+, existing Candidate C epoch/setup stores, JSONL, pytest.

**Spec:** `docs/superpowers/specs/2026-09-25-exit-reentry-loss-control-design.md`

## Global Constraints
- Do not modify parity-pinned Candidate C order/intent decision logic or its execution adapter for this feature.
- Missing setup provenance is `unresolved`; never infer or reconstruct from a later Donchian line.
- Shadow analyzer cannot emit Entry/Reduce/Close/StopUpdate intents.
- Use lifecycle economic P&L for final outcomes.
- No commit, push, or deploy without separate explicit approval.

## Review Focus
1. Original setup breakout reference must come from the exact setup/epoch provenance, not the current rolling Donchian value.
2. Missing/corrupt provenance must remain unresolved.
3. An open position must never be shown as a fresh-entry `Donchian 돌파 대기` sample in the shadow result.
4. Analyzer execution must cause zero exchange/order mutations and zero Candidate C parity hash changes.
5. Partial de-risk status and final lifecycle outcome must both survive restart/re-read.

---
### Task 1: Read-only breakout-failure analyzer
**Files:** Create `candidate_c_breakout_shadow.py`; test `tests/test_candidate_c_breakout_shadow.py`.

**Interfaces:** `analyze_open_position(user_dir, symbol, epoch, setup_record, monitor_rows) -> dict`; `resolve_completed(user_dir, lifecycles) -> list[dict]`; `recent(user_dir, limit=50) -> list[dict]`.

- [ ] **Step 1: Write RED provenance tests** for exact setup id, missing setup id, mismatched epoch/setup, and restart reload.
```python
def test_missing_setup_provenance_is_unresolved():
    row = cbs.analyze_open_position(user_dir, 'DOGE/USDT:USDT', epoch_without_setup(), None, monitor_rows())
    assert row['status'] == 'unresolved'
    assert row['breakout_reference'] is None
```
- [ ] **Step 2: Run RED:** `python -m pytest tests/test_candidate_c_breakout_shadow.py -q`.
- [ ] **Step 3: Implement read-only calculations**: original breakout reference, signed distance back through breakout level, elapsed time since entry, adverse excursion when supported by recorded high/low, `derisk_done`, partial-take-profit flags, and provenance fields.
- [ ] **Step 4: Add zero-mutation test** monkeypatching Candidate C intent/order functions to raise if called; analyzer must still pass.
- [ ] **Step 5: Link completed lifecycle** using exact position/setup identity when available and store `lifecycle_net`, final reason, and resolution timestamp.
- [ ] **Step 6: Run GREEN:** `python -m pytest tests/test_candidate_c_breakout_shadow.py -q`.

### Task 2: Separate observational reporting
**Files:** Modify `web_app.py`, `analysis_report.py`, `templates/dashboard.html`; test `tests/test_analysis_report.py`, `tests/test_candidate_c_monitoring_observability.py`.

- [ ] **Step 1: Write RED report test** asserting breakout-failure Shadow is shown separately from Candidate C live stage and labeled `관찰용 · 실주문 영향 없음`.
- [ ] **Step 2: Add payload** with resolved/unresolved count, distance-through-breakout buckets, derisk status, adverse excursion, and lifecycle Net; never reuse live intent/action fields.
- [ ] **Step 3: Add report subsection** after Exit/Re-entry, and dashboard observational card if data exists.
- [ ] **Step 4: Prove parity untouched:** compare the deployment parity hash inputs before/after; `candidate_c_trader_adapter.py`, `candidate_c_decision_engine.py`, and execution-policy files must be byte-identical.
- [ ] **Step 5: Run regression:** `python -m pytest tests/test_candidate_c_breakout_shadow.py tests/test_candidate_c_monitoring_observability.py tests/test_analysis_report.py tests/test_candidate_c_lifecycle_separation.py -q`.
- [ ] **Step 6: Save evidence only** to `/tmp/candidate_c_breakout_shadow.diff`; no commit/push/deploy.
