#!/usr/bin/env bash
set -euo pipefail

if [[ ! -f "manage.py" || ! -d "core" ]]; then
    echo "오류: ChickenBananaLab 프로젝트 최상단에서 실행해주세요."
    exit 1
fi

if [[ -z "${VIRTUAL_ENV:-}" && -f ".venv/bin/activate" ]]; then
    source ".venv/bin/activate"
fi

PYTHON_BIN="python"
command -v "$PYTHON_BIN" >/dev/null 2>&1 || PYTHON_BIN="python3"

STAMP="$(date +%Y%m%d_%H%M%S)"
BACKUP_DIR="_local_patch_backup/api_cache_safety_${STAMP}"
mkdir -p "$BACKUP_DIR/config" "$BACKUP_DIR/core"

for file in config/settings.py core/crypto_market.py core/subscription_data.py; do
    [[ -f "$file" ]] && cp "$file" "$BACKUP_DIR/$file"
done

echo "백업 완료: $BACKUP_DIR"

"$PYTHON_BIN" - <<'PY'
from pathlib import Path
import re

settings_path = Path("config/settings.py")
crypto_path = Path("core/crypto_market.py")
subscription_path = Path("core/subscription_data.py")

for path in (settings_path, crypto_path, subscription_path):
    if not path.exists():
        raise SystemExit(f"필수 파일을 찾지 못했습니다: {path}")

settings = settings_path.read_text(encoding="utf-8")
start = "# CBL_EXTERNAL_API_SHARED_CACHE_START"
end = "# CBL_EXTERNAL_API_SHARED_CACHE_END"
block = '''# CBL_EXTERNAL_API_SHARED_CACHE_START
_CBL_EXTERNAL_API_CACHE_DIR = BASE_DIR / ".django_cache" / "external_api"

if "CACHES" not in globals():
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "cbl-default-cache",
        }
    }

CACHES["external_api"] = {
    "BACKEND": "django.core.cache.backends.filebased.FileBasedCache",
    "LOCATION": str(_CBL_EXTERNAL_API_CACHE_DIR),
    "TIMEOUT": 300,
    "OPTIONS": {
        "MAX_ENTRIES": 1000,
        "CULL_FREQUENCY": 3,
    },
}
# CBL_EXTERNAL_API_SHARED_CACHE_END'''

if start in settings and end in settings:
    settings = re.sub(
        rf"{re.escape(start)}.*?{re.escape(end)}",
        block,
        settings,
        flags=re.S,
    )
else:
    settings = settings.rstrip() + "\n\n" + block + "\n"

settings_path.write_text(settings, encoding="utf-8")

crypto = crypto_path.read_text(encoding="utf-8")
crypto = crypto.replace("from django.core.cache import cache", "from django.core.cache import caches")
crypto = re.sub(
    r'CACHE_KEY\s*=\s*"[^"]+"\s*\nCACHE_SECONDS\s*=\s*30\s*\nREQUEST_TIMEOUT\s*=\s*5',
    '''CACHE_ALIAS = "external_api"
CACHE_KEY = "cbl_crypto_market_v2"
LAST_GOOD_CACHE_KEY = "cbl_crypto_market_last_good_v2"
REFRESH_LOCK_KEY = "cbl_crypto_market_refresh_lock_v2"
CACHE_SECONDS = 30
LAST_GOOD_SECONDS = 60 * 60 * 6
REFRESH_LOCK_SECONDS = 12
REQUEST_TIMEOUT = 5''',
    crypto,
    count=1,
)

pattern = re.compile(
    r'@require_GET\s*\ndef crypto_market_api\(request\):.*?\n\s*return JsonResponse\(\s*payload,\s*json_dumps_params=\{"ensure_ascii": False\},\s*\)\s*',
    re.S,
)

