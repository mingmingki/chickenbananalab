# Startup Protection-query Pacing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent restart-time OKX pending-protection read bursts without weakening UNKNOWN/fail-safe protection semantics.

**Architecture:** Introduce a process-wide lock and monotonic minimum spacing around the single shared `fetch_pending_protection_orders()` network boundary. Keep ccxt retry/backoff and all callers unchanged; only the pending-algo HTTP call is serialized/paced.

**Tech Stack:** Python 3.10+, `threading.Lock`, `time.monotonic`, existing ccxt client, pytest.

**Spec:** `docs/superpowers/specs/2026-09-25-exit-reentry-loss-control-design.md`

## Global Constraints
- Pace only pending-protection reads; do not globally throttle market data, position, or order-submission calls.
- Existing 429 retry/backoff and fail-safe UNKNOWN behavior remains authoritative.
- Concurrent XRP/SOL/DOGE checks must not issue simultaneous pending-algo HTTP calls.
- Pacing must not convert an error into VERIFIED.
- No commit, push, or deploy without separate explicit approval.

## Review Focus
1. Concurrent callers serialize at the network boundary, not merely at higher-level UI/service code.
2. Both `oco` and `conditional` reads remain inside one paced ownership window per symbol query.
3. A 429 after pacing still propagates through existing UNKNOWN/fail-safe handling.
4. Steady-state sequential calls should incur only the configured minimum spacing, not cumulative sleep from unrelated APIs.
5. The lock must always release on exceptions.

---
### Task 1: Shared pending-protection read pacer
**Files:** Create `okx_read_pacing.py`; modify `okx_client.py` around `fetch_pending_protection_orders()`; test `tests/test_okx_protection_read_pacing.py`.

**Interfaces:** `paced_pending_algo_read(callable_, *, min_spacing_seconds) -> Any`; process-wide lock + last-call monotonic timestamp are module-private.

- [ ] **Step 1: Write RED concurrency test** with three threads representing XRP/SOL/DOGE and a fake network function that records call start times.
```python
def test_three_concurrent_protection_reads_are_serialized():
    starts = run_three_concurrent_reads()
    assert starts == sorted(starts)
    assert min(b-a for a,b in zip(starts, starts[1:])) >= 0.05
```
- [ ] **Step 2: Write RED exception test** proving a raised 429 releases the lock and the next caller still runs.
- [ ] **Step 3: Run RED:** `python -m pytest tests/test_okx_protection_read_pacing.py -q`.
- [ ] **Step 4: Implement pacer** with `threading.Lock`, `time.monotonic`, and sleep only for remaining spacing. Do not catch/translate the underlying exception.
- [ ] **Step 5: Wrap the existing two-call `oco`/`conditional` loop** inside one paced section in `fetch_pending_protection_orders()`.
- [ ] **Step 6: Run GREEN:** `python -m pytest tests/test_okx_protection_read_pacing.py -q`.

### Task 2: Fail-safe and startup replay regression
**Files:** Test `tests/test_okx_protection_read_pacing.py`, existing protection/service tests.

- [ ] **Step 1: Add 16:24:01 burst replay**: start three concurrent protection verification paths and assert network reads are staggered while all three results preserve their original match/UNKNOWN semantics.
- [ ] **Step 2: Add 429 test** where the fake exchange raises `RateLimitExceeded`; assert caller sees existing failure/UNKNOWN path and never a synthetic VERIFIED result.
- [ ] **Step 3: Run regression:** `python -m pytest tests/test_okx_protection_read_pacing.py tests/test_core_live_protection_display.py tests/test_candidate_c_monitoring_observability.py tests/test_service.py -q` and compare `test_service.py` only against its known baseline failures.
- [ ] **Step 4: Save evidence only** to `/tmp/startup_protection_pacing.diff`; no commit/push/deploy.
