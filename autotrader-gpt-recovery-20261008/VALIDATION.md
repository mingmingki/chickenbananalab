# 이번에 실행한 검증

- `final-targeted.xml/log`: **111 passed**, typed SDK timeout/error matrix, LONG/SHORT 승인·timeout 실제 가짜 주문/보호 경로, wait/reject/error/hold 차단, 최종 가격·신선도·수량·위험 검사, GPT 수정 TP2/SL 적용, timeout 원본 계획, 반전 전 검사, client ID 단일 제출/조회/복구, 원래 lifecycle 유지, 중복 OPEN 금지, 전송 실패 영향 없음, UI 렌더링, Shadow opt-in, cooldown 종료 직전 setup 트리거, 계정 설정 reload.
- RED 증거: `policy-red`, `pipeline-red`, `notifications-red`, `pending-red`, `review-red`, `safety-red`, `binding-red` 로그. 중간 GREEN/실패도 삭제하지 않았다.
- `available-baseline.xml/log`: 확보된 **Oct5** 소스 전체, **826 passed / 26 failed / 27 subtests passed**.
- `available-candidate.xml/log`: 같은 Oct5 의존성에 변경된 진입 함수와 지원 소스를 연결한 비교, **819 passed / 33 failed / 27 subtests passed**. 이것은 전체 v4/current LIVE가 아니다.
- `available-regression-delta.json`: 모든 실패와 신규 8개/해소 1개를 보존. 신규 실패에는 CORE 밖 가짜 심볼 `X`, action 없는 Gemini fixture, snapshot 없는 반전 fixture 3개, client ID/체결 응답을 지원하지 않는 수동 진입 fixture, v4에서 이미 관찰 전용으로 바뀐 학습·과거 위험 점수 기대가 포함된다. 해당 검증을 삭제하거나 안전 검사를 완화하지 않았다. 최신 전체 소스·테스트를 확보하여 실제 동작과 일치하도록 이식/검증해야 하며 합격 처리하지 않는다.
- 중간 `available-candidate-incompatible-budget.log`: 변경하지 않은 v4 budget helper까지 Oct5 run_cycle에 넣은 잘못된 비교에서 56 실패. 이 helper는 최신 `core_entry_timing`을 요구한다. 최종 비교는 Oct5 run_cycle/budget을 그대로 유지하며, v4 budget 자체는 별도 source-preservation/직접 함수 테스트로 확인했다. 이 중간 실패도 보존했다.
- `available-parity.xml/log`: 확보된 Candidate C per-bar 판단 일치·약화 진입 baseline **6 passed**. `available-parity-hashes.json`: 그 확보된 파일에 대한 `verified=true`. 현재 VM 전략 해시 검증은 아니다.
- `full-runtime-import.log`: 후보 전체 import는 `ModuleNotFoundError: core_entry_timing`으로 실패. 이후 필요한 최신 market_context 등과 최신 전체 테스트도 현재 확보되지 않았다. 가짜 모듈로 이 실패를 숨기지 않았다.
- `preflight-old-source-rejected.json`: Oct5 전체 소스로 v4 후보를 적용하려는 경우 trader/Adaptive 해시 불일치로 차단. 배포 qualification=false.
- `server-access-final.log`: 실제 GCP 접근 재시도 실패. 과거 배포·테스트 기록을 이번 결과로 쓰지 않았다.

모든 AI·거래소 주문·Telegram 전송 테스트는 더블이다. 실제 계산기·수량 변환·보호주문 검증·SQLite·저널 코드를 사용했고 `tools/sitecustomize.py`로 테스트 socket 접속을 차단했다. 유료 AI와 실주문을 테스트로 호출하지 않았다.

코드 리뷰가 발견한 결함을 RED 재현 후 수정했다. 최종 리뷰와 독립 테스트도 111건 통과했으나 리뷰 범위는 확보된 legacy 진입 수정본이며 운영 합격을 의미하지 않는다.
