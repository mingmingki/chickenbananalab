# V16 strategy guide LIVE / verified
Completed 2026-10-11 10:46:44 KST. Release /opt/autotrader-releases/strategy_guide_v16_20261011T104312KST, PID 192265, restarts0.
Footer 현재 전략과 운영 방법; navigation 전략 · 운영 안내. CORE/Gemini-held/GPT-entry and Candidate C/chart-only roles, entry/SLTP/partial/add/sizing, operating steps,cost roles and recent changes.
Numeric settings and code reduction bounds refresh through existing state polling. Future behavior changes must update explanations/revision in the same release; see MAINTENANCE.md. No extra AI/provider request for guide.
4 read-only UI source changes,21 manifest hashes verified,settings0.
Final regression2040 tests+130 subtests pass,7 feature tests pass; independent review no blockers,desktop/narrow screenshots verified.
Single systemd unit autotrader-ai-cost-v16-deploy-20261011.service success/exit0. Never redispatch.
LIVE dashboard/state/asset HTTP200; CORE/Candidate running;6positions/6OCO exact before/after/current preservation;pending0.
Initial verifier ran before asynchronous startup completed and saw running=false. Journal confirmed CORE start10:44:23/Candidate10:44:25. No retry/restart/deploy action; repeat read-only verification passed. No production defect/change required.
Resume: inspect existing unit and /tmp/autotrader-ai-cost-v16-qualified-20261011/deployment-record.json. Verify script available in stage. Preserve orders/positions.
