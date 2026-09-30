"""Phase 1 - Feature Shadow 로그. market_structure.compute_multi_timeframe() 결과를
매 사이클 기록만 한다 - 실거래 판단/프롬프트에는 아직 쓰지 않는다.
Gemini의 실제 action/confidence를 같은 레코드에 같이 남겨야, 나중에(Phase 3) "구조가
깨졌는데도 Gemini가 hold였던 사례"를 사후에 걸러낼 수 있다.

--- rotation(2026-08-27 추가) ---
CORE(300초, BTC/ETH)에 더해 FAST(60초, XRP/PI)까지 이 로그에 기록하게 되면서 무제한
증가를 막을 필요가 생겼다. production 실측: 20GB 디스크 중 14GB 여유, 이 파일은 CORE
단독 기준 약 1.24MB/day(2026-08-25~27 실측: 1600줄/2.02일), FAST(XRP+PI 상시 가동
가정)까지 더하면 약 8~9MB/day로 추산된다.
회전은 "현재 파일이 MAX_BYTES_PER_FILE 이상이면 타임스탬프를 붙여 rename하고 새
파일로 이어쓰기" 방식이다. os.rename()은 같은 파일시스템 안에서 원자적이므로(POSIX
보장) "쓰다가 잘리는" 상태가 생기지 않는다 - rotate 검사와 그다음 append를 항상 같은
락(jsonl_cache.get_path_lock, 이 파일의 read-path 파싱과 공유) 안에서 수행하므로,
CORE/FAST 스레드가 동시에 써도 서로의 쓰기가 겹치거나 rotation 경계에서 레코드가
쪼개지지 않는다. 오래된 레거시 레코드(rotation/engine_group 필드가 생기기 전)는
그대로 아카이브에 남아 있을 뿐 형식이 바뀌지 않으므로 하위호환에 영향 없다.

--- 임계값 재조정(2026-09-14, Codex->Claude 인계 직후 메모리 원인 조사) ---
최초 50MB는 순전히 "디스크 여유" 기준으로 정했고, 그 시점엔 RSS/cgroup memory.high
압박이 관측 대상이 아니었다. 이번 조사에서 VM을 직접 재확인하니 autotrader.service의
MemoryCurrent가 MemoryHigh(1000MiB)에 사실상 붙어있었고(실측 여유 0.03%), 이 파일
자체도 47.2MB(22806줄, 평균 ~2.1KB/줄)까지 자라 다음 rotation을 목전에 두고 있었다.
web_app.py의 /api/... 대시보드 엔드포인트가 recent(limit=50)을 호출하는데, 실제
필요량(50줄 ≈ 105KB)에 비해 캐시 미스 1회당 재파싱량이 약 450배 과도하다. 디스크
여유가 아니라 "매 대시보드 새로고침마다의 재파싱 순간 할당량"이 지금의 실제 제약이므로
2MB로 낮춘다 - limit=50 기준 여전히 900줄 이상의 여유를 남기면서, 캐시 미스 1회당
최악의 재파싱량을 20x 이상 줄인다. MAX_ARCHIVES(8)는 그대로 둔다 - 최악 디스크
사용량도 (8+1)*2MB=18MB로 여전히 무시할 수준이다. recent()의 반환값은 이 변경으로
전혀 달라지지 않는다(회귀 테스트로 확인) - 회전 경계를 더 자주 넘을 뿐이다."""
import datetime
import glob
import json
import os

import jsonl_cache

# 근거는 위 모듈 docstring의 "임계값 재조정(2026-09-14)" 문단 참고 - 디스크 여유가
# 아니라 대시보드 새로고침마다의 재파싱 순간 메모리 할당량이 현재의 실제 제약이다.
MAX_BYTES_PER_FILE = 2 * 1024 * 1024
MAX_ARCHIVES = 8


def _log_path(user_dir: str) -> str:
    return os.path.join(user_dir, "market_structure_log.jsonl")


