# Same-side Thesis Re-entry Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent `position_ai_close_all` churn by requiring durable same-side thesis recovery before a new same-side CORE entry can reach the normal Gemini/GPT gate.

**Architecture:** Add a dedicated atomic state module for AI-close thesis locks. `_execute_close()` records the durable lock after a confirmed close; `run_cycle()` evaluates recovery from already-fetched 1H/5m data before the normal entry gate. Existing 15-minute direction-agnostic cooldown stays authoritative.

**Tech Stack:** Python 3.10+, pandas, existing atomic JSON utilities, pytest.

**Spec:** `docs/superpowers/specs/2026-09-25-exit-reentry-loss-control-design.md`

## Global Constraints
- Keep 0.75R hard close, 0.90R emergency close, SL/TP, daily-loss, kill switch, protection verification, GPT Entry Gate, and existing 15-minute AI-close cooldown unchanged.
- Same-side minimum lock after AI CLOSE is exactly 30 minutes.
- LONG recovery = confirmed 1H `close > EMA20 > EMA50` + two consecutive confirmed 5m closes above EMA20; SHORT is the exact mirror.
- Use only data already fetched by CORE; no new exchange/API request.
- Missing recovery evidence is fail-closed for same-side re-entry. Thesis-state write failure must never cancel/delay the close.
- No commit, push, or deploy without separate explicit approval.

## Review Focus
1. Restart must preserve the same 30-minute timer.
2. First 15 minutes remain direction-agnostic; after that, opposite-side behavior stays on existing safeguards.
3. One qualifying 5m bar is insufficient; two confirmed bars are required.
4. In-progress/stale 5m bars must not count.
5. State-write failure after close must fall back to the existing 15-minute lock.

---
### Task 1: Durable thesis state + close integration
**Files:** Create `core_reentry_thesis.py`; modify `trader.py`; test `tests/test_core_reentry_thesis.py`.

**Interfaces:** `record_ai_close(user_dir, symbol, side, close_time, assessment, minimum_minutes=30) -> dict`; `get(user_dir, symbol) -> dict|None`; `evaluate_same_side(record, side, now, confirmed_1h, confirmed_5m) -> dict`; `clear_recovered(user_dir, symbol, evidence, now) -> dict`.

- [ ] **Step 1: Write RED tests** for persistence, LONG/SHORT symmetry, 30-minute minimum, one-vs-two confirmed 5m bars, missing evidence fail-closed, restart reload, and non-AI closes not creating thesis state.
```python
def test_two_confirmed_5m_bars_required(tmp_path):
    rec = make_long_close_record(minimum_until='2026-09-25T14:00:00')
    r = crt.evaluate_same_side(rec, 'long', dt.datetime.fromisoformat('2026-09-25T14:01:00'), one_h_bullish(), one_closed_5m_above())
    assert r['blocked'] is True and r['confirmed_5m_recovery_count'] == 1
```
- [ ] **Step 2: Run RED:** `python -m pytest tests/test_core_reentry_thesis.py -q`; expect import/function failures.
- [ ] **Step 3: Implement atomic JSON state** using the same save pattern as `core_manual_close.py`. Persist symbol, closed side, close time, assessment, `minimum_until`, status, evidence, and clear time.
- [ ] **Step 4: Integrate `_execute_close()`** only after `trade_log.record_close(...)` succeeds. On write exception log `CORE_REENTRY_THESIS_STATE_WRITE_FAILED`, retain the existing in-memory 15-minute lock, and still return successful close.
- [ ] **Step 5: Run GREEN:** `python -m pytest tests/test_core_reentry_thesis.py tests/test_core_exit_escalation_20260924.py -q`.

### Task 2: Entry-path enforcement and observability
**Files:** Modify `trader.py`, `web_app.py`, `templates/dashboard.html`; test `tests/test_core_reentry_thesis.py`, `tests/test_dashboard_engine_alignment.py`.

- [ ] **Step 1: Write RED tests** proving 20-minute same-side is blocked before GPT, opposite side after the legacy 15-minute lock is not blocked by the new thesis gate, 30+ minute same-side still requires recovery, recovered same-side still requires normal GPT approval, and the gate makes zero extra client calls.
- [ ] **Step 2: Implement gate** from existing `raw_dfs` using `candle_finality.split_live_closed`; never fetch fresh candles. If recovery is incomplete, record `LOCAL_BLOCKED` reason `ai_close_thesis_not_recovered` and return before GPT.
- [ ] **Step 3: Publish diagnostics**: `reentry_thesis_status`, `reentry_thesis_minimum_remaining_seconds`, `reentry_thesis_1h_pass`, `reentry_thesis_5m_count`, `reentry_thesis_last_clear_reason`.
- [ ] **Step 4: Replay 2026-09-25 XRP**: 10:32 AI CLOSE -> 11:09 LONG candidate. Assert the candidate is blocked unless exact recovery evidence exists.
- [ ] **Step 5: Run focused regression:** `python -m pytest tests/test_core_reentry_thesis.py tests/test_core_exit_escalation_20260924.py tests/test_core_live_protection_display.py tests/test_gpt_entry_gate_dashboard.py tests/test_dashboard_engine_alignment.py -q`.
- [ ] **Step 6: Save evidence only:** `git diff -- core_reentry_thesis.py trader.py web_app.py templates/dashboard.html tests/test_core_reentry_thesis.py tests/test_dashboard_engine_alignment.py > /tmp/reentry_thesis_final.diff`; no commit/push/deploy.
