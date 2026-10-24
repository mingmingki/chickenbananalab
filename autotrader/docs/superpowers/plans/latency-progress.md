Plan: 2026-10-23-dashboard-latency.md
Root causes reproduced by authenticated HTTP and py-spy.
Regression red observed 4 cases, initial green32 targeted.
Review: cross-key lock and middle-file invalidation findings reproduced RED; fixed with per-key single-flight and full-content digest.
Ruling: use isolated release tree because deployed project has no Git metadata.
Ruling: full tests limited to tests/ because bare pytest collects archived duplicate modules (52 collection errors).
Baseline tests/: 2494 passed,29 known failures.

Final: both review findings RED→GREEN; 34 related tests pass; full tests 2500 passed, same29 baseline failures. Canonical output identical. Deployment ownership issue corrected; PID756526 engines and 6 protections verified. HTTP15/15 verified.
