# CORE Gemini compact review release

Keeps the saved 300-second review, Gemini-only mode, risk loop and order execution guards.
Only the model-facing projection is condensed; full closed-candle snapshots and order ledgers stay intact.
Market context includes six timeframes, EMA20/50, RSI change, recent price/MACD paths, 20-close change/range, ATR and volume ratio.
Position context retains side, size, initial entry/risk, stop/target, best price, age and reduction state.
Derived movement is explicitly relative to original entry and latest closed minute, not current average or net PnL.
Identical setting/key saves no longer change the settings revision or restart the review clock.
Real changes still invalidate old approvals.

Validation: prompt size and long/short risk context regressions; low-price precision and unknown values; real settings/Coordinator scheduling regression. Server prepare runs the complete gemini_checks suite before release activation.
