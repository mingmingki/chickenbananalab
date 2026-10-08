# CORE GPT 복구 후보 — LIVE 미반영

이 디렉터리는 2026-10-08 승인 범위에서 만든 **검토용 수정본**이다. 운영 배포본이 아니다. 먼저 [REPORT.md](REPORT.md), [VALIDATION.md](VALIDATION.md), [manifest.json](manifest.json)을 읽는다.

- 보관된 v4 trader를 기준으로 CORE GPT 승인/확인된 timeout 예외, 최종 위험 검사, 주문 상태·ID 복구, Telegram 이벤트와 UI를 수정했다.
- 실제 현재 VM 릴리스·계정·로그에 접근하지 못했다. Oct5 지원 모듈과 현재 운영의 동일성도 확인하지 못했다.
- 현재 전체 런타임 import와 회귀 합격은 미완료다. 원래 `autotrader/`와 사용자의 기존 파일, CAD, 운영 설정·포지션은 변경하지 않았다.
- `manifest.json`의 `deployment_qualified=false`를 사실 확인 없이 바꾸지 않는다. 과거 v4 배포 기록과 1,128건 테스트는 이번 작업 결과가 아니다.
- 지원 파일은 현재 운영 파일과 **전체 기준 해시가 일치할 때만** 사용할 수 있다. 최신 운영 변경이 있으면 그 변경 위에 함수별로 이식하고 다시 검증한다. 이 번들로 이전 릴리스를 덮어쓰지 않는다.

`source.diff`는 구현 변경, `patch/`는 후보 소스, `evidence/`는 이번에 직접 실행한 RED/GREEN·전체 비교·접근 실패 기록이다. `tools/load_entry_kernel.py`의 격리 테스트는 빠진 최신 모듈을 가짜로 만들지 않고 확보된 실제 Oct5 의존성에 수정된 함수를 로드한다. 따라서 전체 v4 실행 검증을 대체하지 않는다.

현재 구현은 보관된 **legacy CORE 진입 경로** 대상이다. CORE unified 소유권이 있는 심볼은 이 경로로 우회 주문하지 않는다. 실제 계정이 unified 엔진을 사용한다면 그 실제 진입 경로에도 정책·알림을 이식하고 검증해야 한다. 현재 계정 엔진은 확인하지 못했다.

커밋·push의 실제 결과는 같은 디렉터리의 `PUBLICATION_STATUS.md`에 기록한다. 재개 단계는 [RESUME.md](RESUME.md)를 따른다.
