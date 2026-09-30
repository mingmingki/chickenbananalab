"""Phase 2.1 항목3 - OKX 원본에 없는 10분봉을 확정 5분봉 2개로 합성한다(사용자 지시).

규칙(전부 사용자 지시 그대로):
- UTC 10분 경계 정렬 - open_time_ms % 10분 == 0인 5분봉만 짝의 "첫 번째"로 삼는다.
- 연속된 확정 5분봉 2개(첫 번째 + 그 다음 5분봉)가 "모두" confirm==1일 때만 생성.
- open=첫 봉 open, high=두 봉 high 최댓값, low=두 봉 low 최솟값, close=두 번째 봉
  close, volume=합계.
- 둘 중 하나라도 누락되거나 미확정이면 10분봉을 만들지 않는다(추측/보간 없음).
- "미래 5분봉이 완성되기 전에 노출 금지"는 구조적으로 자동 충족된다 - 애초에 두
  번째 5분봉이 데이터에 없으면(아직 안 왔으면) 매칭 자체가 안 돼서 생성되지 않는다."""
from __future__ import annotations

import market_data_store as mds

STEP_5M = mds.TIMEFRAME_STEP_MS["5m"]
STEP_10M = mds.TIMEFRAME_STEP_MS["10m"]


def _sum_or_none(a, b):
    if a is None or b is None:
        return None
    return a + b


def derive_10m_from_5m(five_min_rows: list[dict]) -> tuple[list[dict], dict]:
    """five_min_rows: 5분봉 리스트(정렬 여부 무관 - 내부에서 open_time_ms로 재정렬).
    반환: (10분봉 리스트, 통계{"candidate_pairs":, "generated":, "skipped_missing_second":,
    "skipped_unconfirmed":})."""
    by_ts = {r["open_time_ms"]: r for r in five_min_rows}
    result = []
    stats = {"candidate_pairs": 0, "generated": 0, "skipped_missing_second": 0, "skipped_unconfirmed": 0}

    for ts in sorted(by_ts):
        if ts % STEP_10M != 0:
            continue  # 10분 경계의 "두 번째" 자리 - 짝의 시작점으로 취급하지 않는다.
        stats["candidate_pairs"] += 1
        first = by_ts[ts]
        second = by_ts.get(ts + STEP_5M)
        if second is None:
            stats["skipped_missing_second"] += 1
            continue
        if first.get("confirm") != 1 or second.get("confirm") != 1:
            stats["skipped_unconfirmed"] += 1
            continue

        result.append({
            "inst_id": first["inst_id"], "timeframe": "10m", "price_type": first["price_type"],
            "open_time_ms": ts, "close_time_ms": ts + STEP_10M - 1,
            "open": first["open"], "high": max(first["high"], second["high"]),
            "low": min(first["low"], second["low"]), "close": second["close"],
            "volume_contracts": _sum_or_none(first.get("volume_contracts"), second.get("volume_contracts")),
            "volume_ccy": _sum_or_none(first.get("volume_ccy"), second.get("volume_ccy")),
            "volume_ccy_quote": _sum_or_none(first.get("volume_ccy_quote"), second.get("volume_ccy_quote")),
            "confirm": 1,
            "derived_from": "5m",
            "component_open_times_ms": [ts, ts + STEP_5M],
            "component_source_raw_sha256": [first.get("source_raw_sha256"), second.get("source_raw_sha256")],
            "metadata_version": first.get("metadata_version"),
            "collected_at_iso": max(first.get("collected_at_iso") or "", second.get("collected_at_iso") or ""),
        })
        stats["generated"] += 1

    return result, stats


def derived_10m_path(root_dir: str, inst_id: str, price_type: str) -> str:
    return mds.normalized_candles_path(root_dir, inst_id, "10m", price_type)


def load_derived_10m(root_dir: str, inst_id: str, price_type: str) -> list[dict]:
    return mds.load_normalized_candles(root_dir, inst_id, "10m", price_type)


def save_derived_10m_atomic(root_dir: str, inst_id: str, price_type: str, rows: list[dict]) -> str:
    return mds.save_normalized_candles_atomic(root_dir, inst_id, "10m", price_type, rows)


def build_and_save_10m(root_dir: str, inst_id: str, price_type: str, *, repo_dir: str | None = None) -> dict:
    """이미 저장된 5분봉 normalized 데이터로부터 10분봉을 다시 만들어 저장하고
    manifest를 남긴다. 항상 5분봉 전체로부터 재생성한다(멱등 - 몇 번을 다시
    돌려도 같은 5분봉 입력이면 같은 10분봉이 나온다)."""
    five_min_rows = mds.load_normalized_candles(root_dir, inst_id, "5m", price_type)
    derived_rows, stats = derive_10m_from_5m(five_min_rows)
    save_derived_10m_atomic(root_dir, inst_id, price_type, derived_rows)

    gap_ranges = mds.detect_candle_gaps(derived_rows, "10m")
    manifest = mds.build_manifest(
        dataset_name=f"candles_{price_type}_10m", inst_id=inst_id, row_count=len(derived_rows),
        first_ts_ms=derived_rows[0]["open_time_ms"] if derived_rows else None,
        last_ts_ms=derived_rows[-1]["open_time_ms"] if derived_rows else None,
        gap_ranges=gap_ranges, duplicate_count=0, excluded_unconfirmed_count=0,
        excluded_pre_listing_count=0, excluded_anomaly_count=0,
        raw_sha256_list=[], normalized_sha256=mds.sha256_hex(derived_rows),
        repo_dir=repo_dir,
        extra={"derived_from": "5m", "source_5m_row_count": len(five_min_rows), **stats},
    )
    mds.save_manifest_atomic(root_dir, inst_id, f"candles_{price_type}_10m", manifest)
    return {"row_count": len(derived_rows), "manifest": manifest, "stats": stats}
