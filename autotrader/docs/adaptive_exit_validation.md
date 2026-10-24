# Adaptive Exit Engine Validation

Date: 2026-09-26 KST
Baseline: /opt/autotrader-releases/candidate_c_sizing_selector_20260925T2305KST
Implementation workspace: /home/bagmingi/autotrader-work/adaptive_exit_engine_20260926T033217
Policy SHA-256: 56b51abe0e907310d03fe0ed70f164124ed4050f397855237711e2ce556eef1a

## Safety state
- Production /opt/autotrader symlink was not changed during implementation or validation.
- autotrader.service remained active.
- ADAPTIVE_EXIT_MODE defaults to OFF in config.py.
- User .env has no ADAPTIVE_EXIT_MODE or approved-policy override, so adaptive execution is not live.
- LIVE_BOUNDED requires an exact operator-approved policy hash.
- Active stop proposals are monotonic: retain or tighten only; loosening is rejected.
- Gemini has no executable price, size, or leverage authority.
- SHADOW and ADVISORY preserve legacy Candidate C and CORE order arguments.

## Test evidence
- Adaptive + Candidate C focused suite: 143 passed, 0 failed.
- Untouched production baseline pytest -q tests: 483 passed, 21 failed, 27 subtests passed.
- Implementation workspace pytest -q tests: 537 passed, 21 failed, 27 subtests passed.
- Result: +54 passing tests and 0 new failures versus the untouched production baseline.
- The 21 failures are the same existing CORE medium-reversal, CORE profit-lock, legacy live-controller/emergency, and service tests reproduced on the untouched baseline.
- Python compile check passed for all adaptive modules and modified integration modules.
## 2026-09-23 DOGE tail-loss regression fixture
Observed legacy lifecycle identity:
- entry: 0.10403
- legacy notional: 2499.8409 USDT
- lifecycle Net: -162.6812 USDT
- configured fixed margin: 500 USDT at 5x

Adaptive replay at entry timestamp only:
- configured notional: 2500.00 USDT
- risk-capped effective notional: 307.4778325 USDT
- planned dollar loss: 30.0000000 USDT
- risk-cap violations: 0
- stop-loosening violations: 0
- decision: post_cost_rr_below_minimum, so the v1 counterfactual rejects this entry.

This fixture proves the risk-cap and no-lookahead mechanics for the known tail-loss case. It does not by itself prove improved future profitability; LIVE activation remains gated on broader shadow and forward evidence.

## Functional scope implemented
- versioned adaptive policy and deterministic policy/plan hashes
- structural ATR plus support/resistance stop geometry
- fixed-margin and variable-risk dollar-risk caps
- TP1 / TP2 / runner target plan
- cost-aware break-even and profit-lock primitives
- active-stop tighten-only invariant
- bounded Gemini assessment parser and overlay
- append-only duplicate-safe audit log
- lifecycle MAE/MFE no-lookahead features
- staged self-learning authority: SHADOW -> VALIDATED -> explicit LIVE_BOUNDED only
- Candidate C SHADOW/ADVISORY integration
- CORE SHADOW/ADVISORY integration
- dashboard/API advisory observability
- deterministic replay/report utilities
- Candidate C LIVE_BOUNDED entry geometry plus fixed-margin risk cap behind exact policy hash
- CORE LIVE_BOUNDED entry size/SL/TP behind exact policy hash
- CORE hard-risk precedence and anti-churn economic-benefit guard primitives

## Activation gate
This release candidate is safe to package with adaptive execution disabled.
Do not switch Candidate C or CORE to LIVE_BOUNDED without a separate operator decision using this exact policy hash and forward-shadow evidence.
Candidate C should be activated before CORE if and when approved.