replacement = '''@require_GET
def crypto_market_api(request):
    cache = caches[CACHE_ALIAS]

    cached = cache.get(CACHE_KEY)
    if isinstance(cached, dict):
        result = dict(cached)
        result["cached"] = True
        result["stale"] = False
        return JsonResponse(result, json_dumps_params={"ensure_ascii": False})

    has_lock = cache.add(
        REFRESH_LOCK_KEY,
        timezone.now().isoformat(),
        REFRESH_LOCK_SECONDS,
    )

    if not has_lock:
        last_good = cache.get(LAST_GOOD_CACHE_KEY)
        if isinstance(last_good, dict):
            result = dict(last_good)
            result["cached"] = True
            result["stale"] = True
            result["message"] = "갱신 중이어서 마지막 정상 시세를 표시합니다."
            return JsonResponse(result, json_dumps_params={"ensure_ascii": False})

    try:
        payload = _load_market()

        if payload.get("ok"):
            payload["cached"] = False
            payload["stale"] = False
            payload["message"] = "최신 시세입니다."
            cache.set(CACHE_KEY, payload, CACHE_SECONDS)
            cache.set(LAST_GOOD_CACHE_KEY, payload, LAST_GOOD_SECONDS)
            return JsonResponse(payload, json_dumps_params={"ensure_ascii": False})

        last_good = cache.get(LAST_GOOD_CACHE_KEY)
        if isinstance(last_good, dict):
            result = dict(last_good)
            result["cached"] = True
            result["stale"] = True
            result["message"] = "거래소 연결이 지연되어 마지막 정상 시세를 표시합니다."
            return JsonResponse(result, json_dumps_params={"ensure_ascii": False})

        payload["cached"] = False
        payload["stale"] = False
        payload["message"] = "시세를 불러오지 못했습니다."
        cache.set(CACHE_KEY, payload, 10)
        return JsonResponse(payload, status=503, json_dumps_params={"ensure_ascii": False})

    except Exception as error:
        last_good = cache.get(LAST_GOOD_CACHE_KEY)
        if isinstance(last_good, dict):
            result = dict(last_good)
            result["cached"] = True
            result["stale"] = True
            result["message"] = "외부 시세 서버 장애로 마지막 정상 시세를 표시합니다."
            result["refresh_error"] = str(error)[:160]
            return JsonResponse(result, json_dumps_params={"ensure_ascii": False})

        return JsonResponse(
            {
                "ok": False,
                "cached": False,
                "stale": False,
                "cache_seconds": CACHE_SECONDS,
                "updated_at": timezone.localtime().strftime("%Y-%m-%d %H:%M:%S"),
                "message": "시세 정보를 일시적으로 불러올 수 없습니다.",
                "error": str(error)[:160],
                "exchanges": [],
            },
            status=503,
            json_dumps_params={"ensure_ascii": False},
        )

    finally:
        if has_lock:
            cache.delete(REFRESH_LOCK_KEY)
'''

if not pattern.search(crypto):
    raise SystemExit("core/crypto_market.py의 crypto_market_api 함수를 찾지 못했습니다.")

crypto = pattern.sub(replacement, crypto, count=1)
crypto_path.write_text(crypto, encoding="utf-8")

subscription = subscription_path.read_text(encoding="utf-8")
subscription = subscription.replace("from django.core.cache import cache", "from django.core.cache import caches")
subscription = re.sub(
    r'CACHE_KEY\s*=\s*"[^"]+"\s*\nCACHE_SECONDS\s*=\s*30 \* 60\s*\nFAILURE_CACHE_SECONDS\s*=\s*3 \* 60\s*\nREQUEST_TIMEOUT\s*=\s*12',
    '''CACHE_ALIAS = "external_api"
CACHE_KEY = "cbl:subscription-board:v4"
LAST_GOOD_CACHE_KEY = "cbl:subscription-board:last-good:v4"
REFRESH_LOCK_KEY = "cbl:subscription-board:refresh-lock:v4"
CACHE_SECONDS = 30 * 60
LAST_GOOD_SECONDS = 60 * 60 * 24
FAILURE_CACHE_SECONDS = 3 * 60
REFRESH_LOCK_SECONDS = 90
REQUEST_TIMEOUT = 12''',
    subscription,
    count=1,
)

