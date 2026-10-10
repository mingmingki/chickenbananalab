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
