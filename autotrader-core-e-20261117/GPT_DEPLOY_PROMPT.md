# GPT 지시문 — Core E 모의 매매를 서버에 올리기

아래 내용을 그대로 GPT(서버 접속 권한이 있는 쪽)에게 붙여 넣는다.

---

## 역할과 목표

너는 내 자동매매 서버(VM)에 **Core E 모의 매매**를 올리는 작업을 한다.
Core E는 ZEC-USDT-SWAP 초단타 규칙 엔진이다. OKX **공개 시세만** 읽고 **주문을 하나도 내지 않는다**.
목표는 이것을 기존 봇(CORE, Candidate C) 옆에서 독립된 서비스로 2주 동안 돌려, 실시간 모의 결과를 쌓는 것이다.

## 코드 위치

- 저장소: `mingmingki/chickenbananalab`
- 브랜치: `ccr-2977f3d7-i1ayxh` (커밋 `2602184` 이후)
- 폴더: `autotrader-core-e-20261117/`
  - 설명: `README.md`
  - 실행: `python -m core_e.run_paper`
  - 결과 요약: `python -m core_e.report`
  - systemd 예시: `deploy/core-e-paper.service`

## 절대 하지 말 것

1. 기존 봇의 코드, 설정, `.env`, 서비스 파일, 데이터를 **수정하거나 재시작하지 않는다.** 읽기만 한다.
2. Core E에 API 키, 시크릿, 패스프레이즈를 넣지 않는다. 비공개 OKX 주소(`/api/v5/trade`, `/api/v5/account`, `ws/v5/private` 등)를 부르는 코드를 추가하지 않는다.
3. Core E 설정의 `mode`를 `paper`가 아닌 값으로 바꾸지 않는다. 실주문 기능을 만들지 않는다.
4. 이 브랜치를 `main`에 합치지 않는다. 강제 푸시하지 않는다.
5. 테스트가 하나라도 실패하면 서비스를 켜지 말고 멈춰서 보고한다. 테스트를 건너뛰거나 지우지 않는다.

## 작업 순서

1. **사전 확인 (읽기만)**
   - 기존 봇 서비스 이름, 실행 사용자, 작업 폴더를 확인한다. 예: `systemctl list-units --type=service | grep -i -E 'autotrader|trader|bot'`
   - 확인 결과는 기록만 하고 건드리지 않는다.
   - Python 버전을 확인한다. 3.10 이상이어야 한다.
   - `websockets` 버전을 확인한다. 13 이상이어야 한다.
     - 확인 명령: `python3 -c "import websockets; print(websockets.__version__)"`
     - 없거나 13 미만이면 시스템 패키지를 바꾸지 말고, 아래 6번에서 `--feed rest`를 쓴다.
   - 디스크 여유를 확인한다. 모의 기록은 하루 수 MB 수준이다.

2. **코드 가져오기**
   - 위 브랜치에서 `autotrader-core-e-20261117/` 폴더만 `/opt/core-e`에 복사한다. 기존 봇 폴더와 겹치지 않는 곳이어야 한다.
   - 복사한 커밋 해시를 기록한다.

3. **테스트**
   - `cd /opt/core-e && python3 -m unittest discover -s tests -v` 를 실행한다.
   - 42개가 모두 OK여야 다음으로 넘어간다.

4. **짧은 실제 시험 (3분)**
   - 실행: `python3 -m core_e.run_paper --out /tmp/core_e_smoke --max-runtime-sec 180`
   - 웹소켓으로 시세가 들어오는지 확인한다. 로그에 "웹소켓 연결됨"이 나와야 한다.
   - 끝나고 `/tmp/core_e_smoke/status.json`의 `process`가 `stopped`인지 확인한다.
   - 웹소켓이 계속 끊기면 `--feed rest`로 다시 시험하고, 그 이유를 보고한다.

5. **기록 폴더 준비**
   - `/var/lib/autotrader/core_e`를 만든다.
   - 소유자는 서비스를 돌릴 사용자로 한다. 기존 봇과 같은 사용자를 쓰되, 기존 봇 파일은 건드리지 않는다.

6. **systemd 서비스 등록**
   - `deploy/core-e-paper.service`를 복사해 `/etc/systemd/system/core-e-paper.service`로 둔다.
   - `WorkingDirectory`, `User`, `ExecStart`의 python 경로를 이 서버에 맞게 고친다.
   - 4번에서 rest로 바꿨다면 `ExecStart` 끝에 `--feed rest`를 붙인다.
   - `sudo systemctl daemon-reload && sudo systemctl enable --now core-e-paper`
   - 서비스 이름은 반드시 `core-e-paper`로 한다. 기존 서비스 이름과 겹치면 멈추고 보고한다.

7. **동작 확인 (켠 뒤 15분)**
   - `systemctl status core-e-paper` 가 active (running)이어야 한다.
   - `journalctl -u core-e-paper -n 50` 에 오류가 없어야 한다.
   - `/var/lib/autotrader/core_e/status.json` 이 5초마다 갱신되는지 확인한다(`wall_ts`가 바뀌는지).
   - 처음 10분은 평소 거래량을 쌓는 중이라 진입이 없다. 그 뒤 `warmed_up`이 `true`여야 한다.
   - 기존 봇 서비스가 계속 정상인지 확인한다(상태, CPU, 메모리). Core E 하나의 CPU와 메모리 사용량도 적는다.

## 보고 형식

아래를 한 번에 정리해서 보고한다.

- 기존 봇 서비스 이름, 사용자, 폴더 (건드리지 않았음을 명시)
- Python 버전, websockets 버전, 시세 방식(ws 또는 rest)
- 복사한 커밋 해시와 설치 경로
- 테스트 결과 (42개 통과 여부, 실패 시 출력 전체)
- 3분 시험의 로그 마지막 20줄
- `systemctl status core-e-paper` 결과
- 켠 뒤 15분 시점 `status.json` 전체
- 기존 봇 상태와 자원 사용량, Core E 자원 사용량
- 문제가 있었다면: 무엇을, 왜, 어떻게 했는지

## 매일 확인 (2주 동안)

매일 한 번 아래를 실행하고 결과를 보고한다.

```sh
cd /opt/core-e
python3 -m core_e.report --ledger /var/lib/autotrader/core_e/events.jsonl --day $(TZ=Asia/Seoul date -d yesterday +%F)
python3 -m core_e.report --ledger /var/lib/autotrader/core_e/events.jsonl
systemctl is-active core-e-paper
grep -c '"type": "gap"' /var/lib/autotrader/core_e/events.jsonl
```

설정(진입 조건, 익절·손절 폭 등)은 **바꾸지 않는다.** 결과가 나빠도 그대로 두고 숫자만 보고한다.
2주 동안 설정을 바꾸면 결과를 판단할 수 없게 된다.

## 2주 뒤 판단 기준 (판단만, 실행은 하지 않음)

아래를 모두 넘으면 "실거래 검토 가능", 하나라도 못 넘으면 "계속 불가"로 보고한다.

- 거래 300건 이상
- 수수료 후 합계 플러스
- PF 1.2 이상
- `비용이 매번 3bp 더 들었다면` 값도 플러스
- 날짜별 결과가 특정 하루에만 몰려 있지 않을 것

"실거래 검토 가능"이 나와도 **실주문 기능을 만들거나 켜지 않는다.** 나에게 보고만 한다.

## 멈추기와 되돌리기

```sh
sudo systemctl disable --now core-e-paper
sudo rm /etc/systemd/system/core-e-paper.service && sudo systemctl daemon-reload
# 기록은 남겨둔다: /var/lib/autotrader/core_e/
```
