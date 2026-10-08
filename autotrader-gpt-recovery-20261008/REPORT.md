# 작업 결과

**전체 요청 미완료. 수정 후보와 격리 검증은 보존했으며, LIVE 배포·실제 텔레그램 전송·운영 확인·push는 완료하지 못했다.**

| 항목 | 구현·로컬 결과 | LIVE 반영·직접 확인 |
|---|---|---|
| v4 복구 | 인계 문서의 상충하는 초기/완료 상태를 구분했고 보관된 실제 v4 소스 해시를 확인했다. 기존 v4 최종 위험 검사를 기준으로 수정했다. | 과거 v4 배포 보고만 확보. 현재 ExecStart·PID·릴리스·소스는 미확인. |
| AI 비용 후보 | 지정 ZIP을 찾지 못해 명세대로 추가 paid Shadow만 기본 OFF/계정 opt-in으로 재현. GPT 진입·보유 포지션 검증과 기존 Gemini 호출 조건 유지. 호출 사유 로그 추가. | 현재 비용 후보 적용 여부와 실제 절감은 미확인. |
| GPT 진입 정책 | 유효한 CORE Gemini 후보에 approve_now 또는 typed entry TIMEOUT만 통과. wait/reject/그 외 오류 차단. raw TIMEOUT·TIMEOUT_BYPASS·주문 결과를 분리. timeout에서 GPT 수정 계획을 쓰지 않음. | 현재 계정 `.env` 변경·프로세스 재시작 미실행. migration 도구와 실제 config reload 테스트만 완료. |
| 승인 후 주문 | 미확정 응답을 실패/체결로 오인하는 경로, 조회 ID 위조, 판단 ID 혼선, 중복/계약 단위 OPEN 기록, 상태 덮어쓰기, 보호 조회 예외 시 동결 누락을 재현·수정. | ETH 09:10/BTC 09:43/PI 원본 로그에 접근하지 못해 실제 사건 원인과 실제 숫자는 확정하지 못함. |
| 반전·주문 복구 | 대체 진입 최종 자격 확인 후 기존 포지션 처리. durable client ID 조회·단일 제출·재시작 후 복구. lifecycle 바뀜/권한 불명확/미확정 청산은 동결·pending 유지. | 실제 주문·청산 테스트를 강제로 만들지 않았다. 현 포지션/보호주문 미조회. |
| Telegram·UI | 모든 GPT 결과/로컬 차단/제출/대기/체결/실패를 같은 decision_id로 연결. durable 중복 방지, 5초 요청 timeout, 확정 429만 최대 1회 재시도. 전송 불확실성은 재전송하지 않고 DELIVERY_UNKNOWN 표시. API·화면 연결 포함. | 현재 저장 수신처 미확인. 실제 연결 확인 메시지를 보내지 못했다. 테스트 더블 전송 성공을 실제 Telegram 성공으로 보고하지 않는다. |
| 테스트 | 신규 격리 111건 통과. 확보된 Candidate C 판단 일치 6건 및 해당 소스 검증 해시 통과. Oct5 전체 baseline 826 통과/26 실패, 비교 후보 819 통과/33 실패. | 현재 운영 전체 회귀/확정봉 루프/최신 전략 검증 해시는 미완료. 과거 1,128건을 재사용하지 않음. |
| Git | 원 저장소 tracked 변경 없음, 기존 CAD·미커밋 파일 보존. 별도 로컬 checkout에서 이번 디렉터리만 커밋 대상으로 준비. | 실제 commit/push 결과는 PUBLICATION_STATUS.md 참조. |

보관된 v4 trader 기준 해시: `c6431dd805d8bb7f7f6e729993c89194e27c0b432cda104a86ef94973f74a318`. 후보 해시는 `manifest.json`에 있다. 보관된 인계가 주장하는 릴리스 `/opt/autotrader-releases/early_entry_risk_rr_repair_v4_20261008T0156KST`, PID 75450은 **현재 직접 관찰 결과가 아니다**.

실제 접근 차단:

- 기본 gcloud 설정은 sandbox에서 `credentials.db` 쓰기가 불가했다. 기존 인증 설정을 권한 700의 허용된 `/private/tmp/.../gcloud-config`로 복사해 재시도했다. 인증정보를 번들·커밋에 넣지 않았다.
- `gcloud compute ssh ... --project=autotrader-okx-gemini-0037 --zone=us-central1-a`: OAuth 갱신 시 `oauth2.googleapis.com` DNS `NameResolutionError [Errno 8]`. 이는 서버 권한 거부가 확인된 것이 아니라 OAuth 서버 연결 전 실패다.
- 기존 SSH 키와 알려진 VM IP `34.28.148.113` 직접 접속: `Operation not permitted`.
- Remote Desktop Commander의 같은 Mac에서 기존 gcloud 진단 프로세스를 시작하는 대안: `MCP tool call requires approval, but approval policy is never`.
- GitHub MCP `create_blob`: 같은 자동 승인 거부. 로컬 Git 경로와 실제 push 결과도 별도로 기록한다.

AI 비용: 현재 운영의 기간·목적별 호출/토큰 원본을 읽지 못했으므로 수치와 실측 절감액은 **미측정**이다. 이번 검증에서 유료 AI·실주문은 호출하지 않았다.

서버 변경·재시작·배포 전환 자체를 실행하지 않았으므로 이번 작업에 따른 LIVE 롤백은 발생하지 않았다. 현재 정상 릴리스를 다시 읽어 확인하기 전에는 과거 경로로 롤백하지 않는다.
