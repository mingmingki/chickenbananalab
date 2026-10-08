"""JSONL 로그 파일(trades_log/gpt_shadow_log/gpt_hold_audit_log/token_usage) 공용 캐시.

배경: 대시보드가 2초마다 /api/state, /api/trades, /api/shadow, /api/hold_audit,
/api/stats/periodic를 거의 동시에 호출하면, 각 요청이 매번 같은 파일을 처음부터 다시
읽고 파싱했다 - 새로고침처럼 여러 요청이 겹칠 때 순간적으로 메모리 사용량이 크게
튀는 원인 중 하나였다(개별 요청은 가볍지만 동시에 여러 개가 겹치면 peak working set이
커짐).

파일의 (mtime, size)가 마지막으로 읽었을 때와 같으면 캐시된 파싱 결과를 그대로
재사용하고, 바뀌었을 때만(즉 실제로 새 기록이 추가됐을 때만) 다시 읽는다. mtime만
비교하는 것보다 size까지 같이 보는 게 더 안전하다(드물지만 파일시스템의 mtime 해상도가
낮아 서로 다른 두 번의 append가 같은 mtime을 갖는 경우에도 size는 대개 달라진다).

단순 시간 기반 TTL과 달리 이 방식은 절대 stale한 데이터를 반환하지 않는다. 그리고
"짧은 시간에 여러 요청이 겹치면 파싱을 한 번으로 줄인다"는 목적을 실제로 보장하려면
캐시 미스를 확인하는 것과 실제로 파싱해서 캐시에 채워 넣는 것 사이가 원자적이어야
한다 - 그렇지 않으면 여러 스레드가 동시에 "미스"를 보고 각자 파일을 파싱해버려서
캐시가 있으나 마나 해진다. 그래서 경로별 락(get_path_lock)으로 "이 파일은 지금 딱
한 스레드만 파싱 중"을 보장하고, 락을 기다렸던 스레드들은 깨어난 뒤 다시 한 번 캐시를
확인해서(double-checked locking) 방금 채워진 결과를 그대로 재사용한다."""

import json
import os
import threading
from collections import OrderedDict

_meta_lock = threading.Lock()
# path -> ((mtime, size), parsed_records). OrderedDict라 삽입/재사용 순서를 알 수 있어
# LRU 방식으로 오래된 항목을 밀어낼 수 있다(아래 CACHE_MAX_ENTRIES 참고).
_cache: "OrderedDict[str, tuple[tuple[float, int], list]]" = OrderedDict()
_path_locks: dict[str, threading.Lock] = {}

# 메모리 조사에서 발견(2026-08-27) - market_structure_log.py/fast_live_log.py의
# candidate audit/candle_finality_log.py는 recent()가 부족분을 채우려고 rotation된
# 예전 아카이브 파일까지 이 캐시로 읽는다. 아카이브는 한 번 만들어지면 절대 내용이
# 바뀌지 않으므로(mtime/size 불변) 이 캐시에서 절대 무효화되지 않고, 아카이브가
# 쌓일수록(수 주~수개월 단위로) _cache 항목이 영원히 늘어나기만 하는 잠재적 무제한
# 증가 경로였다(다만 production 실측 시점엔 아직 어떤 파일도 rotation 임계값(50MB)에
# 도달하지 않아 이 경로 자체가 아직 실제로 트리거된 적은 없었다). 최근 사용된 경로
# CACHE_MAX_ENTRIES개만 남기고 밀어내는 단순 LRU 상한을 둬서, 캐시가 보유하는 최대
# 파싱 결과 총량 자체를 구조적으로 제한한다.
CACHE_MAX_ENTRIES = 64


def get_path_lock(path: str) -> threading.Lock:
    """경로별로 락을 하나씩 재사용한다 - 서로 다른 파일을 읽는 요청끼리는 여전히
    동시에 진행되고(락을 공유하지 않으므로), 같은 파일을 읽는 요청끼리만 파싱을
    직렬화한다. 계정 수만큼(파일 종류당 하나씩)만 늘어나므로 무한정 커지지 않는다.

    market_structure_log.py도 이 락을 그대로 재사용한다(rotation+append를 이 파일의
    read-path 파싱과 같은 락으로 직렬화 - CORE/FAST 스레드가 동시에 쓰거나, 쓰는
    도중에 다른 스레드가 읽어서 rotation 경계의 반쪽짜리 상태를 보는 경우를 막는다)."""
    with _meta_lock:
        lock = _path_locks.get(path)
        if lock is None:
            lock = threading.Lock()
            _path_locks[path] = lock
        return lock


