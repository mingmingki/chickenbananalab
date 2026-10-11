# AI 비용 절감 v15 — LIVE 배포·검증 완료

2026-10-11 10:12:10 KST 배포 완료.
릴리스: /opt/autotrader-releases/gemini_management_v15_20261011T101109KST
서비스 PID:191484,재시작0. 단일 배포 유닛 성공,종료0.

| 기능 | LIVE 담당 |
|---|---|
| Candidate C 진입·SL/TP·부분익절·부분손절·이익보호 | 차트/위험 코드,AI0 |
| CORE 진입 방향·시점·실제 SL/TP 선택 | Gemini |
| 신규·고정금 추가·반전 진입 승인 | GPT;wait/reject 차단,기존 확인된 typed timeout 예외 유지 |
| CORE 유지·부분익절·부분손절·전량청산 행동 | Gemini,명시적 management_action |
| ATR·손실·수익 되돌림·차트 위험과 감축 수량 | 코드;CORE 최초 수량 기준5~50%,누적50%한도 유지 |
| 거래패턴 성과 집계 | 로컬 코드;자동6시간 및 수동 유료AI 분석 중단 |

수동 CORE SL/TP 계산의 스키마 예외,명시적HOLD/REDUCE/ADD를 무시할 수 있던 반전 청산 경로를 수정했다. 반전은Gemini CLOSE_ALL 및별도GPT진입승인이 필요하다. 추가진입은승인방향/가격과실제보호주문 일치검사를 거친다. 기존 유료 Shadow/HoldAudit은새모드에서 호출되지 않는다.

검증: 전체2033개 테스트 +130개 하위테스트,실패0. 새기능37개. 독립리뷰 Important사항수정,회귀재현후통과.
서버:CORE/Candidate모두 실행,HTTP200,소스19개 해시일치,설정변경 CORE_GEMINI_MANAGEMENT_ONLY=true 1개.
배포 전후 포지션6/OCO6 완전동일,미체결일반주문0,현재모든포지션의정확한수량과SL/TP보호검증.

배포 후 첫0.97분 실제관찰:Gemini 기본분석4회,보유리뷰3건 모두기존Gemini분석재사용,HOLD3건(BTC/ETH/XRP),GPT호출0회. 유료보유GPT제거와재사용은실제로그로확인했다.
표본구간의 코드상비용추정 $0.023815는제공사청구액이 아니다. 짧은표본으로 월절감액이나 수익성 개선을 확정하지 않는다. 감축/추가 주문의 부분체결·보호보존은격리테스트로검증했으며강제LIVE주문을 발생시키지 않았다.

복구:유닛 autotrader-ai-cost-v15-deploy-20261011.service 와 /tmp/autotrader-ai-cost-v15-qualified-20261011/deployment-record.json 먼저조회. 재배포하지 말 것.
정규 체크포인트: /private/tmp/autotrader-integrated-audit-publication-20261010/autotrader-integrated-audit-20261010/CHECKPOINT.md
검증소스커밋01db8be,배포의도커밋e5248ed. 최종증거커밋은정규git기록조회.
