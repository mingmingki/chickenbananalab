from pathlib import Path
import json,datetime,hashlib
repo=Path('/Users/bagmingi/chickenbanana-work/chickenbananalab');root=Path('/private/tmp/autotrader-gpt-recovery-final-20261008');dest=repo/'autotrader-gpt-final-20261008';e=root/'evidence'
d=json.loads((e/'health-final.json').read_text());cost=d['costs'];daily=d['daily']['daily_completion'];deploy=json.loads((e/'deployment.json').read_text());tg=json.loads((e/'telegram-confirmation.json').read_text())
report=f'''# CORE GPT recovery: deployed and verified

2026-10-08 KST. This report supersedes the old Oct5-mixed candidate's unqualified status. That candidate and the immutable v4 production preservation remain intact. No CAD/root main edits, credential publication, forced trade, or test position close occurred.

| Request | Implementation | LIVE / actual verification |
|---|---|---|
| CORE entry policy | Valid Gemini LONG/SHORT + approve_now or typed entry APITimeoutError proceeds through all final local guards. Raw TIMEOUT is distinct from TIMEOUT_BYPASS; original Gemini/local exits remain authoritative on timeout. wait/reject/hold and non-timeout errors block. | Actual account is ROLLBACK/LIVE, all four ownership rows legacy. Applied to its trader path. Unified entry behavior was not changed or claimed qualified. Loaded settings gate=true, timeout bypass=true, paid Shadow=false. |
| Entry/order recovery | Decision IDs, coin/contracts, durable client ID, complete original-fill proof, immutable lifecycle binding, restart reconciliation, atomic terminal receipt/event, idempotent OPEN, residual cancel and bounded original-position safety recovery. Reversals qualify before existing-position handling. | Existing BTC/ETH/DOGE/SOL positions and protective OCO IDs/size/SL/TP unchanged. Pending regular orders=0. New natural entry/fill not observed; no forced orders. |
| Telegram/UI | All GPT results and subsequent local/submitted/pending/filled/failed states share decision IDs; asynchronous durable outbox with bounded timeout/confirmed retry and uncertain-delivery preservation. UI separates approval from fill; original revised-plan values persist. | Existing receiver confirmation exactly once: ok=true, message_id={tg['message_id']}, outbox SENT=1. Authenticated state/events/dashboard/assets200; journal/API notification consistency confirmed for this confirmation. Natural trade notification chain not yet observed. |
| Paid calls/costs | Additional paid Shadow default OFF, account opt-in. Required initial signal/held-risk timing functions AST-identical to v4. Metadata stores model/tokens/purpose/identity/input hash/retry ceiling; missing actual retry data remains unknown. No paid call added for verification or message text. | Provider tokens/purposes aggregated; new model metadata accumulating. Savings and profitability are unmeasured. |
| Verification | Fresh candidate suites all pass; reviewer Ready. No removed tests or weaker safety assertions. Old fixtures now invoke actual helpers and provide actual exchange/lifecycle proof. | 446 Python sources compile, affected modules import offline; Candidate C live/backtest shared source parity=true locally and remotely. CORE strategy hashes unchanged; candidate synthetic entry/exit boundary verified. This does not assert natural market replay profitability. |
| Deployment | Private previous-release/account/systemd/env backup, exact v4 per-file baseline checks, 33-file qualified overlay, shared symlinks retained, atomic release switch, existing approved resume mechanism. Rollback script retains the directly verified previous v4. | {deploy['release']}, PID{deploy['pid']}, active/NRestarts0; running CORE and C, verified loaded settings, confirmed-bar heartbeat, other accounts/user settings and shared paths unchanged, no new logged ERROR/CRITICAL/Traceback during observation. |
| Commit/push | Isolated existing infra branch; source/evidence only. | Publication status is recorded separately after actual native git push verification. |

## Fresh final candidate verification

| Suite | Passed | Subtests | Exit |
|---|---:|---:|---:|
| tests |1264|27|0|
| gemini_checks |230|38|0|
| negative_guard_checks |41|7|0|
| rollback_checks |37|12|0|
| rollback_full_checks |257|46|0|

Total 1,829 tests plus 130 subtests across independent suite processes. Fresh review focused verification147 passed. These are candidate results, not the 1,128 baseline or earlier1251 result. Network isolation is test-process-only; native gcloud/git/gh were authenticated and exercised independently. Regression failures, RED reproductions and every final XML/log are preserved under evidence/. `qualification.json` records imports/compile/parity/source/timing preservation. Source manifest SHA and changed-files baseline/candidate SHA retain exact provenance; strategy pin hashes were not rewritten to force passing.

## Historical incidents

Read-only original service journal, decision pipeline, trade journal and paginated OKX orders/fills/protection histories are in history/. `correlation.json` is snapshot-scoped to 2026-10-08T09:08:09.817UTC, not a current account statement.

- ETH09:10: GPT approve_now then REVERSAL_ENTRY_BLOCK before_close=entry_late_exhaustion_no_pullback; kept existing LONG. Normal local pursuit/exhaustion guard, no exchange rejection. A synthetic receipt bug is not asserted as this incident's cause.
- BTC09:43: confirmed entry GPT timeout25095ms; old policy failed closed before submit. Candidate changes precisely typed entry timeout handling; other API errors remain blocked.
- PI: post_cost_rr_below_minimum in original ADAPTIVE_EXIT LIVE_BOUNDED. Cost/risk rejection preserved.
- Recent approved-but-blocked cases include final_tp2_above_leverage_cap (BTC11:12/15:01/17:02/17:16, ETH12:35/16:56, XRP17:22) and entry_late_exhaustion_no_pullback (BTC14:26, ETH17:02). Successful approvals (BTC09:20/15:30/17:36, ETH11:12/13:30) link to existing fills/OPEN.
- Historical snapshot:50 GPT gate rows (15approved/8reject/14wait/13error), approved success5/block10;31 filled regular orders/non-reduce13;13 journalOPEN;129 fill rows; fill/journal/protection mismatches0 at that snapshot. No claim of completeness beyond that snapshot/pagination scope.

## Deployed identity and preservation

- Release: `{deploy['release']}`
- trader.py SHA256: `{deploy['trader_sha256']}`
- systemd PID `{deploy['pid']}`, ExecStart `/opt/autotrader/.venv/bin/python3 /opt/autotrader/web_app.py`, WorkingDirectory `/opt/autotrader`; /proc cwd resolves to this release.
- Private backup: `/var/lib/autotrader/deployment-backups/core-gpt-recovery-20261008` root700, archive/account settings600. Private data is not copied to this publication.
- Existing source/settings rechecked before deployment; source hash checks before overlay and after LIVE deployment. Other OFF accounts remain untouched; C DOGE/SOL stays LIVE. Baseline v4 immutable source preserved unchanged.
- CORE first and next closed-bar cycles observed; C last_closed_bar advanced1791462900000→1791463200000, heartbeat current. Service active/NRestarts0, all authenticated APIs/UI/static verified200. Historical stale worker files were not used as LIVE heartbeat evidence.
- BTC short1.21, ETH short3.92, DOGE short1.44 and SOL short1.61 contracts; their position/trade/lifecycle and OCO snapshots unchanged through final read-only check. No trial close/cancel/entry was made. Emergency recovery writes occur only in ordinary validated source policy, never through this audit.
- Connection confirmation is a durable named event; repeating the tool sees its SENT result and does not resend. Event/API acknowledgement message935 agrees. No token/chat ID published.

## Measured calls and costs

Snapshot `{cost['generated_at']}`. Token usage logs and recorded provider-rate estimates, **not invoices**. Rolling24h: Gemini{cost['by_provider_24h']['gemini']['calls']}calls/${cost['by_provider_24h']['gemini']['cost_usd']:.6f}, OpenAI{cost['by_provider_24h']['openai']['calls']}calls/${cost['by_provider_24h']['openai']['cost_usd']:.6f}; total${cost['rolling_24h_cost_usd']:.6f}. Today${cost['today_cost_usd']:.6f}. Month-to-date${cost['month_to_date_cost_usd']:.6f}.

Monthly projection AI${cost['projected_monthly_ai_cost_usd']:.2f}, server${cost['server']['monthly_estimate_usd']:.2f}, total${cost['projected_monthly_total_usd']:.2f}. AI uses24h×730/24. Server is e2-medium/20GBpd-standard/oneexternalIPv4, compute+disk+IP estimated; tax/network/logging/discounts/credits excluded. Current paid billing was not queried or represented as these estimates.

Purpose/model/token and duplicate/retry details are retained in health-final.json. {cost['call_metadata_24h']['logged_calls']} logged successful-token responses; duplicate request records0. Historical{cost['call_metadata_24h']['request_identity_unknown_calls']} request identities unknown; actual retries unknown on{cost['call_metadata_24h']['unknown_retry_calls']} rows. Zero measured retries is not proof of zero retries. Current model label gemini-3.8-flash4 logged calls; older model metadata unknown. Entry metadata uses actual response/request IDs when available. No measured saving, improved win rate or learned-policy profit claim.

## Daily review and remaining observational limits

The existing daily endpoint remains read-only/Shadow, `live_authority=false`. Live learning enable=false. Snapshot: entry150 samples/resolved150 (late0, immediate adverse8); exit97 samples/resolved90 (CLOSE winner43/HOLD45/REDUCE500). These are accumulated analysis, not a newly validated change or equal current-release-only sample. Current telemetry35/37 (94.59%); CORE24/24, C11/13. Full historical feature coverage35/607 remains separate. Patterns70, promotion eligible0, blocked70; performance improvement is unverified.

`DAILY_STATUS.md` separates the observable existing numbered daily items from unidentified historical item definitions. Original daily11 exact requirement text is absent from the supplied preservation/resume prompt; user-message recovery searches did not retrieve its complete original list. Items whose definition could not be verified are explicitly unassessed, not claimed implemented/LIVE-complete. This bookkeeping limitation does not alter the deployed GPT recovery policy or turn observation into autonomous learning.

Remaining: natural qualified entry/fill/whole trade notification chain, long-term recovery observations, verified savings, actual provider/GCP invoices, learning promotion and profitability. No missing live trade was created to satisfy those observations. Native deployment health passed; rollback not needed.
'''
(dest/'REPORT.md').write_text(report)
(dest/'VALIDATION.md').write_text('Final fresh verification evidence is evidence/final-suite-results.json, final-*.xml/log, qualification.json and CODE_REVIEW.md. Tests1829+130subtests; review147. Deployment and live health are deployment.json, post-server.txt, health-first/final.json, deployed-source-verification.json, pre/post/final-exchange.json and telegram-confirmation.json. No paid AI/exchange order/close occurred in verification. Production baseline and original failed candidate are separate and not credited as candidate passing results.\n')
(dest/'DAILY_STATUS.md').write_text('''# Existing daily status, not a learning/profit claim

Existing UI numbering is observable only for1·2,3,4,6; the original complete11-item spec was not recovered. Unknown item definitions are intentionally unassessed.

| Original number | Verified existing definition | Implementation | LIVE/evidence |
|---|---|---|---|
|1|Late-entry analysis|Read-only analysis exists|150samples,late0; current CORE freshness guard separately LIVE|
|2|Immediate-adverse/follow-through entry analysis|Read-only analysis exists|8immediate-adverse,6clean-follow-through; unchanged early setup timing verified, no proven performance improvement|
|3|Profit preservation CLOSE/REDUCE50/HOLD comparison|Shadow analysis exists|97samples/90resolved; outcome counterfactuals do not grant new order authority|
|4|Integrated context/data quality|Read-only telemetry/API/UI exists|Current35/37 complete; full history35/607 separate|
|5|Original requirement definition unavailable|Unassessed|No completion/LIVE claim|
|6|Self-learning blockers/promotion explanation|Observation exists|live_enabled=false,70blocked,0eligible; no validated promoted learning|
|7|Original requirement definition unavailable|Unassessed|No completion/LIVE claim|
|8|Original requirement definition unavailable|Unassessed|No completion/LIVE claim|
|9|Original requirement definition unavailable|Unassessed|No completion/LIVE claim|
|10|Original requirement definition unavailable|Unassessed|No completion/LIVE claim|
|11|Original requirement definition unavailable|Unassessed|No completion/LIVE claim|

Costs/API/metadata/UI and CORE/C status are verified separately in REPORT.md; they are not assigned invented historical daily requirement numbers. Natural fills and profitability/savings were not forced or inferred from accumulated data.
''')
(dest/'PUBLICATION_STATUS.md').write_text('Final candidate deployed and verified. Native Git publication pending at this document creation; actual commit/push verification will replace this status after push. Isolated branch infra/autotrader-core-gpt-recovery-20261008. Root main/CAD/index untouched.\n')
(dest/'RESUME.md').write_text('Implementation + all5freshcandidate suites1829+130subtests + independent147review Ready complete. LIVErelease '+deploy['release']+' PID'+deploy['pid']+' SHA'+deploy['trader_sha256']+'. CORElegacyROLLBACK/LIVE and CtwoLIVEloops resumed; flags actual APItrue/true/false; after7min nextCOREcycle and Cnewclosedbar verified,0newerrors/NRestarts0. pre/finalposition/protectionidentical,pending0. TelegramoneconfirmationSENTmessage935. Backup/rollback remoteprivatepath inREPORT. Commit/pushpending. Source/evidence preserved inthisfolder; tools preserve.py rebuilds whitelist fromactualproductionmanifest. Remaining observational limits and dailyoriginalnumbering definition gap inREPORT/DAILY_STATUS. production/CADunmodified.\n')
print('reports persisted')