def _stat_key(path: str) -> tuple[float, int] | None:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_mtime, st.st_size)


def _read_cached(path: str, key: tuple[float, int]) -> list | None:
    with _meta_lock:
        cached = _cache.get(path)
        if cached is not None and cached[0] == key:
            _cache.move_to_end(path)  # LRU - 최근 사용으로 표시
            # 얕은 복사본을 반환한다 - 호출부가 반환된 리스트를 정렬/필터링 등으로
            # 바꾸는 건 괜찮지만, 캐시에 저장된 원본 리스트 객체를 실수로 공유해서
            # 수정하는 사고를 막기 위함이다.
            return list(cached[1])
    return None


def load_jsonl_cached(path: str) -> list:
    """path를 한 줄씩 JSON으로 파싱한 리스트를 반환한다. 파일이 없으면 빈 리스트.
    (mtime, size)가 마지막 캐시와 같으면 디스크를 다시 읽지 않는다. 여러 스레드가
    동시에 같은(캐시 미스인) 파일을 요청해도 실제 파싱은 한 스레드만 하고 나머지는
    그 결과를 기다렸다가 재사용한다(single-flight)."""
    key = _stat_key(path)
    if key is None:
        return []

    hit = _read_cached(path, key)
    if hit is not None:
        return hit

    # 캐시 미스 - 이 파일에 대해서는 한 번에 한 스레드만 파싱하도록 경로별 락을 잡는다.
    with get_path_lock(path):
        # 락을 기다리는 동안 다른 스레드가 이미 파싱을 끝내고 캐시를 채웠을 수 있다
        # (그 사이 파일이 또 바뀌었을 수도 있으니 key도 다시 조회한다).
        key = _stat_key(path)
        if key is None:
            return []
        hit = _read_cached(path, key)
        if hit is not None:
            return hit

        records = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

        with _meta_lock:
            _cache[path] = (key, records)
            _cache.move_to_end(path)
            while len(_cache) > CACHE_MAX_ENTRIES:
                _cache.popitem(last=False)  # 가장 오래 안 쓰인 항목부터 제거(LRU)
        return list(records)


def tail_jsonl(path: str, count: int) -> list:
    """파일 전체를 파싱하거나 _cache에 보관하지 않고, 끝에서부터 최대 count줄만
    읽어 파싱한다(파일에 등장하는 원래 순서 그대로 반환 - 오래된 것이 앞).

    2026-09-14(ChatGPT 검토 지적) - candle_finality_log.py/market_structure_log.py의
    recent()이 회전 임계값(2MB)을 낮춘 뒤에도, "현재 파일에 limit을 못 채우면 다음
    아카이브를 통째로 읽어 채운다"는 폴백 경로에서 load_jsonl_cached()를 그대로
    썼다. 아카이브는 불변이라 한 번 파싱하면 다시 파싱되진 않지만(캐시가 있으니),
    "임계값을 낮추기 전에 이미 만들어진 최대 50MB짜리 오래된 아카이브"를 그
    "한 번"에 통째로 파싱하는 순간의 메모리 스파이크 자체가 원래 문제와 같은
    모양이다 - 그래서 아카이브는 이 함수로 끝부분만 읽는다. 기존 원본 파일은
    전혀 건드리거나 지우지 않는다(읽기 전용, 뒤에서부터 청크 단위로만 seek)."""
    if count <= 0:
        return []
    try:
        size = os.path.getsize(path)
    except OSError:
        return []
    if size == 0:
        return []
    chunk_size = 65536
    data = b""
    pos = size
    newline_count = 0
    with open(path, "rb") as f:
        while pos > 0 and newline_count <= count:
            read_size = min(chunk_size, pos)
            pos -= read_size
            f.seek(pos)
            data = f.read(read_size) + data
            newline_count = data.count(b"\n")
    text = data.decode("utf-8", errors="ignore")
    raw_lines = text.split("\n")
    if pos > 0 and raw_lines:
        # 파일 시작이 아닌 중간부터 읽었으므로 맨 앞 조각은 잘렸을 수 있다 - 버린다
        # (그 줄이 실제로 온전했더라도, 그 앞줄들은 어차피 필요한 count를 이미
        # 채우고 남는 여유분이므로 손실이 아니다).
        raw_lines = raw_lines[1:]
    records = []
    for line in raw_lines:
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records[-count:]
