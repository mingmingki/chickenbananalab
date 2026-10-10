# 2026-10-10 integrated audit — NOT DEPLOYED

Production observed: core_gemini_gpt_sltp_v7_20261010, PID162715, active, NRestarts0.
Direct read-only OKX audit: six nonzero positions, six valid reduceOnly OCOs with exact contract coverage; pending regular orders0.
Source mirror and private runtime evidence are in /private/tmp/chickenbanana-integrated-audit-20261010 and /tmp/chickenbanana-audit-20261010 on VM. Do not publish runtime account data.
Two report bugs fixed in isolated candidate: v6-only release identity recognition and unrelated Oct6 fallback cohort date.
RED4fail2pass; relatedGREEN19pass. Complete corrected source export:
baseline1192pass78fail27subtests; candidate1198pass78fail27subtests. Failure IDs identical; no new failures.
Original source export omitted one gz fixture (22 failures). It was restored from actual production and the corrected baseline rerun. First incomplete100failure result is not the qualified baseline.
Existing API fixture now supplies exact current deployment identity; every assertion retained.
Do not deploy, restart services, alter settings, open/close positions, amend/cancel protection, or promote a strategy while full regression qualification remains unresolved.
78 failures across11modules require contract investigation:42entry pipeline,10dual AI consensus,8wide-stop regression,4final-entry submission,3report contract,3wide-stop sizing,2TP2 AI rescue,2GPT dashboard,2AI budget,1event budget,1fixed sizing.
Current source changed since oldv5/v6 artifacts. Never replace it with main/CAD or an older candidate. Match hashes in manifest; reuse evidence only if source and dependencies match.
NaturalLIVEcases: ADA14:57 dual-approval protectedfill; BTC15:06TIMEOUT_BYPASS protectedfill; ADA16:43dual approval blocked before reversal close byfinal_target_r_bounds_unrepresentable.
No extra Codex CLI, AI paid calls, test trade, Telegram test, GCP restart, or order mutation was used in this audit.
Next: verify missing test DTO/proof fields versus actual production contract without weakening risk/protection assertions; classify changed SL/TP expectations versus genuine defects; reproduce finalADA bounds failure using saved exactdecision inputs. Then cost-inclusive causal replay and full suites, only then immutable deployment with exact currentidentity marker plus preservation snapshot/rollback.

Final fixture-only qualification update:
Fake exchange implements price_to_precision with fixture price ticks; active CORE entry fixtures use ADA instead of PI; raw GPT log fixtures include actual CORE symbol. Historical trades unchanged. No existing assertion removed or relaxed.
Final full candidate:1255passed21failed27subtests.59 original failing IDs pass;19 original failures persist;2 favorable-quote target tests newly fail because valid ADA now reaches submission rather than unsupported-PI veto. This is newly exposed behavior of unchanged trading code, not a green qualification. Deployment blocked.
Remaining discrepancies include tighter20percent leveraged SL cap, final-price TP normalization, unchanged fixed-margin risk budget, and600second routine AI coalescing. Do not merely change expectations to get green; replay exact approved inputs and verify cap/ATR/RR/quantity/protection invariants.

## Resume 2026-10-10 18:18 KST — phase 1 read-only
- Publication HEAD 1382de6; git worktree clean. Candidate remains /private/tmp/chickenbanana-integrated-audit-20261010/candidate; do not use publication root as trading source.
- DC active sessions none before resume; no pytest/gcloud/ssh task running. Existing Codex app daemons left untouched.
- Read-only VM check 18:14 KST: autotrader.service active/running, PID162715, NRestarts0, symlink core_gemini_gpt_sltp_v7_20261010. No restart/deploy/order changes performed.
- Candidate and VM hashes match: trader.py 85112984652741a3aeb74f5ec4a74e38f17d77129d84f711b42a29edd68c7c81; core_entry_sltp_repair.py 8dadc188859c3efae299faedbc567aa2ae833d461d69b0f829d98edf4833ef6c; entry_execution_normalization.py b8ed4939dd389d485581bb1805be0cee279e0fd26e4e884458731951dee96a8f.
- Existing final-candidate-full.xml reused; 21 failures identified. Three AI tests expect obsolete immediate low-importance calls; most stop tests expect pre-v7 wide-stop geometry. Four final submission tests require independent final quote/quantity/normalization investigation. Do not weaken cap/ATR/cost/RR/exposure/protection assertions.
- Next: reproduce isolated contracts with existing Python venv, retrieve saved exact ADA decision inputs read-only, add regression before any genuine code fix. LIVE deployment still blocked.