old_head = '''def get_subscription_payload(force_refresh: bool = False) -> dict[str, Any]:
    if not force_refresh:
        cached = cache.get(CACHE_KEY)
        if isinstance(cached, dict):
            cached = dict(cached)
            cached["cached"] = True
            return cached
'''

new_head = '''def get_subscription_payload(force_refresh: bool = False) -> dict[str, Any]:
    cache = caches[CACHE_ALIAS]

    if not force_refresh:
        cached = cache.get(CACHE_KEY)
        if isinstance(cached, dict):
            cached = dict(cached)
            cached["cached"] = True
            cached["stale"] = False
            return cached

    has_lock = cache.add(
        REFRESH_LOCK_KEY,
        timezone.now().isoformat(),
        REFRESH_LOCK_SECONDS,
    )

    if not has_lock and not force_refresh:
        last_good = cache.get(LAST_GOOD_CACHE_KEY)
        if isinstance(last_good, dict):
            last_good = dict(last_good)
            last_good["cached"] = True
            last_good["stale"] = True
            last_good["message"] = "갱신 중이어서 마지막 정상 청약정보를 표시합니다."
            return last_good
'''

if old_head not in subscription:
    raise SystemExit("청약 캐시 함수 시작부를 찾지 못했습니다.")
subscription = subscription.replace(old_head, new_head, 1)

subscription = subscription.replace(
    '''    if not service_key:
        return {
''',
    '''    if not service_key:
        if has_lock:
            cache.delete(REFRESH_LOCK_KEY)
        return {
''',
    1,
)

old_tail = '''    cache.set(
        CACHE_KEY,
        payload,
        CACHE_SECONDS if success_count > 0 else FAILURE_CACHE_SECONDS,
    )

    return payload
'''

new_tail = '''    if success_count > 0:
        payload["stale"] = False
        cache.set(CACHE_KEY, payload, CACHE_SECONDS)
        cache.set(LAST_GOOD_CACHE_KEY, payload, LAST_GOOD_SECONDS)
    else:
        last_good = cache.get(LAST_GOOD_CACHE_KEY)
        if isinstance(last_good, dict):
            fallback = dict(last_good)
            fallback["cached"] = True
            fallback["stale"] = True
            fallback["message"] = "공공데이터 연결이 지연되어 마지막 정상 청약정보를 표시합니다."
            fallback["errors"] = errors
            if has_lock:
                cache.delete(REFRESH_LOCK_KEY)
            return fallback

        payload["stale"] = False
        cache.set(CACHE_KEY, payload, FAILURE_CACHE_SECONDS)

    if has_lock:
        cache.delete(REFRESH_LOCK_KEY)

    return payload
'''

if old_tail not in subscription:
    raise SystemExit("청약 캐시 함수 마지막 구간을 찾지 못했습니다.")
subscription = subscription.replace(old_tail, new_tail, 1)
subscription_path.write_text(subscription, encoding="utf-8")

print("공유 캐시 및 장애 대비 코드 적용 완료")
PY

mkdir -p .django_cache/external_api

"$PYTHON_BIN" -m py_compile core/crypto_market.py core/subscription_data.py
"$PYTHON_BIN" manage.py check

echo
echo "패치 완료"
echo "- 시세: 30초 공유 캐시"
echo "- 청약: 30분 공유 캐시"
echo "- 외부 API 장애: 마지막 정상 데이터 표시"
echo "- 동시 접속: refresh lock으로 중복 호출 방지"
echo "- 청약: DB 저장을 하지 않으므로 DB 중복 데이터 없음"
echo
echo "로컬 실행:"
echo "python manage.py runserver"
