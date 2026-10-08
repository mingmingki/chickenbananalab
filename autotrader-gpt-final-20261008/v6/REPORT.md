# Entry / cost V6 — candidate qualified, 운영 반영 전

Baseline `8e510d77a166f57c4584afbbdefd05e3b4e06be2`, branch `infra/autotrader-entry-cost-v6-20261009`. 기존 V5 REPORT와 과거 검증 증거를 보존했다. 이 보고서는 로컬 구현/검증 결과다. 운영 배포, commit, push, main merge를 수행하지 않았다. V6_DEPLOY_OPERATOR.py는 주 작업자 소유이며 수정하지 않았다.

## 수정과 승인 경계

기존 절대 AI 가격 고정은 GPT 승인 후 임의 전략 변경을 막으려는 계약이었다. 이 목적을 유지하면서 기존 0.2% 승인가격 드리프트 안에서만 실행 호가와 정책을 맞춘다. 앵커는 최초 승인 가격/SL/TP/수량/예산으로 고정한다. 작은 가격 변경의 재검증 때 앵커를 교체하지 않는다. SL 확대, 수량/예산 증가, 큰 TP 변경은 허용하지 않는다.

PI 2026-10-09 00:02 LONG: 승인 .08268 / 실행 .08259 / 원 TP2 .0926016의 최종 leverage gain 60.61024337% 초과를 재현했다. 거래소 tick .00001 기준 TP2 .09250으로 정규화해 60% 이내, SL 보호 방향·TP 순서·비용후 RR·수량/예산/최소수량을 다시 검증한다. SHORT 대칭, precision serializer 불일치, freshness/추격/일일손실, 큰 가격 변경 거절을 유지한다. verified AI 계획에는 호가 반영 후 TP1/TP2 R min/max와 SL ATR 및 leverage 상한도 적용한다. 기존 구조 SL risk-sizing fallback은 넓은 SL을 별도 위험예산으로 승인하는 경로이므로 verified AI ATR/SL cap을 일괄 강제하지 않는다. 이 구분은 정책 확대가 아니라 기존 허용 근거의 보존이다.

Gemini 적격 + GPT approve_now, 또는 기존 typed entry APITimeoutError + timeout_confirmed + bypass 설정만 후보가 된다. wait/reject/기타 오류 차단을 유지했다. 정책 상한 삭제, 새 전략/온라인학습/위험한도/DCA 확대는 없다.

## 독립리뷰 Important 4건

| 사건 | 수정 | 실제 검증 |
|---|---|---|
| 최종 R/ATR 검증 누락 | 최초 승인 앵커, Decimal 호가, R/ATR/stop cap의 가능한 교집합만 보정, 불가능하면 정확한 사유 차단 | 양방향 .1% drift의 과도한 R 변경 차단, .01% drift 보정, ATR 최소 widening 금지, stop cap 회귀 |
| 주문과 감사/계획 불일치 | 최종 수량/가격/SL/TP/전체 plan/hash를 decision context·receipt·영구 adaptive audit에 공통 반영; ai_exit_plan_audit도 그 값을 사용 | 실제 _handle_new_entry→_execute_entry→receipt/audit/outbox hash 일치, reversal pre-close/post-close/pre-submit의 계획 전달 |
| C 실제 setup rejection 알림 누락 | direction/ATR·stale 거절 전 entry_attempt 표시, setup 정보의 cycle result 전달, 공통 durable outbox 연결 | native DOGE whole-cycle 위험/방향/30분 경과 차단, 같은 event 재처리 1회, 같은 setup 다른 사유 2회, Shadow 조용 |
| CORE adaptive 차단 계산/표시 오류 | adaptive context/plan의 실제 OCO TP2로 비용포함 RR·SL/TP·qty·margin·budget·bounds 계산; Telegram 요약행을 truncation 전 표시 | 실제 run_cycle adaptive block의 persisted event와 Telegram 가격/RR/기준 일치 |

리뷰 수정 전 정확한 candidate cwd RED: `evidence/red-review-important-fixed-fixtures.log` 10 실패. 강화된 GREEN은 `green-review-important-extended.log` 21 통과 및 최종 handoff 전체 suite. 초기 fixture 오류 출력도 삭제하지 않고 구분해 보존했다.

