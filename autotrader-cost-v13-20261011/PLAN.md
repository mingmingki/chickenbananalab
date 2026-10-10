# COST V13 implementation plan
Spec: ../autotrader-cost-v12-20261011/COST_REDUCTION_DESIGN.md (user approved implementation + deployment).
Task 1: offline RED tests for unified held response, fresh same-context reuse, changed/expired/missing-context fallback, duplicate evidence removal and compact prompt safety.
Task 2: implement core_ai_context.py, compact CORE prompts, Gemini unified schema and fresh response reuse in trader. Preserve GPT and all execution paths/intervals. Single current evidence block, one opposite-side entry contract while held plus actual SLTP; both contracts flat. Expected: focused tests pass, one Gemini request for adjacent valid held management.
Task 3: whole project suite + prompt size measurement + fresh final branch review. Expected: strategy/protection suite green; no paid AI test; measured token estimation explicitly distinct from billing.
Task 4: guarded stage/preflight, immutable before/after position/OCO evidence, single systemd dispatch, readonly verification. Expected: both LIVE loops, dashboard200, no duplicate deploy, exact preservation.
Global constraints: same Gemini/GPT models, urgent risk/profit checks and15m held reviews, Candidate C0AI, no test orders, no settings changes, no trade-veto changes.
Review focus: freshness clock measured from before provider request; changed MFE/reduction/protection/position invalidate reuse; malformed or incomplete merged data falls back; prompt precision SLTP/mark; close/reversal paths; extra context output cost; no status claims before evidence.
