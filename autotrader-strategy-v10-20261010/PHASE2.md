# V10 phase 2 — implemented, qualification IN PROGRESS

Current production remains v9 /opt/autotrader-releases/strategy_safety_v9_20261010T211941KST PID172366 active NRestarts0; v10 deploy unit not found (read-only verified 22:00+ KST). Never repeat v9 deployment.
V10 isolated candidate /private/tmp/chickenbanana-strategy-v10-20261010/candidate. Focused 37 regressions pass (green-review.xml). Review reproduced/fixed CORE held-timeout fabricated approval, local SL ratchet after AI reduction, skipped same-side held AI review, held Gemini prompt and UI context. Profit giveback requests dual-AI management; per-symbol single-flight/cooldown; actual unchanged stop bounds ADD risk. Partial reductions remain initial25%+25%, cumulative50%, durable lifecycle/bar/fill reconciliation, exact OCO resize in place.
CORE Gemini exit prices are mandatory: GPT approves same plan or confirmed entry timeout uses valid Gemini plan; wait/reject/missing/invalid plan fail closed. Chart chase conditions are AI evidence. Hard loss/fee/RR/precision/size/daily/protection checks stay. Candidate chart flag suppresses entry/exit AI calls. New CORE-only noise floor max(0.5ATR,0.2% price); existing20/30/60 margin return caps unchanged.
Independent reviewer findings corrected, including legacy owner requirement (deployment must assert CORE_UNIFIED_MODE=ROLLBACK and owner=legacy). All five full suites running in one sequential runner; record PID from Work tool session and inspect /evidence/full-results.json before starting another test. LIVE v10 deployment remains BLOCKED until full qualification. Existing positions/OCO untouched.

---

