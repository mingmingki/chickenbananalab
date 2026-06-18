import json
import re
from difflib import SequenceMatcher
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_STOP_WORDS = {"오늘", "최근", "관련", "기반", "서비스", "도입", "적용", "지원", "강화", "공개", "발표", "전면", "업무", "시장", "전망", "추진", "확대", "개편", "건설", "건축", "부동산", "금융", "테크", "일상", "뉴스", "기사"}
_TRACKING_PARAMS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "gclid", "fbclid", "nclick", "trackingcode", "from", "sid"}

def normalize_keyword(value):
    text = str(value or "").lower()
    text = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", text)
    text = re.sub(r"[^0-9a-z가-힣]+", " ", text)
    return "".join(text.split())

def normalize_source_url(value):
    value = str(value or "").strip()
    if not value:
        return ""
    try:
        parts = urlsplit(value)
        query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k.lower() not in _TRACKING_PARAMS]
        path = re.sub(r"/{2,}", "/", parts.path or "/").rstrip("/")
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(query), ""))
    except Exception:
        return value.lower().rstrip("/")

def keyword_tokens(value):
    text = str(value or "").lower()
    text = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", text)
    return {w for w in re.findall(r"[0-9a-z가-힣]+", text) if len(w) >= 2 and w not in _STOP_WORDS}

def is_similar_keyword(left, right):
    left_key, right_key = normalize_keyword(left), normalize_keyword(right)
    if not left_key or not right_key:
        return False
    if left_key == right_key:
        return True
    if min(len(left_key), len(right_key)) >= 12:
        shorter, longer = sorted((left_key, right_key), key=len)
        if shorter in longer and len(shorter) / len(longer) >= 0.72:
            return True
    if SequenceMatcher(None, left_key, right_key).ratio() >= 0.78:
        return True
    a, b = keyword_tokens(left), keyword_tokens(right)
    if a and b:
        overlap = len(a & b)
        union = len(a | b)
        if overlap >= 2 and (overlap / union >= 0.52 or overlap / min(len(a), len(b)) >= 0.72):
            return True
    return False

def unpack_recommendation(item, fallback_category_label=""):
    if isinstance(item, dict):
        return {
            "keyword": str(item.get("keyword", "") or "").strip(),
            "reason": str(item.get("reason", "") or "").strip(),
            "source_url": str(item.get("source_url", "") or "").strip(),
            "source": str(item.get("source", "") or "").strip(),
            "published_at": str(item.get("published_at", "") or "").strip(),
            "category_label": str(item.get("category", "") or fallback_category_label).strip(),
        }
    return {"keyword": str(item or "").strip(), "reason": "", "source_url": "", "source": "", "published_at": "", "category_label": fallback_category_label}

def is_duplicate_candidate(candidate, accepted):
    source_url = normalize_source_url(candidate.get("source_url"))
    for previous in accepted:
        previous_url = normalize_source_url(previous.get("source_url"))
        if source_url and previous_url and source_url == previous_url:
            return True
        if is_similar_keyword(candidate.get("keyword"), previous.get("keyword")):
            return True
    return False

def build_news_context(candidate):
    return json.dumps({k: candidate.get(k, "") for k in ("keyword", "reason", "source_url", "source", "published_at")}, ensure_ascii=False)

