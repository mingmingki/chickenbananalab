# Dashboard latency repair implementation plan

Goal: restore realized PnL and Candidate C dashboard refresh without changing order or risk behavior.
Evidence: API response 8.141s PnL, 7.587s state, 5.311s Candidate C; browser PnL aborts at 8s.
Profile: repeated Path.resolve per eligible close; repeated 47MB epoch replay in UI and observation-only analytics.
Constraints: keep canonical PnL corrections, strict epoch validation, current settings, CORE/Candidate C running modes and all native protections.

- [ ] Red: repeated account path resolution; identical file polling; mutation isolation; replacement/deletion; concurrent pollers; changed-during-read.
- [ ] Move canonical directory resolution into the existing per-process cached manifest loader.
- [ ] Add a bounded file-signature cache for read-only projections. Never reuse a projection after file identity changes; never share mutable results.
- [ ] Use it only for dashboard epoch loading and observational active-epoch analytics.
- [ ] Show explicit realized PnL timeout status while retaining last successful figures.
- [ ] Run targeted and full regression tests; compare all canonical summaries on the same live journals.
- [ ] Guarded preflight, preserve configuration/protection, deploy and confirm both engines resumed.
- [ ] Repeat authenticated API timings and check actual rendered dashboard.
