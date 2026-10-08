# Exit Counterfactual + Lifecycle P&L Reporting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Measure AI-exit quality and re-entry churn with append-only shadow evidence while making full lifecycle economic P&L the default strategy-analysis basis.

**Architecture:** Extend completed lifecycle rows with close-vs-reduce reconciliation fields, then add an append-only `exit_reentry_shadow.py` journal. AI CLOSE records are created after confirmed close; later normal CORE cycles feed already-fetched confirmed candles into unresolved +15/+30/+60/+120m observations. Analysis/report code links shadow exits to full lifecycles and next same-side lifecycles without mutating orders.

**Tech Stack:** Python 3.10+, existing JSONL cache/helpers, pandas, pytest.

**Spec:** `docs/superpowers/specs/2026-09-25-exit-reentry-loss-control-design.md`

## Global Constraints
- Strategy-quality metrics use lifecycle economic Net: all REDUCE + final CLOSE + recorded fee/funding/adjustment.
- Account cashflow/account-bills remains a separate authoritative accounting basis and must not be replaced.
- Horizon observations use nearest confirmed observation at/after 15/30/60/120m; never fabricate exact fills or use future data before the horizon.
- Shadow analytics must make zero exchange/order mutations and add no API calls.
- Close-only Net remains diagnostic only.
- No commit, push, or deploy without separate explicit approval.

## Review Focus
1. A lifecycle with two REDUCE events must reconcile exactly to `reduce_net + final_close_net == lifecycle_net`.
2. Partial/unmatched lifecycles must remain labeled partial and must not be silently promoted to complete evidence.
3. Horizon resolution must never select a candle timestamp before the requested horizon.
4. Missing post-exit data must stay `unresolved`, not become zero P&L.
5. Report reason/symbol attribution must use lifecycle Net rather than the final close row alone.

---
### Task 1: Lifecycle reconciliation fields
**Files:** Modify `trade_learning_lifecycle.py`; test `tests/test_trade_learning_lifecycle.py`, `tests/test_dashboard_economic_pnl.py`.

**Interfaces:** Each completed lifecycle adds `lifecycle_net`, `final_close_net`, `reduce_net`, `difference_due_to_reduces`, `final_close_reason`; `net_pnl` remains equal to `lifecycle_net` for compatibility.

- [ ] **Step 1: Write RED tests** for zero/one/two REDUCE events and partial lifecycle coverage.
```python
def test_two_reduces_reconcile_to_lifecycle_net(tmp_path):
    life = build_fixture_lifecycle(tmp_path, reduce_nets=[-6.75, -9.90], close_net=-18.63)
    assert life['lifecycle_net'] == pytest.approx(-35.28, abs=.02)
    assert life['reduce_net'] == pytest.approx(-16.65, abs=.02)
    assert life['final_close_net'] == pytest.approx(-18.63, abs=.02)
```
- [ ] **Step 2: Run RED:** `python -m pytest tests/test_trade_learning_lifecycle.py tests/test_dashboard_economic_pnl.py -q`.
- [ ] **Step 3: Implement `_finish()` reconciliation** from realized event rows only; do not infer missing funding/fees.
- [ ] **Step 4: Run GREEN** with the same command.

### Task 2: Append-only AI-exit counterfactual journal
**Files:** Create `exit_reentry_shadow.py`; modify `trader.py`; test `tests/test_exit_reentry_shadow.py`.

**Interfaces:** `record_ai_exit(user_dir, exit_record) -> dict`; `observe_confirmed_market(user_dir, symbol, observation_time, close, high, low) -> list[dict]`; `resolve_links(user_dir, lifecycles) -> list[dict]`; `recent(user_dir, limit=50) -> list[dict]`.

- [ ] **Step 1: Write RED tests** for +15/+30/+60/+120 horizon resolution, no-lookahead, unresolved fallback, original SL/TP crossing flags, same-side re-entry within 30/60/120m, and duplicate suppression.
- [ ] **Step 2: Run RED:** `python -m pytest tests/test_exit_reentry_shadow.py -q`.
- [ ] **Step 3: Record exit sample** only after confirmed `position_ai_close_all`; include symbol, side, exit time/price, original SL/TP when known, Gemini assessment, correction flag when already available, and final close reason.
- [ ] **Step 4: Feed observations from normal CORE cycles** using confirmed candle data already in memory. `observe_confirmed_market()` must never call `OkxClient`.
- [ ] **Step 5: Link completed lifecycles** by symbol/side/exit timestamp, then link the next same-symbol/same-side completed lifecycle. Store `actual_exit_lifecycle_net`, `next_lifecycle_net`, `churn_cycle_net`, and analytical outcome (`exit_saved_loss`, `exit_missed_recovery`, `reentry_profitable`, `reentry_loss`, `unresolved`).
- [ ] **Step 6: Prove read-only authority:** monkeypatch order/create/cancel methods to raise and assert shadow resolution still passes without invoking them.
- [ ] **Step 7: Run GREEN:** `python -m pytest tests/test_exit_reentry_shadow.py tests/test_trade_learning_lifecycle.py -q`.

### Task 3: Analysis/report defaults
**Files:** Modify `trade_pattern_analysis.py`, `web_app.py`, `analysis_report.py`; test `tests/test_trade_pattern_analysis.py`, `tests/test_analysis_report.py`, `tests/test_analysis_report_dashboard.py`.

- [ ] **Step 1: Write RED report tests** asserting AI CLOSE attribution uses `lifecycle_net`, while `final_close_net` and `reduce_net` are shown separately.
- [ ] **Step 2: Add analysis payload `exit_reentry`** with AI CLOSE lifecycle count/Net, close-only Net, reduce Net, same-side re-entry rates 30/60/120m, subsequent lifecycle Net, resolved/unresolved counts, and churn-cycle Net.
- [ ] **Step 3: Add `[17. Exit/Re-entry]`** to `analysis_report.py`; label lifecycle accounting and observational/counterfactual status explicitly.
- [ ] **Step 4: Replay the 2026-09-25 XRP sequence** and assert the 11:09 lifecycle is approximately -35.29 rather than -18.63 close-only.
- [ ] **Step 5: Run regression:** `python -m pytest tests/test_trade_learning_lifecycle.py tests/test_exit_reentry_shadow.py tests/test_trade_pattern_analysis.py tests/test_analysis_report.py tests/test_analysis_report_dashboard.py tests/test_dashboard_economic_pnl.py -q`.
- [ ] **Step 6: Save diff/test evidence only** under `/tmp/exit_counterfactual_lifecycle_final.diff`; no commit/push/deploy.