## 알림 / 비용 / 이익관리

CORE pre-GPT 기록 전용 차단 13개 callsite가 cfg/decision/id/계산 증거를 통해 _set_core_entry_outcome과 outbox로 연결된다. C 실제 EntryIntent 및 setup-bearing NoAction 차단도 연결된다. 정상 no_setup/held/hold를 진입 시도로 취급하지 않는다. 서로 다른 사유를 throttle로 숨기지 않는다. 엔진·종목·방향·KST·decision/setup·Gemini/GPT 결과/확신도·단계/사유·계산값/기준·수량/증거금/감소사유·가격/SL/TP를 표시한다. 아직 계산하지 않은 수량·GPT·가격은 UNKNOWN으로 유지한다.

주문 판단 스레드에서는 Telegram HTTP를 호출하지 않는다. 기존 worker/outbox 계약을 보존하고 CORE/C 시작/평범한 cycle에서도 pending worker를 시작한다. 전송 timeout/전송중 프로세스 종료/identity 없는 응답은 DELIVERY_UNKNOWN으로 재전송하지 않는다. 확정 429만 5초 이내 1회 재시도한다. NaN/Infinity 진단값은 unknown으로 저장하여 실제 차단 event 자체가 사라지지 않게 했다. 주문 복구는 원 receipt를 새 가격/새 계획으로 바꾸지 않으며, 원 client ID/최초 fill proof/position lifecycle/OCO 검증을 유지한다.

AI 저중요도 price/signature 변경만 마지막 성공 호출부터 10분 coalesce한다. 새 early setup, 실제 포지션 변화, EMA 방향/구조 붕괴/위험 맥락, 1ATR 이상 가격 이동, 중요한 외부 event는 즉시 재검토한다. stable held/flat fallback 30분과 5분 확정봉 deterministic risk/negative/profit guard는 유지한다. 성공 memory와 접수 pending을 분리하여 변동이 원복되거나 호출이 실패해도 필요한 변화가 소실되지 않는다. market event_key가 crypto를 이미 제외한다는 기존 사실을 유지하며 매 시세마다 바뀐다고 가정하지 않았다.

성공 token log에 목적/engine/trigger/실제 모델/캐시 입력 토큰을 기록하고, 별도 application-attempt log에서 timeout/오류/설정 retry ceiling을 기록한다. SDK 내부 실제 재시도나 실패 호출 비용을 모르면 unknown이다. 캐시 토큰만으로 legacy 요율 추정 비용을 임의 차감하지 않는다. 운영 비용 API/UI에서 trigger/cache/timeout과 rolling24h 중 배포 이전/이후 및 배포 이후 누적 비용을 분리한다. ENTRY_COST_V6_DEPLOYMENT.json의 실제 created_at 또는 operator가 만드는 entry_cost_v6_YYYYMMDDThhmmssKST 릴리스 경로를 사용한다. 로컬 빌드 시각을 배포 시각으로 쓰지 않는다. 배포 경로 시각은 서비스 전환보다 준비 시간이 조금 앞설 수 있다. 청구서 요율·50% 절감·수익 개선은 미측정이다.

부분익절의 감축 자체가 거래소 최소수량보다 작은 코드 오류를 RED→GREEN로 수정했다. 잔여 최소수량만 확인하던 경로가 illegal partial order를 만들지 않고 다음 profit-lock 판단으로 계속된다. structure/MFE 감축과 실제 관리 주문 persist 전에도 최소수량을 확인한다. 기존 CORE/C partial/MFE giveback/profit-lock/structure/negative/close-escalation, 비용 포함 보호 floor, 이미 감축한 수량과 별도 lifecycle flags, 중복/restart/신선도 테스트를 유지했다. 실제 SOL 23:15 자동 부분감축/SL lock은 기존 체결 근거이고, 01:52 manual 전량청산을 자동 기능 완성 증거로 쓰지 않았다.

