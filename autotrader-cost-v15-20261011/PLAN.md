# CORE Gemini management and GPT entry-only Implementation Plan

> Execute inline with superpowers:executing-plans; one fresh whole-branch reviewer before deployment.

Goal: implement the user-approved role split and deploy once while preserving live positions/OCO.
Spec: user-approved role table and autotrader-cost-v14-20261011/RESULT.md.
Architecture: opt-in CORE_GEMINI_MANAGEMENT_ONLY; Gemini explicit management_action; pure core_management_policy determines bounded reduction fraction from observed risk; GPT entry gate retains new/add/reversal exposure approval; execution safeguards unchanged.
Global constraints: Candidate C chart-only/AI0, fixed entry margin unchanged, cumulative partial cap initial50%, position/OCO preservation, no forced paid calls/test orders, typed entry timeout policy unchanged.

Files: config.py (flag), core_management_policy.py (validation/code sizing), gemini_analyzer.py (schema/prompt/actions), core_ai_context.py (snapshot validation), trader.py (Gemini management handler and GPT add-entry approval), web_app.py+templates/dashboard.html (effective settings and retired paid pattern review UI).

Task1: validated Gemini actions + pure deterministic sizing.
- RED tests malformed action/confidence/risk, stronger observed loss/giveback->different fraction, both directions, unknown geometry no executable REDUCE.
- Implement enabled(cfg), valid_review(cfg,review), decision(cfg,review,position,protection,rows,evidence) returning action/confidence/reasoning/risk_level/reduce_fraction/code_fraction_policy.
- No confidence-only sizing; min .05 max .50, existing cumulative cap stays execution responsibility.
- Extend both primary nested and standalone held schemas; retain old schema for flag false; reuse fingerprint includes flag.
- GREEN focused tests; checkpoint.

Task2: route Gemini management and GPT only added exposure.
- RED handler tests HOLD/REDUCE/CLOSE no GPT call; ADD calls existing entry gate exactly once; wait/reject/error no add, typed timeout still existing policy.
- Add exposure candidate uses same side and actual current SL/TP, validates approval and unchanged position/protection before add.
- Require new executor add approval proof (approved or typed timeout) when enabled; preserve legacy mode behavior.
- Reduction executor accepts validated Gemini+code approval; preserve freshness, lifecycle, closed-bar, pending/dedup/cumulative/partial-fill/OCO checks.
- Gemini errors retain protection and do not call GPT management.
- Keep urgent paths and ordinary review cooldown unchanged.
- GREEN integration and full five isolated test groups; checkpoint.

Task3: UI and free trade stats.
- Expose effective management mode; labels Gemini/code sizing + GPT entry-only; remove misleading automatic6h wording.
- Paid manual strategy review endpoint disabled in new mode and UI paid button removed; transaction/history/code statistics retained.
- Full five groups and one independent readonly reviewer; fix important issues with RED/GREEN; checkpoint.

Task4: qualify and deploy.
- Manifest all v14 verified files plus new policy; matching full-source hashes, exact old release guard, one new config flag.
- Stage/preflight readonly; fresh snapshots and no pending general orders. Dispatch v15 unit once; no retry without reading record/unit.
- Verify source/settings, both runtimes, exact before/after positions/OCO, disabled paid patterns, effective Gemini-only management and GPT entry/add gate.
- Observe natural paid routes without forcing orders; report measured data limits, persist checkpoint and push.

Review focus: arbitrary JSON arrays/types; ADD response masquerading as reduction; protection changed during GPT; past/future timestamps; pending partial fills/restarts.
Ruling: existing isolated publication worktree and prior deployment authorization reused; no redundant approval for this explicit implement/deploy/verify instruction. Preserve all checkpoints and evidence rather than delete recoverable workspace.
