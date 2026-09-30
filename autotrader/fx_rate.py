"""오늘 USD->KRW 환율 조회. 무료 공개 API(키 불필요)를 하루 1회만 호출하고
서버 메모리에 캐시한다 - 대시보드가 몇 초마다 폴링해도 매번 외부 호출하지 않는다."""
import datetime

import requests

_API_URL = "https://open.er-api.com/v6/latest/USD"
_TIMEOUT_SECONDS = 5

_cache = {"date": None, "rate": None}


def get_usd_krw_rate() -> float | None:
    today = datetime.date.today().isoformat()
    if _cache["date"] == today and _cache["rate"] is not None:
        return _cache["rate"]
    try:
        response = requests.get(_API_URL, timeout=_TIMEOUT_SECONDS)
        response.raise_for_status()
        rate = response.json().get("rates", {}).get("KRW")
        if rate:
            _cache["date"] = today
            _cache["rate"] = rate
        return rate
    except Exception:
        return _cache["rate"]
