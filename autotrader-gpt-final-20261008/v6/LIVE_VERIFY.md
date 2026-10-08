# 현재 LIVE 기준 read-only 검증

현재 baseline: /opt/autotrader → /opt/autotrader-releases/core_gpt_recovery_v5_20261008T213530KST. trader SHA256 0218e6f6138a070fa79c2aebd9b608730105e4b7f6bd9a2c9ce8fc3b4e557190. 20개 patch baseline와 배포전용 preregistration SHA는 evidence/live-baseline-check.json에서 전부 일치했다. 운영 source/settings/positions/SL/TP/orders는 변경하지 않았다.

주 작업자가 검토한 patch와 verify_live_readonly.py를 VM의 staging 디렉터리에 둔 뒤, 아래 STAGING만 실제 경로로 대체한다. 검증 script는 계정 import 전에 AUTOTRADER_PROJECT_DIR=/opt/autotrader를 설정하고 credential이나 환경값을 출력하지 않는다. READ-ONLY 상태의 outbox 집계만 읽는다. real order/provider/Telegram HTTP 호출을 하지 않는다.

```sh
/opt/homebrew/bin/gcloud compute ssh autotrader-vm --zone us-central1-a --project autotrader-okx-gemini-0037 --command 'readlink -f /opt/autotrader; sha256sum /opt/autotrader/trader.py; systemctl show autotrader.service -p MainPID -p NRestarts -p ActiveState -p SubState'
/opt/homebrew/bin/gcloud compute ssh autotrader-vm --zone us-central1-a --project autotrader-okx-gemini-0037 --command '/opt/autotrader/.venv/bin/python STAGING/verify_live_readonly.py STAGING/deployment-patch baseline'
```

배포/전환은 주 작업자의 기존 V6_DEPLOY_OPERATOR.py가 맡는다. 이 후보 작업에서 실행하거나 수정하지 않았다. 배포 후 동일 staging 파일로 검증한다:

```sh
/opt/homebrew/bin/gcloud compute ssh autotrader-vm --zone us-central1-a --project autotrader-okx-gemini-0037 --command '/opt/autotrader/.venv/bin/python STAGING/verify_live_readonly.py STAGING/deployment-patch candidate'
```

phase candidate는 실제 release source 20개 및 full C parity와 승인/activation/policy/service 상태를 검사한다. 기존 계정/설정/포지션·보호 주문 보존은 operator의 private deployment evidence에서 대조한다. 자연 새 확정봉 판단→차단 outbox 또는 승인→실제 fill/OCO→부분감축/SL lock을 관찰한다. 시험 주문/강제 청산으로 관찰을 채우지 않는다. Telegram DELIVERY_UNKNOWN/FAILED/429는 그대로 안전하게 기록하며, 정상 메시지 도착 여부는 operator가 실제 수신 증거로 확인한다.

운영 비용 API /api/operating-costs에서 release_cohort의 created_at/source/배포후 cost/calls와 rolling24h의 pre/post split을 확인한다. costs는 토큰 기반 기존 추정치이고 실제 SDK retry/실패 청구가 unknown이면 미확인으로 남긴다. 자연 거래 경제손익과 절감률은 배포 이후 신규 cohort가 누적된 뒤 평가한다.
