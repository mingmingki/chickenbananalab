import logging
import os

from datetime import date, datetime, timedelta

import requests

from django.core.cache import cache
from django.utils import timezone


logger = logging.getLogger(__name__)

APPLYHOME_APT_API_URL = (
    "https://api.odcloud.kr/api/"
    "ApplyhomeInfoDetailSvc/v1/getAPTLttotPblancDetail"
)

CACHE_SECONDS = 30 * 60


def _parse_date(value):
    if not value:
        return None

    text = str(value).strip()

    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue

    return None


def _display_date(value):
    parsed = _parse_date(value)
    return parsed.strftime("%m.%d") if parsed else "-"


def _display_range(start_value, end_value):
    start = _parse_date(start_value)
    end = _parse_date(end_value)

    if not start and not end:
        return "-"

    if start and not end:
        return start.strftime("%m.%d")

    if end and not start:
        return end.strftime("%m.%d")

    if start == end:
        return start.strftime("%m.%d")

    return f"{start.strftime('%m.%d')}~{end.strftime('%m.%d')}"


def _multi_date_range(row, start_fields, end_fields):
    starts = [
        parsed
        for parsed in (_parse_date(row.get(field)) for field in start_fields)
        if parsed
    ]
    ends = [
        parsed
        for parsed in (_parse_date(row.get(field)) for field in end_fields)
        if parsed
    ]

    start = min(starts) if starts else None
    end = max(ends) if ends else None

    if not start and not end:
        return "-"

    if start and not end:
        return start.strftime("%m.%d")

    if end and not start:
        return end.strftime("%m.%d")

    if start == end:
        return start.strftime("%m.%d")

    return f"{start.strftime('%m.%d')}~{end.strftime('%m.%d')}"


def _format_count(value):
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "-"


def _safe_url(value):
    text = str(value or "").strip()

    if text.startswith("https://") or text.startswith("http://"):
        return text

    return ""


def _subscription_status(begin_value, end_value):
    today = timezone.localdate()
    begin = _parse_date(begin_value)
    end = _parse_date(end_value)

    if begin and today < begin:
        return "접수예정", "upcoming"

    if begin and end and begin <= today <= end:
        return "접수중", "open"

    if end and today > end:
        return "마감", "closed"

    return "공고중", "notice"


def _normalize_item(row):
    status, status_code = _subscription_status(
        row.get("RCEPT_BGNDE"),
        row.get("RCEPT_ENDDE"),
    )

    rank1 = _multi_date_range(
        row,
        (
            "GNRL_RNK1_CRSPAREA_RCPTDE",
            "GNRL_RNK1_ETC_GG_RCPTDE",
            "GNRL_RNK1_ETC_AREA_RCPTDE",
        ),
        (
            "GNRL_RNK1_CRSPAREA_ENDDE",
            "GNRL_RNK1_ETC_GG_ENDDE",
            "GNRL_RNK1_ETC_AREA_ENDDE",
        ),
    )

    rank2 = _multi_date_range(
        row,
        (
            "GNRL_RNK2_CRSPAREA_RCPTDE",
            "GNRL_RNK2_ETC_GG_RCPTDE",
            "GNRL_RNK2_ETC_AREA_RCPTDE",
        ),
        (
            "GNRL_RNK2_CRSPAREA_ENDDE",
            "GNRL_RNK2_ETC_GG_ENDDE",
            "GNRL_RNK2_ETC_AREA_ENDDE",
        ),
    )

    return {
        "name": str(row.get("HOUSE_NM") or "주택명 미등록").strip(),
        "region": str(row.get("SUBSCRPT_AREA_CODE_NM") or "-").strip(),
        "address": str(row.get("HSSPLY_ADRES") or "").strip(),
        "supply_count": _format_count(row.get("TOT_SUPLY_HSHLDCO")),
        "announcement": _display_date(row.get("RCRIT_PBLANC_DE")),
        "special": _display_range(
            row.get("SPSPLY_RCEPT_BGNDE"),
            row.get("SPSPLY_RCEPT_ENDDE"),
        ),
        "rank1": rank1,
        "rank2": rank2,
        "winner": _display_date(row.get("PRZWNER_PRESNATN_DE")),
        "status": status,
        "status_code": status_code,
        "url": _safe_url(row.get("PBLANC_URL")),
        "constructor": str(row.get("CNSTRCT_ENTRPS_NM") or "").strip(),
    }


def get_latest_subscription_items(limit=8):
    cache_key = f"cbl:applyhome:apt:v1:{limit}"
    cached = cache.get(cache_key)

    if cached is not None:
        return cached

    service_key = os.getenv("DATA_GO_KR_SERVICE_KEY", "").strip().strip('"').strip("'")

    if not service_key:
        logger.warning("DATA_GO_KR_SERVICE_KEY가 설정되지 않았습니다.")
        return {
            "items": [],
            "error": "청약 정보를 준비 중입니다.",
            "updated_at": "",
            "total_count": 0,
        }

    params = {
        "page": 1,
        "perPage": 30,
        "returnType": "JSON",
        "serviceKey": service_key,
    }

    headers = {
        "Accept": "application/json",
        "User-Agent": "ChickenBananaLab/1.0",
    }

    try:
        response = requests.get(
            APPLYHOME_APT_API_URL,
            params=params,
            headers=headers,
            timeout=(3.05, 6),
        )
        response.raise_for_status()

        payload = response.json()
        rows = payload.get("data") or []

        if not isinstance(rows, list):
            raise ValueError("청약 API data 형식이 올바르지 않습니다.")

        rows.sort(
            key=lambda row: (
                _parse_date(row.get("RCRIT_PBLANC_DE")) or date.min,
                str(row.get("HOUSE_MANAGE_NO") or ""),
            ),
            reverse=True,
        )

        cutoff = timezone.localdate() - timedelta(days=7)

        recent_rows = []
        for row in rows:
            reception_end = _parse_date(row.get("RCEPT_ENDDE"))

            if reception_end is None or reception_end >= cutoff:
                recent_rows.append(row)

        selected_rows = (recent_rows or rows)[:limit]
        items = [_normalize_item(row) for row in selected_rows]

        result = {
            "items": items,
            "error": "",
            "updated_at": timezone.localtime().strftime("%m.%d %H:%M"),
            "total_count": payload.get("totalCount") or 0,
        }

        cache.set(cache_key, result, CACHE_SECONDS)
        return result

    except Exception:
        logger.exception("청약홈 분양정보 API 호출 실패")

        return {
            "items": [],
            "error": "청약 정보를 잠시 불러오지 못했습니다.",
            "updated_at": "",
            "total_count": 0,
        }
