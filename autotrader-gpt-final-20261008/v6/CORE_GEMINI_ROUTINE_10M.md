# CORE Gemini 주분석 10분 정책 · 2026-10-09

운영 릴리스: `entry_cost_v6_20261009T061845KST`. 안전장치/주문 보호·기존 계정 설정 변경 없음.

- `POLL_INTERVAL_SECONDS=300`: 코인 상태 및 위험·보유관리 5분 감시 유지
- `CORE_GEMINI_ROUTINE_INTERVAL_SECONDS=600`: 비중요 가격/지표 변화는 성공한 마지막 호출 시점부터 최소 10분 간격으로 묶기
- 완전히 안정적이면 기존 30분 fallback 유지. 억지로 매 10분 Gemini를 호출하지 않음
- 신규 early setup, 포지션 변경, 1H/5m 구조 변경, 중요 시장 이벤트, 최소 1ATR 급변은 즉시 호출
- 주문 승인·추격 방지·일일 손실 한도·SL/TP 보호·기존 5분 위험 대응은 변경하지 않음
- 관찰/상세: `/api/state`의 `settings.core_gemini_routine_interval_seconds=600`, `settings.core_gemini_stable_fallback_seconds=1800`, 서버 `CORE_AI_BUDGET_CALL/SKIP reason`, 용도별 AI 비용 분석
- 안전 테스트: 2026-10-09 로컬 집중 102 passed; 5묶음 1,895 passed + 130 subtests. 새 릴리스 상태: CORE 4/Candidate C 2 LIVE 실행, CORE kill switch OFF, restart 0; 기존 SL/TP 사전 검증 OK.
- 실제 AI 비용 절감액·진입 성과는 아직 미측정. 변경 전 24h 비용 추정 CORE Gemini $95.61/월, 전체 $141.83/월. 동일 길이의 변경 후 24h 청구/호출 및 `entry_setup_changed` 긴급 사건 지연율 비교 필요.
