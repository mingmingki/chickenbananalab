# 남은 작업 — 승인 재요청 불필요

1. 네트워크/프로세스 실행이 허용된 기존 인증 환경에서 GCP에 접속한다. `.env`, 토큰, OAuth·SSH 인증 파일을 출력하거나 업로드하지 않는다. 실패한 기존 명령:

   ```sh
   env CLOUDSDK_CONFIG=/private/tmp/autotrader-gpt-recovery-20261008/gcloud-config gcloud compute ssh autotrader-vm --zone=us-central1-a --project=autotrader-okx-gemini-0037 --quiet --ssh-key-file=/Users/bagmingi/.ssh/google_compute_engine --command='systemctl show autotrader.service -p ActiveState -p MainPID -p NRestarts -p WorkingDirectory'
   ```

2. 실제 `/opt/autotrader` 대상, 서비스 ExecStart/PID/프로세스 cwd, 실행 파일 SHA256, 실제 계정 및 CORE engine/ownership를 읽는다. 현재 추가 비용 후보와 v4 적용 여부를 코드 해시·실행 경로로 판정한다. 과거 경로와 README만 믿지 않는다.
3. 10월 8일 ETH 09:10/BTC 09:43/PI 및 최근 승인 사례를 같은 decision_id/lifecycle로 후보→계획→GPT→게이트→거래소→체결→보호→UI까지 추적한다. 실제 실패 원인을 수정하고 원본 사건 재생을 추가한다. 본 번들의 합성 재현을 ETH 실제 원인으로 바꾸어 보고하지 않는다.
4. 현재 정상 릴리스와 설정을 비공개로 백업한다. 그 **실제 최신 소스**로 별도 후보를 만든다. `tools/preflight_overlay.py <실제소스>`는 읽기만 하고 qualification 실패를 반환한다. 해시가 다르면 기존 변경 위로 함수별 이식한다. `ui_integration.py`는 단일 anchor가 바뀌면 실패한다.
5. 실제 계정이 legacy CORE이면 본 변경 경로를 검증한다. unified CORE라면 해당 worker/adapter/최종 policy/주문·알림 경로에도 정책을 구현하고 재생한다. ownership 검사나 정상 위험 검사를 제거하지 않는다.
6. 최신 전체 회귀, 실제 LIVE/백테스트 판단 일치와 전략 소스 검증 해시를 실행한다. 기존 실패를 고치고 통과 로그를 저장한다. 정책 해시를 결과 없이 임의 갱신하지 않는다.
7. 현재 사용자로 확인된 계정의 `.env`에 `GPT_ENTRY_GATE_ENABLED=true`, `CORE_GPT_ENTRY_TIMEOUT_BYPASS=true`, `CORE_PAID_SHADOW_ENABLED=false`를 적용한다. `active_account_policy.py`는 다른 설정을 보존하며 비공개 backup을 생성한다. 계정 추정·loop 강제 시작 기능은 없다.
8. 기존 배포 도구/서비스 방식으로 후보 전환·재시작한다. 기존 loop 시작/중단 상태, 포지션·보호주문·공유 데이터와 설정을 보존한다. 운영 실패면 **이번에 직접 확인한 직전 정상 릴리스**로 복구한다.
9. 새 PID/ExecStart/cwd/hash/실제 읽는 계정 설정, CORE/Candidate C 확정봉 heartbeat, 포지션/보호주문, 이벤트 UI/저널/알림 일치, 중복 주문·재시작·예외 여부를 직접 확인한다. 저장된 같은 Telegram 수신처로 연결 확인 메시지를 보내 Telegram `ok=true/message_id`를 확인한다. 알림 token/chat ID는 출력하지 않는다.
10. 실제 새 신호가 없으면 거래를 강제로 만들지 않는다. 운영 루프 확인과 격리 재생을 실제 체결 확인과 구분한다. 이후 정상 릴리스 결과·비용의 기간/목적별 실제 호출·토큰을 인계에 기록하고 관련 파일만 push한다.

로컬 보존 커밋/번들/실패한 push의 정확한 정보는 PUBLICATION_STATUS.md에 있다. 이 작업은 이미 사용자에게 승인되어 있으므로 동일 범위 배포·전송·push에 다시 동의를 묻지 않는다.
