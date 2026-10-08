"""Phase 1.5 - LIVE(진행 중 캔들 포함) vs CLOSED(확정봉만) Shadow 비교 로그.
기존 market_structure_log.jsonl(Phase 1 뷰어가 읽는 스키마)과는 완전히 별개 파일이다 -
이 로그는 실거래 판단에 쓰이지 않고, 나중에 "확정봉만 썼으면 판단이 달라졌을 사례"를
분석하기 위한 재료로만 쓴다.

rotation(2026-08-27, 메모리 조사에서 추가) - trader.py가 매 CORE 사이클(300초, 심볼당)
record() 직후 recent(limit=10)을 호출하는데, 방금 append로 이 파일의 mtime/size가
바뀌었으니 jsonl_cache의 캐시가 매번 무효화되어 "파일 전체를 다시 파싱"이 매 사이클
반복된다. 이 파일은 회전 없이 계속 커지고 있었다(production 실측 8.5MB, 계속 증가 중) -
파일이 클수록 이 재파싱 비용(순간 할당량)도 계속 커지고, CPython이 해제된 메모리를
OS에 즉시 반환하지 않는 경향과 맞물려 RSS가 시간이 갈수록 우상향하는 원인 중 하나로
지목됐다. market_structure_log.py와 동일한 검증된 rotation을 적용해 이 재파싱 비용의
상한을 고정한다 - 호출 패턴(record 직후 recent) 자체는 CORE 로직이라 바꾸지 않는다.

임계값 재조정(2026-09-14, Codex->Claude 인계 직후 메모리 원인 조사) - 최초 도입 당시
50MB는 "디스크 여유(20GB 중 14GB)" 기준으로 정한 값이었고, 그 시점엔 실제 RSS/cgroup
memory.high 압박이 아직 관측되지 않았다. 이번 조사에서 VM을 직접 재확인한 결과
autotrader.service의 MemoryCurrent가 MemoryHigh(1000MiB)에 사실상 붙어있고(실측
1048268800/1048576000 byte, 여유 0.03%), 이 파일 자체도 현재 25.6MB(4356줄, 평균
~5.9KB/줄)까지 자라 있었다 - recent(limit=10)이 실제로 필요한 데이터(10줄 ≈ 59KB)에
비해 캐시 미스 1회당 재파싱량이 약 430배 과도하다. 디스크가 아니라 "매 사이클 재파싱
순간 할당량"이 지금의 진짜 제약이므로 2MB로 낮춘다 - limit=10 기준 여전히 250줄 이상의
여유(현재 파일 하나만으로도 사실상 항상 충분)를 남기면서, 캐시 미스 1회당 최악의
재파싱량을 25x 이상 줄인다. recent()의 반환값(내용/개수/순서)은 이 변경으로 전혀
달라지지 않는다 - 회전 경계를 더 자주 넘을 뿐, 여러 파일에 걸쳐 최신순으로 합치는
기존 로직이 그 경계를 이미 올바르게 처리한다(로컬에서 회귀 테스트로 확인)."""
import datetime
import glob
import json
import os

import jsonl_cache

# 근거는 위 모듈 docstring의 "임계값 재조정(2026-09-14)" 문단 참고 - 디스크 여유가
# 아니라 매 사이클 재파싱 순간 메모리 할당량이 현재의 실제 제약이다.
MAX_BYTES_PER_FILE = 2 * 1024 * 1024
MAX_ARCHIVES = 8


def _log_path(user_dir: str) -> str:
    return os.path.join(user_dir, "candle_finality_log.jsonl")


def _rotate_if_needed_locked(path: str) -> None:
    """market_structure_log._rotate_if_needed_locked와 완전히 동일한 패턴(이미 그쪽
    테스트에서 검증된 - 동시 rotation 파일명 충돌 방지 포함) 로직을 재사용한다."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return
    if size < MAX_BYTES_PER_FILE:
        return

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    archive_path = f"{path}.{ts}"
    suffix = 0
    while os.path.exists(archive_path):
        suffix += 1
        archive_path = f"{path}.{ts}_{suffix}"
    try:
        os.rename(path, archive_path)
    except OSError:
        return

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
    indicators_by_tf: dict,
    structure_by_tf: dict,
) -> None:
    """indicators_by_tf: {tf: {"live": {...}, "closed": {...}, "indicator_divergence": bool}}
    structure_by_tf: {tf: {"live": {...}, "closed": {...}, "structure_divergence": bool}}"""
    os.makedirs(user_dir, exist_ok=True)
    path = _log_path(user_dir)
    line = json.dumps(
        {
            "symbol": symbol,
            "gemini_action": gemini_action,
            "gemini_confidence": gemini_confidence,
            "indicators": indicators_by_tf,
            "structure": structure_by_tf,
            "time": datetime.datetime.now().isoformat(timespec="seconds"),
        },
        ensure_ascii=False,
    ) + "\n"
    with jsonl_cache.get_path_lock(path):
        _rotate_if_needed_locked(path)
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)


def _archive_paths_newest_first(path: str) -> list[str]:
    return sorted(glob.glob(f"{path}.*"), reverse=True)


def _load_all(user_dir: str) -> list:
    return jsonl_cache.load_jsonl_cached(_log_path(user_dir))


def recent(user_dir: str, limit: int = 100) -> list:
    """현재 파일은 회전 임계값(2MB)으로 이미 크기가 제한돼 있어 그대로 캐시
    경로로 읽는다. 아카이브는 불변이지만 임계값을 낮추기 전에 만들어진 최대
    50MB짜리가 남아있을 수 있어(2026-09-14 ChatGPT 검토 지적), 통째로 파싱해
    캐시에 얹지 않고 tail_jsonl()로 필요한 만큼만 끝에서부터 읽는다 - 기존
    아카이브 원본 파일 자체는 그대로 둔다(삭제/축소 없음)."""
    path = _log_path(user_dir)
    matched: list = []
    raw = list(reversed(jsonl_cache.load_jsonl_cached(path)))
    matched.extend(raw)
    if len(matched) < limit:
        for archive_path in _archive_paths_newest_first(path):
            still_needed = limit - len(matched)
            if still_needed <= 0:
                break
            raw = list(reversed(jsonl_cache.tail_jsonl(archive_path, still_needed)))
            matched.extend(raw)
    return matched[:limit]
