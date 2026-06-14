#!/usr/bin/env bash
set -euo pipefail

if [[ ! -f "manage.py" || ! -f "core/realestate_subscription.py" ]]; then
    echo "오류: ChickenBananaLab 프로젝트 최상단에서 실행해주세요."
    exit 1
fi

if [[ -z "${VIRTUAL_ENV:-}" && -f ".venv/bin/activate" ]]; then
    source ".venv/bin/activate"
fi

PYTHON_BIN="python"
command -v "$PYTHON_BIN" >/dev/null 2>&1 || PYTHON_BIN="python3"

STAMP="$(date +%Y%m%d_%H%M%S)"
BACKUP_DIR="_local_patch_backup/realestate_subscription_cache_${STAMP}"
mkdir -p "$BACKUP_DIR/core"
cp core/realestate_subscription.py "$BACKUP_DIR/core/realestate_subscription.py"

echo "백업 완료: $BACKUP_DIR"

"$PYTHON_BIN" - <<'PY'
from pathlib import Path
import re

path = Path("core/realestate_subscription.py")
text = path.read_text(encoding="utf-8")

text = text.replace(
    "from django.core.cache import cache",
    "from django.core.cache import caches",
)

text = re.sub(
    r"CACHE_SECONDS = 30 \* 60",
    '''CACHE_ALIAS = "external_api"
CACHE_SECONDS = 30 * 60
LAST_GOOD_SECONDS = 60 * 60 * 24
REFRESH_LOCK_SECONDS = 90''',
    text,
    count=1,
)

start = text.find("def get_latest_subscription_items(limit=8):")
if start == -1:
    raise SystemExit("get_latest_subscription_items() 함수를 찾지 못했습니다.")

new_function = r'''def get_latest_subscription_items(limit=8):
    cache = caches[CACHE_ALIAS]

    cache_key = f"cbl:applyhome:apt:v2:{limit}"
    last_good_key = f"cbl:applyhome:apt:last-good:v2:{limit}"
    refresh_lock_key = f"cbl:applyhome:apt:refresh-lock:v2:{limit}"

    cached = cache.get(cache_key)
    if isinstance(cached, dict):
        result = dict(cached)
        result["cached"] = True
        result["stale"] = False
        return result

    has_lock = cache.add(
        refresh_lock_key,
        timezone.now().isoformat(),
        REFRESH_LOCK_SECONDS,
    )

    if not has_lock:
        last_good = cache.get(last_good_key)
        if isinstance(last_good, dict):
            result = dict(last_good)
            result["cached"] = True
            result["stale"] = True
            result["error"] = ""
            result["message"] = "갱신 중이어서 마지막 정상 청약정보를 표시합니다."
            return result

    service_key = os.getenv("DATA_GO_KR_SERVICE_KEY", "").strip().strip('"').strip("'")

    if not service_key:
        if has_lock:
            cache.delete(refresh_lock_key)

        last_good = cache.get(last_good_key)
        if isinstance(last_good, dict):
            result = dict(last_good)
            result["cached"] = True
            result["stale"] = True
            result["error"] = ""
            result["message"] = "인증키를 확인하는 동안 마지막 정상 청약정보를 표시합니다."
            return result

        logger.warning("DATA_GO_KR_SERVICE_KEY가 설정되지 않았습니다.")
        return {
            "items": [],
            "error": "청약 정보를 준비 중입니다.",
            "message": "",
            "updated_at": "",
            "total_count": 0,
            "cached": False,
            "stale": False,
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
        seen_ids = set()

        for row in rows:
            house_manage_no = str(row.get("HOUSE_MANAGE_NO") or "").strip()
            pblanc_no = str(row.get("PBLANC_NO") or "").strip()
            unique_id = f"{house_manage_no}:{pblanc_no}"

            if unique_id != ":":
                if unique_id in seen_ids:
                    continue
                seen_ids.add(unique_id)

            reception_end = _parse_date(row.get("RCEPT_ENDDE"))

            if reception_end is None or reception_end >= cutoff:
                recent_rows.append(row)

        selected_rows = (recent_rows or rows)[:limit]
        items = [_normalize_item(row) for row in selected_rows]

        result = {
            "items": items,
            "error": "",
            "message": "최신 청약정보입니다.",
            "updated_at": timezone.localtime().strftime("%m.%d %H:%M"),
            "total_count": payload.get("totalCount") or 0,
            "cached": False,
            "stale": False,
        }

        cache.set(cache_key, result, CACHE_SECONDS)
        cache.set(last_good_key, result, LAST_GOOD_SECONDS)
        return result

    except Exception:
        logger.exception("청약홈 분양정보 API 호출 실패")

        last_good = cache.get(last_good_key)
        if isinstance(last_good, dict):
            result = dict(last_good)
            result["cached"] = True
            result["stale"] = True
            result["error"] = ""
            result["message"] = "공공데이터 연결이 지연되어 마지막 정상 청약정보를 표시합니다."
            return result

        return {
            "items": [],
            "error": "청약 정보를 잠시 불러오지 못했습니다.",
            "message": "",
            "updated_at": "",
            "total_count": 0,
            "cached": False,
            "stale": False,
        }

    finally:
        if has_lock:
            cache.delete(refresh_lock_key)
'''

text = text[:start] + new_function + "\n"
path.write_text(text, encoding="utf-8")

print("core/realestate_subscription.py 적용 완료")
PY

mkdir -p .django_cache/external_api

"$PYTHON_BIN" -m py_compile core/realestate_subscription.py
"$PYTHON_BIN" manage.py check

echo
echo "청약정보 안정화 패치 완료"
echo "- 30분 공유 캐시"
echo "- 24시간 마지막 정상 데이터 보관"
echo "- 동시 갱신 중복 호출 방지"
echo "- HOUSE_MANAGE_NO + PBLANC_NO 기준 중복 제거"
echo "- 외부 API 장애 시 마지막 정상 데이터 표시"
echo
echo "로컬 실행:"
echo "python manage.py runserver"