def _rotate_if_needed_locked(path: str) -> None:
    """호출부(record())가 이미 이 path의 락을 잡은 상태에서만 불러야 한다. 현재 파일이
    MAX_BYTES_PER_FILE 이상이면 타임스탬프 아카이브로 옮기고, MAX_ARCHIVES를 넘는
    오래된 아카이브는 지운다(기록 손실을 완전히 없앨 수는 없지만 최소화 - 최근
    아카이브는 항상 남겨둔다)."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return
    if size < MAX_BYTES_PER_FILE:
        return

    # os.rename()은 목적지가 이미 있으면 POSIX에서 조용히 덮어쓴다 - 시스템 클록
    # 해상도가 마이크로초보다 낮거나(일부 환경) 같은 순간에 연속 rotation이 몰리면
    # 타임스탬프만으로는 파일명이 겹칠 수 있어서(실제로 테스트에서 재현됨), 겹치면
    # 겹치지 않을 때까지 순번을 붙여 절대 덮어쓰지 않게 한다.
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    archive_path = f"{path}.{ts}"
    suffix = 0
    while os.path.exists(archive_path):
        suffix += 1
        archive_path = f"{path}.{ts}_{suffix}"
    try:
        os.rename(path, archive_path)
    except OSError:
        return  # rename 실패해도 다음 record()가 그냥 기존 파일에 이어쓴다(안전한 쪽으로)

    archives = sorted(glob.glob(f"{path}.*"))
    while len(archives) > MAX_ARCHIVES:
        oldest = archives.pop(0)
        try:
            os.remove(oldest)
        except OSError:
            pass


def record(
    user_dir: str,
    symbol: str,
    gemini_action: str | None,
    gemini_confidence: float | None,
    structures: dict,
    position_side: str | None = None,
    position_pnl_pct: float | None = None,
    engine_group: str = "core",
    variant: str | None = None,
    side: str | None = None,
    decision: str | None = None,
    extra_indicators: dict | None = None,
) -> None:
    """engine_group/variant/side/decision/extra_indicators는 삭제된 FAST(XRP/PI 60초
    스캘프 엔진)의 시장구조 관찰을 위해 추가됐던 필드다 - FAST 제거(2026-08-31) 후
    새 레코드를 쓰는 곳은 trader.py(CORE) 하나뿐이고, 이 함수를 그 기본값 그대로
    호출하므로 지금부터는 항상 engine_group="core", 나머지는 None으로만 기록된다.
    과거 FAST가 남긴 레코드(engine_group="fast", variant/side/decision 값 있음)를
    그대로 읽을 수 있도록 필드 자체는 스키마 호환을 위해 남겨뒀다."""
    os.makedirs(user_dir, exist_ok=True)
    path = _log_path(user_dir)
    line = json.dumps(
        {
            "symbol": symbol,
            "engine_group": engine_group,
            "gemini_action": gemini_action,
            "gemini_confidence": gemini_confidence,
            "position_side": position_side,
            "position_pnl_pct": position_pnl_pct,
            "variant": variant,
            "side": side,
            "decision": decision,
            "structures": structures,
            "extra_indicators": extra_indicators,
            "time": datetime.datetime.now().isoformat(timespec="seconds"),
        },
        ensure_ascii=False,
    ) + "\n"
    # rotate 여부 판단과 append를 이 파일의 read-path와 같은 락으로 직렬화한다 -
    # 여러 CORE 심볼 스레드가 동시에 써도 서로의 줄이 섞이거나 rotation 경계에서
    # 레코드가 반으로 쪼개지지 않는다.
    with jsonl_cache.get_path_lock(path):
        _rotate_if_needed_locked(path)
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)


def _archive_paths_newest_first(path: str) -> list[str]:
    """타임스탬프가 파일명에 그대로 들어있어(YYYYMMDD_HHMMSS_ffffff) 문자열 역순
    정렬이 곧 최신순 정렬이다."""
    return sorted(glob.glob(f"{path}.*"), reverse=True)


def recent(user_dir: str, limit: int = 100, engine_group: str | None = None) -> list:
    """최신순으로 최근 기록을 반환한다. engine_group을 주면 그 값으로 필터링한다
    ("core"/"fast"). 이 필드가 생기기 전의 레거시 레코드는 전부 CORE가 남긴 것이므로
    engine_group이 없으면 "core"로 취급한다(과거 기록을 잘못 재분류하지 않으면서도
    필터가 자연스럽게 동작하게 하기 위함).

    현재 파일부터 읽고, limit을 못 채웠으면(막 rotation이 일어난 직후 등) 최근
    아카이브까지 순서대로 이어서 읽는다 - "최근 데이터는 API에서 그대로 읽힘"을
    rotation 경계에서도 보장하기 위함.

    2026-09-14(ChatGPT 검토 지적) - 회전 임계값을 50MB->2MB로 낮췄지만, 그 전에
    이미 만들어진 최대 50MB짜리 아카이브가 남아있으면 이 폴백 경로가 그 파일
    전체를 한 번에 파싱해 캐시에 얹어(jsonl_cache.load_jsonl_cached) 원래
    문제와 같은 모양의 메모리 스파이크를 낼 수 있었다. 현재 파일(이미 2MB로
    제한됨)은 그대로 캐시 경로로 읽되, 아카이브는 tail_jsonl()로 끝에서부터
    필요한 만큼만 읽는다 - 원본 아카이브 파일은 전혀 삭제/수정하지 않는다.

    2026-09-14(ChatGPT 재검토 R4 지적, 직접 재현 확인) - engine_group 필터로
    일부가 걸러지면 고정폭(needed*3) 한 번만 읽어서는, 그 폭보다 더 뒤쪽에
    몰려 있는 매칭 레코드를 놓칠 수 있었다(재현: 한 아카이브의 앞쪽 10줄만
    'fast', 나머지 30줄이 'core'인 경우 recent(limit=1, engine_group='fast')가
    빈 리스트를 반환함 - 뒤쪽 3줄만 읽어서는 앞쪽의 'fast' 레코드에 닿지 못함).

    2026-09-14(ChatGPT v5 재검토 P2 지적, 직접 재현 확인) - 위 수정(요청 폭을
    3배 시작, 이후 4배씩 키움)은 매칭은 정확히 찾았지만, 매번 tail_jsonl()을
    "끝에서부터 처음부터 다시" 호출해 매칭이 희소한 큰 아카이브에서 파일 전체를
    여러 번(실측: 10000줄짜리에 매칭 1건만 앞쪽에 있을 때 10240/40960 두 번,
    즉 파일 전체를 두 번) 다시 파싱하는 순간 메모리 스파이크를 냈다(캐시에
    남지 않는 것과 "한 번의 읽기 순간 메모리"는 다른 문제). 이제 파일 끝에서부터
    고정 크기 청크로 뒤로 이동하며, 이미 읽은 바이트 구간은 절대 다시 읽지
    않는다(청크 경계에 걸친 앞쪽 조각만 다음 청크와 이어붙임) - 매칭이 필요한
    만큼 모이거나 파일 시작에 닿으면 그 자리에서 멈춘다. 매칭이 없거나 아주
    드물면 결국 파일 전체를 읽어야 할 수 있지만(더 뒤로 갈 데이터가 없으므로
    불가피함), 그래도 그 시점의 순간 메모리에는 청크 하나 분량만 남고 이미
    처리한 레코드만 누적 리스트에 쌓인다 - 전체를 한 번에 문자열/캐시로 들고
    있지 않는다.

    2026-09-14(ChatGPT v7 재검토 P2 지적, 직접 재현 확인) - 위 청크 경계
    처리가 문자 손실을 냈다: 청크를 읽자마자(아직 앞쪽이 멀티바이트 문자
    중간에서 잘렸을 수 있는 상태로) 통째로 decode(errors='ignore')부터 했고,
    그 잘린 "문자열"의 첫 줄을 다시 encode해 다음 청크로 넘겼다 - 이미
    decode 단계에서 조용히 버려진 바이트는 되살아나지 않는다(재현: 유효한
    UTF-8 JSONL을 한글 "한" 문자 정중앙에 64KB 청크 경계가 걸리게 만들면,
    그 레코드의 reason 필드가 빈 문자열로 돌아옴 - 순서/건수는 정상이라 ASCII
    데이터로는 놓칠 수 있음). 이제 청크 경계 처리를 바이트 단계에서 먼저
    끝낸다 - '\\n'(0x0A)은 ASCII라 UTF-8 멀티바이트 문자의 후속 바이트로는
    절대 나타나지 않으므로, 첫 '\\n' 바이트 위치를 기준으로 자르면 문자
    경계를 침범할 수 없다. 그 앞부분(pending)은 디코딩하지 않고 원본 바이트
    그대로 다음(더 앞쪽) 청크에 이어붙이고, '\\n' 뒤쪽만 그 청크 안에서
    decode한다."""
    path = _log_path(user_dir)
    matched: list = []

    def _filtered(raw):
        if engine_group is not None:
            return [r for r in raw if (r.get("engine_group") or "core") == engine_group]
        return raw

    def _archive_matches(archive_path: str, needed: int) -> list:
        try:
            size = os.path.getsize(archive_path)
        except OSError:
            return []
        if size == 0:
            return []
        chunk_size = 65536
        pos = size
        pending = b""  # 청크 경계에 걸쳐 아직 완전한 줄로 안 끝난 맨 앞 바이트
                        # 조각 - 디코딩 전(원본 바이트 그대로) 상태로만 들고 있는다.
        found: list = []
        with open(archive_path, "rb") as f:
            while pos > 0 and len(found) < needed:
                read_size = min(chunk_size, pos)
                pos -= read_size
                f.seek(pos)
                data = f.read(read_size) + pending
                if pos > 0:
                    # '\n' 바이트는 UTF-8 멀티바이트 문자의 연속 바이트로 절대
                    # 나타나지 않는다(연속 바이트는 항상 0x80 이상) - 그래서
                    # 이 위치를 기준으로 나누면 디코딩 전이라도 문자 경계를
                    # 안전하게 지킨다.
                    split_at = data.find(b"\n")
                    if split_at == -1:
                        # 이 청크 전체가 한 줄(의 일부)뿐 - 통째로 보류하고
                        # 더 앞쪽 청크를 마저 읽는다.
                        pending = data
                        continue
                    pending, data = data[:split_at], data[split_at + 1:]
                else:
                    pending = b""
                text = data.decode("utf-8", errors="ignore")
                lines = text.split("\n")
                for line in reversed(lines):  # 이 청크 안에서도 최신 것부터
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if engine_group is None or (record.get("engine_group") or "core") == engine_group:
                        found.append(record)
                        if len(found) >= needed:
                            break
        return found

    matched.extend(_filtered(list(reversed(jsonl_cache.load_jsonl_cached(path)))))
    if len(matched) < limit:
        for archive_path in _archive_paths_newest_first(path):
            still_needed = limit - len(matched)
            if still_needed <= 0:
                break
            matched.extend(_archive_matches(archive_path, still_needed))
    return matched[:limit]