C 상태/API/UI에 observed contracts × contract size × entry price ÷ leverage의 예상 margin과 설정 상한을 분리했다. 최근 진입의 pilot/risk-cap/equity/lot 감소 근거를 별도로 표시한다. 실제 binding 계산값으로 감소 사유를 결정하며 감액이 없는 경우 빈 사유로 남긴다. 이 표시 오류도 RED→GREEN로 수정했다. 기존 historical position의 주문 당시 근거가 없으면 unavailable이다. 제공된 DOGE25.30/SOL36.93과 설정150의 차이는 short pilot50% 및 넓은 SL risk-cap 근거이며 150 강제를 위해 보호 한도를 삭제하지 않았다. 온라인 학습 live_enabled=false / eligible0은 검증 미완료로 유지하고 데이터 누적과 분석 스냅샷 지연을 따로 표시한다.

## 검증 출력

네트워크 test-only sitecustomize가 socket connect/connect_ex/sendto/create_connection을 차단한다. 실주문/실 AI/Telegram 대신 realistic boundary fake를 사용했다. 기존 테스트 이름 변경·xfail/skip 추가는 없다. 기존 regression expectation의 변경은 허용된 작은 계약내 정규화/10분 coalesce에 한하며 위험 차단 테스트는 그대로 유지한다.

| Suite | Baseline | 최종 handoff | Subtest |
|---|---:|---:|---:|
| tests | 1264 | 1314 | 27 |
| gemini_checks | 230 | 230 | 38 |
| negative_guard_checks | 41 | 41 | 7 |
| rollback_checks | 37 | 37 | 12 |
| rollback_full_checks | 257 | 257 | 46 |
| 합계 | 1829 | **1879** | **130** |

정확한 candidate cwd의 bare `python -m pytest -q`: **1314 passed / 27 subtests**. pytest.ini의 testpaths=tests로 canonical 기본 suite를 지정했고 별도 네 suite도 각각 전부 실행했다. 기존 bare 수집 충돌 실패와 모든 중간 RED/실패 출력, 최종 로그/XML을 보존했다. 수정된 run_verification.py는 candidate와 test-isolation을 절대 경로로 사용한다. 기존 경로 오류 tools/run_verification.py는 사용하지 않았다.

추가 RED: red-v6(PI/알림/coalesce/최소수량), red-profit-candidate, red-outbox-startup, red-candidate-fresh-guard, red-final-stop-leverage-cap, red-observability(릴리스 비용/identity), red-nonfinite-block-notification, red-margin-reduction-reasons. 최종 신규 계약 50개 모두 handoff-tests 및 bare에 포함한다.

C current behavior 1313개 통과(낡은 hash assertion 1개만 이 단계에서 제외)와 native DOGE replay를 확인한 후 변경된 C 소스와 알림 의존성만 parity SHA에 고정했다. 최종 suite에서는 해당 hash assertion을 포함해 모두 통과했다. 배포 전용 preregistration은 Git에 없으므로 로컬 full LIVE parity=false를 명시한다. LIVE의 해당 파일 SHA는 기존 pin과 일치한다. 새 릴리스의 full parity/activation blockers는 operator가 실제 배포 경로에서 다시 검사한다. source/event/parity 체크에서 patch20개 SHA, pre-GPT callsite13개, source compile, actual LIVE baseline20/20을 확인했다.

## 인계와 남은 위험

변경 runtime 20개는 `deployment-patch/manifest.json`과 `source/`에 정확히 기록된다. 테스트/pytest.ini와 문서는 source-manifest에 별도 포함한다. test-generated flask_secret.key / vapid_private_key.pem, .env/사용자 폴더/운영 DB/credentials는 manifest와 보존본에서 제외한다.

주 작업자는 현재 LIVE baseline→patch source hash 확인→새 릴리스 full C parity/activation 검사→기존 승인 배포 작업→candidate phase 확인을 진행한다. `LIVE_VERIFY.md`와 `verify_live_readonly.py`는 source/settings/order 변경 없이 현재 상태를 검증한다. 외부 account/settings가 바뀌면 baseline assertion이 차단한다. 자동 승인 우회, 시험 주문, 임의 청산은 없다.

검토 위험: 작은 TP/SL 호가 보정의 범위와 structural fallback 계약 구분; 기존 포지션/receipt의 restart 보호 및 reversal 사이즈 전달; outbox DELIVERY_UNKNOWN의 수동 관찰(확정 안 된 전송을 자동 재발송하지 않음); 자연 진입/부분익절/OCO 체결과 배포 이후 실제 비용·수익 성과는 추가 관찰 필요. 수익 보장 및 학습 승격 완료를 주장하지 않는다.