## Resume phase 2a — genuine execution regressions
- Existing correct runtime /Users/bagmingi/chickenbanana-work/chickenbananalab/.venv-autotrader/bin/python reused (Python3.11); unrelated project .venv and global Python have no pytest. No dependencies installed. Targeted unchanged suite reproduced exactly21failed126passed (resume-red-contracts.xml/log).
- Read-only saved SQLite event ADA/USDT:USDT-20261010164321221141: short approved Gemini.76/GPT.72; reviewed .2527/final .2524; SL .26275746; TP1 equals final quote .2524; TP2 .222376. No reversal close occurred. Native final TP1 zero distance caused final_target_r_bounds_unrepresentable despite feasible one-tick profitable price inside approval.
- Added5regressions first: exact ADA and mirrored long, native final stop cap both sides, invalid reconciliation never retaining entry authority. RED5failed, then minimal candidate-only fixes GREEN11passed including existing v7repair tests.
- entry_execution_normalization.py: native final stop can only tighten to same20%leveraged cap within original approval drift; native noise floor preserved; native TP1 minimum one tick instead of zero, still bounded by cap and approval distance. Verified AI ATR/R rules unchanged.
- core_entry_sltp_repair.py: rejected reconciliation now explicitly returns entry_allowed=False with updated hash, even if incoming plan carried True. Previously invalid allowed target retained authority.
- Evidence in /private/tmp/chickenbanana-integrated-audit-20261010/evidence/resume-{red,green}-execution.*. Source edits only in isolated candidate. Production unchanged; deployment blocked pending full regression, cost-inclusive causal replay and preservation checks.

## Resume phase 2b — policy-specific test qualification
- Routine Gemini event tests now prove .5ATR/RSI-only changes coalesce at5minutes and call at10; >=1ATR, EMA/structure/correction/lifecycle events remain immediate. No production AI gate changes. GREEN30passed (resume-green-budget.xml).
- Wide-stop tests now verify the actual v7 20%leveraged cap (3.98price distance at100/5x), exact cost-inclusive modeled loss and TP2 netRR>=1.1. Quantity-reduction fixtures use equity500/budget25 so size reduction remains genuinely exercised. Verified Gemini/GPT price propagation and audit equality assertions retained. High-cost parameter .02 proves smaller quantity; .06 demonstrates mathematically infeasible netRR and fail-closed. Volatile ATR10 proves native noise floor cannot fit within unchanged stop cap. No risk budget policy/settings changes. GREEN84passed (resume-green-risk.xml).
- Next final boundary tests: approved budget tied to original modeled loss to exercise real quote-driven quantity reduction; distinguish invalid AI target at approval (must reject) from valid approved target normalized after favorable quote (bounded/capped). Then full regression and replay. Deployment remains blocked.

## Resume phase 3 — qualification passed, not deployed yet
- Primary final suite1292passed27subtests; gemini_checks230passed38subtests; rollback_checks37passed12subtests; negative_guard_checks41passed7subtests; rollback_full_checks257passed46subtests. Total1857passed130subtests, no failures.
- Bare all-directory pytest collection collides across duplicate historic test basenames (52collection errors, then4missing sibling-helper imports under importlib). No tests deleted/ignored to hide failures: each of5complete suites ran in separate process with its own PYTHONPATH and importlib, all green. Original suite comparison is tests/ (old1276tests, now1292).
- Dedicated profit retention/partial TP/causal historical replay/dual approval/entry/protection/idempotency checks:215passed. Generic adaptive replay is conservative modeled-risk evidence, NOT an intrabar profit proof; no profitability guarantee or new strategy promotion claimed.
- Independent read-only review found no important/critical delta defects. Seven additional edge regressions prove excessive normalization drift rejection, coarse tick/noise infeasibility, reproducible rejection hash. All12new execution tests pass.
- Direct read-only exchange check:6positions (BTC/ADA/ETH/XRP/SOL/DOGE),6live reduceOnly OCOs with exact size and SL+TP,0conditional,0pending regular orders,6fully protected. Service remainsPID162715 active/restarts0; no order changes performed.
- Next: prepare immutable release using existing reviewed V19 deploy operator adapted for exact currentv7hashes, record exact new deployment identity, verify current activation/parity and protected positions before/after switch. Preserve shared paths/settings and old-release rollback. Do not run parallel deploy operators.
