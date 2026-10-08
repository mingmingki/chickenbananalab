"""Phase 2 - 공식 OKX 데이터 2계층 저장소(Raw / Normalized) + manifest.

계약서 요구사항 요약:
- Raw: endpoint/request_params/fetch_time/http코드/원본 payload/page cursor/SHA-256을
  그대로, 수정 없이, append-only로 보관한다.
- Normalized: 심볼/타임프레임/시가·종가 타임스탬프/OHLC/거래량/confirm 플래그/마크
  OHLC/펀딩 시각·비율/메타데이터 버전/원본 raw sha256을 표준화한 레코드로 보관한다.
- 각 데이터셋은 manifest(schema 버전, 수집 범위, 행 수, 첫/마지막 타임스탬프, 공백
  구간, 중복 개수, 제외된 미확정 캔들 개수, raw/normalized SHA-256, 코드 버전/워킹
  트리 지문)를 갖는다.
- raw 파일은 절대 덮어쓰지 않는다(항상 새 파일). normalized/manifest는 원자적으로
  갱신한다(임시파일 -> os.replace).

이 모듈 자체는 네트워크 호출을 하지 않는다 - okx_public_data가 가져온 응답을 저장/
정규화하는 순수 로직만 담당한다(계약 검증을 네트워크와 분리해서 테스트하기 위함)."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import uuid

SCHEMA_VERSION = 1

# OKX 캔들 bar 문자열 -> 밀리초 간격. "1D"는 사용자 지시에 따라 OKX의 UTC 기준
# bar("1Dutc")로만 수집한다 - 이 맵의 키는 "우리 내부 타임프레임 라벨"이고,
# 실제 OKX bar 파라미터로의 매핑은 OKX_BAR_PARAM에 따로 둔다(라벨과 거래소 파라미터를
# 혼동하지 않기 위해).
TIMEFRAME_STEP_MS = {
    "1m": 60 * 1000,
    "3m": 3 * 60 * 1000,
    "5m": 5 * 60 * 1000,
    "10m": 10 * 60 * 1000,  # OKX에 없는 파생 타임프레임 - 확정 5분봉 2개를 합성해서 만든다.
    "15m": 15 * 60 * 1000,
    "1H": 3600 * 1000,
    "4H": 4 * 3600 * 1000,
    "1D": 24 * 3600 * 1000,
}
# "10m"은 OKX 원본 bar가 아니므로(우리가 합성) 이 맵에 없다 - 실수로 거래소에
# "10m"을 요청하는 코드가 생기면 KeyError로 즉시 드러나게 한다.
OKX_BAR_PARAM = {
    "1m": "1m",
    "3m": "3m",
    "5m": "5m",
    "15m": "15m",
    "1H": "1H",
    "4H": "4H",
    "1D": "1Dutc",  # UTC 기준 - 절대 "1D"(UTC+8) 쓰지 않는다.
}


def sha256_hex(obj) -> str:
    canonical = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _atomic_write_json(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp_", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=True, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Raw 계층
# ---------------------------------------------------------------------------


def raw_dir(root_dir: str, inst_id: str, category: str) -> str:
    return os.path.join(root_dir, "raw", inst_id, category)


def write_raw_record(
    root_dir: str, *, inst_id: str, category: str, endpoint: str, request_params: dict,
    fetch_time_iso: str, http_status: int, api_code: str, raw_payload: dict,
    page_cursor: dict | None = None,
) -> dict:
    """raw fixture 한 건을 새 파일로 기록한다(절대 기존 파일을 덮어쓰지 않음 - 파일명에
    fetch_time+uuid를 섞어 충돌 가능성을 원천 차단한다). 임시파일 -> os.replace로
    원자적으로 쓴다(쓰다 만 파일이 실제 raw 데이터처럼 보이는 사고 방지)."""
    payload_sha256 = sha256_hex(raw_payload)
    record = {
        "endpoint": endpoint,
        "request_params": request_params,
        "fetch_time_iso": fetch_time_iso,
        "http_status": http_status,
        "api_code": api_code,
        "page_cursor": page_cursor or {},
        "sha256": payload_sha256,
        "raw_payload": raw_payload,
    }
    safe_ts = fetch_time_iso.replace(":", "").replace(".", "").replace("+", "")
    filename = f"{safe_ts}_{uuid.uuid4().hex[:8]}.json"
    path = os.path.join(raw_dir(root_dir, inst_id, category), filename)
    if os.path.exists(path):
        # 파일명에 uuid가 섞여 있으므로 사실상 도달 불가 - 그래도 raw 불변식(절대 덮어쓰기
        # 금지)을 코드로 방어한다.
        raise FileExistsError(f"raw 파일이 이미 존재함(덮어쓰기 금지): {path}")
    _atomic_write_json(path, record)
    return {"path": path, "sha256": payload_sha256}


# ---------------------------------------------------------------------------
# Normalized 계층 - 캔들
# ---------------------------------------------------------------------------


def normalized_candles_path(root_dir: str, inst_id: str, timeframe: str, price_type: str) -> str:
    return os.path.join(root_dir, "normalized", inst_id, f"candles_{price_type}_{timeframe}.json")


def load_normalized_candles(root_dir: str, inst_id: str, timeframe: str, price_type: str) -> list[dict]:
    path = normalized_candles_path(root_dir, inst_id, timeframe, price_type)
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def validate_candle_row(row: dict) -> tuple[bool, str | None]:
    """OHLC 이상치/음수 거래량을 걸러낸다. (유효여부, 사유) 튜플을 반환한다."""
    o, h, l, c = row["open"], row["high"], row["low"], row["close"]
    if h < max(o, c, l) - 1e-12:
        return False, "high_below_max_ohlc"
    if l > min(o, c, h) + 1e-12:
        return False, "low_above_min_ohlc"
    if h < l:
        return False, "high_below_low"
    for field in ("volume_contracts", "volume_ccy", "volume_ccy_quote"):
        v = row.get(field)
        if v is not None and v < 0:
            return False, f"negative_{field}"
    return True, None


def normalize_candle_rows(
    raw_data_rows: list[list], *, inst_id: str, timeframe: str, price_type: str,
    source_raw_sha256: str, metadata_version: str, collected_at_iso: str,
    listing_time_ms: int | None,
) -> tuple[list[dict], dict]:
    """OKX raw candle 배열([ts,o,h,l,c,(vol,volCcy,volCcyQuote,)confirm])을 정규화
    레코드 리스트로 변환한다. 상장 이전(listing_time_ms 미만) 데이터는 조용히 버린다
    (지어내지 않고, 애초에 존재할 수 없는 데이터이므로 제외). 이상치도 제외한다.

    반환: (정규화된 행 리스트, 통계 dict{"excluded_pre_listing":, "excluded_anomaly":})"""
    step_ms = TIMEFRAME_STEP_MS[timeframe]
    stats = {"excluded_pre_listing": 0, "excluded_anomaly": 0}
    rows = []
    for raw_row in raw_data_rows:
        open_time_ms = int(raw_row[0])
        o, h, l, c = float(raw_row[1]), float(raw_row[2]), float(raw_row[3]), float(raw_row[4])
        if price_type == "trade":
            vol_contracts = float(raw_row[5])
            vol_ccy = float(raw_row[6])
            vol_ccy_quote = float(raw_row[7])
            confirm = int(raw_row[8])
        else:  # mark: [ts,o,h,l,c,confirm] - 거래량 필드 자체가 없음
            vol_contracts = vol_ccy = vol_ccy_quote = None
            confirm = int(raw_row[5])

        if listing_time_ms is not None and open_time_ms < listing_time_ms:
            stats["excluded_pre_listing"] += 1
            continue

        row = {
            "inst_id": inst_id, "timeframe": timeframe, "price_type": price_type,
            "open_time_ms": open_time_ms, "close_time_ms": open_time_ms + step_ms - 1,
            "open": o, "high": h, "low": l, "close": c,
            "volume_contracts": vol_contracts, "volume_ccy": vol_ccy, "volume_ccy_quote": vol_ccy_quote,
            "confirm": confirm,
            "source_raw_sha256": source_raw_sha256, "metadata_version": metadata_version,
            "collected_at_iso": collected_at_iso,
        }
        ok, _reason = validate_candle_row(row)
        if not ok:
            stats["excluded_anomaly"] += 1
            continue
        rows.append(row)
    return rows, stats


def merge_normalized_candles(existing_rows: list[dict], new_rows: list[dict]) -> tuple[list[dict], int]:
    """open_time_ms로 중복 제거 후 오름차순 정렬한다. 같은 open_time_ms가 기존/신규
    양쪽에 있으면 신규(더 최근에 수집한 값)로 덮어쓴다 - 직전 사이클에서 미확정
    (confirm=0)으로 저장된 마지막 캔들이 다음 수집에서 확정된 값으로 갱신되는
    경우를 정확히 반영하기 위함. 반환: (병합된 행, 중복 개수)."""
    merged: dict[int, dict] = {row["open_time_ms"]: row for row in existing_rows}
    duplicate_count = 0
    for row in new_rows:
        if row["open_time_ms"] in merged:
            duplicate_count += 1
        merged[row["open_time_ms"]] = row
    ordered = [merged[k] for k in sorted(merged.keys())]
    return ordered, duplicate_count


def save_normalized_candles_atomic(root_dir: str, inst_id: str, timeframe: str, price_type: str, rows: list[dict]) -> str:
    path = normalized_candles_path(root_dir, inst_id, timeframe, price_type)
    _atomic_write_json(path, rows)
    return path


def detect_candle_gaps(sorted_rows: list[dict], timeframe: str) -> list[list[int]]:
    """정렬된(오름차순) 캔들 사이에 예상 간격(step_ms)보다 큰 공백이 있으면
    [직전_open_time_ms, 다음_open_time_ms] 쌍으로 기록한다."""
    step_ms = TIMEFRAME_STEP_MS[timeframe]
    gaps = []
    for prev, curr in zip(sorted_rows, sorted_rows[1:]):
        diff = curr["open_time_ms"] - prev["open_time_ms"]
        if diff > step_ms:
            gaps.append([prev["open_time_ms"], curr["open_time_ms"]])
    return gaps


# ---------------------------------------------------------------------------
# Normalized 계층 - 펀딩비
# ---------------------------------------------------------------------------


def normalized_funding_path(root_dir: str, inst_id: str) -> str:
    return os.path.join(root_dir, "normalized", inst_id, "funding_rate.json")


def load_normalized_funding(root_dir: str, inst_id: str) -> list[dict]:
    path = normalized_funding_path(root_dir, inst_id)
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def normalize_funding_rows(
    raw_data_rows: list[dict], *, inst_id: str, source_raw_sha256: str, collected_at_iso: str,
) -> list[dict]:
    """실제 정산값(realizedRate)을 우선 사용한다 - 계약서 지시. realizedRate가 없으면
    (아직 정산 전 예정값만 있는 경우) None으로 명시적으로 남기고 fundingRate로 대체
    추정하지 않는다 - "예측치와 실제 정산치를 섞지 않는다"는 요구사항."""
    rows = []
    for raw_row in raw_data_rows:
        realized = raw_row.get("realizedRate")
        rows.append({
            "inst_id": inst_id,
            "funding_time_ms": int(raw_row["fundingTime"]),
            "funding_rate": float(raw_row["fundingRate"]) if raw_row.get("fundingRate") not in (None, "") else None,
            "realized_rate": float(realized) if realized not in (None, "") else None,
            "method": raw_row.get("method"),
            "formula_type": raw_row.get("formulaType"),
            "source_raw_sha256": source_raw_sha256,
            "collected_at_iso": collected_at_iso,
        })
    return rows


def merge_normalized_funding(existing_rows: list[dict], new_rows: list[dict]) -> tuple[list[dict], int]:
    merged: dict[int, dict] = {row["funding_time_ms"]: row for row in existing_rows}
    duplicate_count = 0
    for row in new_rows:
        if row["funding_time_ms"] in merged:
            duplicate_count += 1
        merged[row["funding_time_ms"]] = row
    ordered = [merged[k] for k in sorted(merged.keys())]
    return ordered, duplicate_count


def save_normalized_funding_atomic(root_dir: str, inst_id: str, rows: list[dict]) -> str:
    path = normalized_funding_path(root_dir, inst_id)
    _atomic_write_json(path, rows)
    return path


def funding_interval_stats(sorted_rows: list[dict]) -> dict:
    """펀딩 정산 간격 통계 - 8시간 고정을 절대 가정하지 않는다(XRP/PI 등 가변 주기
    대응). 실제 관측된 fundingTime 간격의 min/median/max만 기록한다."""
    if len(sorted_rows) < 2:
        return {"min_gap_ms": None, "median_gap_ms": None, "max_gap_ms": None}
    diffs = sorted(
        curr["funding_time_ms"] - prev["funding_time_ms"]
        for prev, curr in zip(sorted_rows, sorted_rows[1:])
    )
    mid = len(diffs) // 2
    median = diffs[mid] if len(diffs) % 2 == 1 else (diffs[mid - 1] + diffs[mid]) / 2
    return {"min_gap_ms": diffs[0], "median_gap_ms": median, "max_gap_ms": diffs[-1]}


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


def git_fingerprint(repo_dir: str | None = None) -> dict:
    """코드 버전/워킹트리 지문 - git 명령 실패해도(git 없음/git repo 아님) 절대
    예외를 던지지 않고 "unknown"으로 fail-open한다(manifest 생성 자체가 이 정보
    하나 때문에 실패하면 안 됨)."""
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=repo_dir, stderr=subprocess.DEVNULL,
        ).decode().strip()
        dirty = subprocess.call(
            ["git", "diff", "--quiet", "HEAD"], cwd=repo_dir,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        ) != 0
        return {"commit": commit, "dirty": dirty}
    except Exception:
        return {"commit": "unknown", "dirty": None}


def build_manifest(
    *, dataset_name: str, inst_id: str, row_count: int, first_ts_ms: int | None, last_ts_ms: int | None,
    gap_ranges: list, duplicate_count: int, excluded_unconfirmed_count: int,
    excluded_pre_listing_count: int, excluded_anomaly_count: int,
    raw_sha256_list: list[str], normalized_sha256: str, repo_dir: str | None = None,
    extra: dict | None = None,
) -> dict:
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "dataset_name": dataset_name,
        "inst_id": inst_id,
        "row_count": row_count,
        "first_ts_ms": first_ts_ms,
        "last_ts_ms": last_ts_ms,
        "gap_ranges": gap_ranges,
        "duplicate_count": duplicate_count,
        "excluded_unconfirmed_count": excluded_unconfirmed_count,
        "excluded_pre_listing_count": excluded_pre_listing_count,
        "excluded_anomaly_count": excluded_anomaly_count,
        "raw_sha256_count": len(raw_sha256_list),
        "raw_sha256_combined": hashlib.sha256("".join(sorted(raw_sha256_list)).encode("utf-8")).hexdigest() if raw_sha256_list else None,
        "normalized_sha256": normalized_sha256,
        "code_version": git_fingerprint(repo_dir),
    }
    if extra:
        manifest["extra"] = extra
    return manifest


def manifest_path(root_dir: str, inst_id: str, dataset_name: str) -> str:
    return os.path.join(root_dir, "manifests", inst_id, f"{dataset_name}_manifest.json")


def save_manifest_atomic(root_dir: str, inst_id: str, dataset_name: str, manifest: dict) -> str:
    path = manifest_path(root_dir, inst_id, dataset_name)
    _atomic_write_json(path, manifest)
    return path
