# GPT 지시문 — 자동매매 검토용 증거 수집 (읽기 전용)

아래 `---` 아래 내용을 그대로 GPT(서버 접속 권한이 있는 쪽)에게 붙여 넣는다.

---

너는 자동매매 VM(`autotrader-vm`, zone `us-central1-a`, project `autotrader-okx-gemini-0037`)에서
**읽기 전용 증거 수집**만 한다. Claude가 이 증거로 운영본 전체를 검토한다.

## 절대 하지 말 것
1. 기존 봇의 코드·설정·`.env`·플래그 파일·서비스·데이터를 수정하거나 재시작하지 않는다.
2. 주문·청산·TP/SL 변경, 실거래 중지/시작, 롤백을 하지 않는다.
3. 수집이 끝날 때까지 배포나 재시작을 하지 않는다. 진행 중인 작업이 있으면 먼저 끝내고 시작한다.
4. 결과 번들을 공개 레포 `mingmingki/chickenbananalab`에 올리지 않는다. API 키나 `.env` 원문을 어디에도 붙여넣거나 커밋하지 않는다.

## 순서
1. VM에서 수집 스크립트를 커밋 고정 주소로 받아 해시를 확인한다. 해시가 다르면 멈추고 보고한다.
   ```sh
   curl -fsSL https://raw.githubusercontent.com/mingmingki/chickenbananalab/31aa99f30ed5df3ea0e1c0c8a522c02c8da0c52e/tools/autotrader_audit/collect_autotrader_audit.sh -o ~/collect_autotrader_audit.sh
   echo "e5992aa899b6bcfb4e31b6b707c56bc2917ae1761fdce5559ffc2413a01fb597  $HOME/collect_autotrader_audit.sh" | sha256sum -c -
   ```
2. 실행한다. 10~20분 걸린다. OKX는 조회(GET)만 하고, 주문 관련 호출은 없다.
   ```sh
   sudo bash ~/collect_autotrader_audit.sh
   ```
   마지막에 나오는 `bundle:`, `size:`, `sha256:` 줄을 기록한다.
3. 비밀값 검사 결과를 확인한다. 첫 줄의 숫자가 3 이상이어야 하고 `WARNING`이 없어야 한다. 아니면 멈추고 보고한다.
   ```sh
   tar -xzOf <bundle 경로> --wildcards '*/secret_scrub_report.txt' | head -3
   ```
4. 번들을 git push가 되는 곳(보통 Mac)으로 가져온다.
   ```sh
   gcloud compute scp 'autotrader-vm:~/cbl_audit_*.tar.gz' . --zone=us-central1-a --project=autotrader-okx-gemini-0037
   ```
5. 비공개 레포 `mingmingki/chickenbanana-autotrader`의 새 브랜치에 올린다. 95MB가 넘으면 `split -b 90m`으로 나눠 올린다.
   ```sh
   git clone --depth 1 https://github.com/mingmingki/chickenbanana-autotrader cbl-audit-push && cd cbl-audit-push
   git checkout -b audit/v341-$(TZ=Asia/Seoul date +%Y%m%d-%H%M)
   mkdir -p audit-bundles && cp ../cbl_audit_*.tar.gz audit-bundles/
   ```
6. 같은 폴더에 `audit-bundles/GPT_CHANGES.md`를 만들어, **최근 7일 동안 네가 서버에서 바꾼 것**을 시간순으로 적는다.
   - 배포한 릴리스 이름
   - 바꾼 설정과 플래그 파일
   - 재시작
   - 수동 주문·청산
   - 지금 알고 있는 오류
   모르면 모른다고 적는다.
7. 커밋하고 푸시한다.
   ```sh
   git add audit-bundles && git commit -m "Audit evidence bundle" && git push -u origin HEAD
   ```

## 보고
- 브랜치 이름
- 번들 파일 이름·크기·sha256
- 활성 릴리스(`active_release_name` 줄)
- 수집 중 실패한 단계
- `okx/_meta.json`의 errors 개수
