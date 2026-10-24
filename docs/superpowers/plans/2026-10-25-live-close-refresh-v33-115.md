# Live close refresh v33-115 Implementation Plan

> **For agentic workers:** Execute inline using superpowers:executing-plans; request one final independent code review.

**Goal:** Display a confirmed flat exchange position immediately and journal exchange closes on the existing 30-second worker tick instead of waiting for the 300-second AI cycle.

**Architecture:** Keep the display thread observational. Reuse the existing external-close recorder from both the main cycle and its same-thread fast tick; no extra AI review, entry attempt, or concurrent ledger writer.

**Tech Stack:** Python, Flask, JavaScript, pytest, Node.js.

**Spec:** Existing approved priority: correct closed-position display and delayed ledger synchronization.

## Global Constraints
- Preserve effective settings, CORE/Candidate C requested running states, live protection, risk 1%, leverage 5, daily limits CORE 5% / Candidate C 10%.
- No discretionary market orders or stop widening. Do not change entry, partial-reduction, or profit-retention policies.
- Preserve exact fill attribution, stable execution IDs, pending-intent guards and retry behavior.
- A failed/unknown exchange read is never a confirmed close.

## Review Focus
- Confirmed flat must override a stale cached position; unknown observations retain their fallback.
- A new non-null live position must override an older flat protection observation.
- Pending entry/reduce/add/manual settlement must retain priority over external-close recording.
- Journal failure must retain the cached position for retry; repeat ticks must not duplicate the close.
- The fast accounting path must neither place orders nor request candles/paid AI after recognizing a close.

## Task 1: Closed-position display
Files: autotrader/templates/dashboard.html; autotrader/tests/test_live_close_refresh_v33_115.py.
- [ ] Add behavior tests executing the shipped position-selector expression in Node: confirmed flat, unknown fallback, new live position, empty initial state.
- [ ] Run the new UI tests and observe stale-position failure.
- [ ] Add a small selector distinguishing confirmed flat from an unknown live value and wire the position card to it.
- [ ] Run UI and existing dashboard refresh tests.

## Task 2: External-close accounting on the existing fast tick
Files: autotrader/trader.py; autotrader/tests/test_live_close_refresh_v33_115.py.
- [ ] Add tests proving a confirmed external close is recorded on a fast tick without candles/AI/order calls; repeats are idempotent; network/ledger errors and pending recovery retain state.
- [ ] Run backend regression tests and observe no-journal failure.
- [ ] Extract the existing external-close block unchanged into a helper that clears cached position only after recording succeeds.
- [ ] Call the helper from the main cycle and, after existing settlement/manual guards, from the fast tick for a confirmed flat position.
- [ ] Run focused regression tests and compare the complete canonical suite with the baseline.

## Task 3: Review and deploy
- [ ] Independently review the branch and address important findings with failing tests first.
- [ ] Commit and push the isolated branch.
- [ ] Package only changed code/tests and a source manifest; stage from v33-114 with identical runtime symlinks/configuration.
- [ ] Run server-focused tests and existing guarded deployment preflight, preserving requested engines and settings hash.
- [ ] Deploy, verify resume checkpoint, protection/quantity parity, API state and source hashes.
- [ ] Report exact deployment scope and a separate same-basis account-peak/realized/unrealized analysis.