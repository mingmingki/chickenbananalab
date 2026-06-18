import json
import re
from difflib import SequenceMatcher
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


_STOP_WORDS = {
    "오늘", "최근", "관련", "기반", "서비스", "도입", "적용", "지원", "강화",
    "공개", "발표", "전면", "업무", "시장", "전망", "추진", "확대", "개편",
    "건설", "건축", "부동산", "금융", "테크", "일상", "뉴스", "기사",
    "단독", "정책리뷰", "시황", "상보", "종합", "속보", "포토", "영상",
    "사상", "처음", "첫", "시대", "역사적", "고점", "돌파", "마감",
    "넘었다", "열렸다", "열어", "찍은", "선정", "우협", "운용사",
    "위탁운용사", "규모", "속도", "나서", "행", "원년", "과제",
}

_EVENT_NOISE = {
    "오늘", "최근", "이번", "관련", "기반", "서비스", "도입", "적용", "지원",
    "강화", "공개", "발표", "전면", "업무", "시장", "전망", "추진", "확대",
    "개편", "뉴스", "기사", "단독", "정책리뷰", "시황", "상보", "종합",
    "속보", "포토", "영상", "사상", "처음", "첫", "시대", "역사적", "고점",
    "돌파", "마감", "넘었다", "열렸다", "열어", "찍은", "선정", "우협",
    "운용사", "위탁운용사", "규모", "속도", "나서", "행", "원년", "과제",
    "대상", "통해", "위해", "대한", "에서", "으로", "하기로", "했다",
}

_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "gclid", "fbclid", "nclick", "trackingcode", "from", "sid",
}

_SYNONYM_PATTERNS = (
    (r"\b구천피\b", "코스피 9000"),
    (r"\b9천피\b", "코스피 9000"),
    (r"\b9000피\b", "코스피 9000"),
    (r"\b9,?000선\b", "9000"),
    (r"\b9,?000\b", "9000"),
    (r"\b5,?000억\b", "5000억"),
    (r"\b오천억\b", "5000억"),
    (r"\b인공지능\b", "ai"),
    (r"\b오픈\s*ai\b", "openai"),
)


def _clean_text(value):
    text = str(value or "").lower()
    text = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", text)

    for pattern, replacement in _SYNONYM_PATTERNS:
        text = re.sub(pattern, replacement, text, flags=re.I)

    # 숫자 뒤에 붙은 한국어 조사·의존 표현을 분리한다.
    # 예: 9000도 -> 9000, 9000선 -> 9000, 5000억은 -> 5000억
    text = re.sub(
        r"(?P<num>\d+(?:억|만|조)?)(?:선|대|도|은|는|이|가|을|를|에|의|로|으로|까지|부터|만)\b",
        r"\g<num>",
        text,
    )

    text = re.sub(r"[^0-9a-z가-힣]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_keyword(value):
    return "".join(_clean_text(value).split())


def normalize_source_url(value):
    value = str(value or "").strip()
    if not value:
        return ""
    try:
        parts = urlsplit(value)
        query = [
            (k, v)
            for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if k.lower() not in _TRACKING_PARAMS
        ]
        path = re.sub(r"/{2,}", "/", parts.path or "/").rstrip("/")
        return urlunsplit((
            parts.scheme.lower(),
            parts.netloc.lower(),
            path,
            urlencode(query),
            "",
        ))
    except Exception:
        return value.lower().rstrip("/")


def keyword_tokens(value):
    text = _clean_text(value)
    return {
        word
        for word in text.split()
        if len(word) >= 2 and word not in _STOP_WORDS
    }


def event_tokens(value):
    """
    기사 표현(돌파·마감·시대 등)은 버리고,
    사건을 식별하는 고유명사·수치·핵심 명사만 남긴다.
    """
    text = _clean_text(value)
    result = []

    for word in text.split():
        if len(word) < 2:
            continue
        if word in _EVENT_NOISE:
            continue
        result.append(word)

    return set(result)


def _contains_event_anchor(tokens, anchor_words):
    def has_anchor(anchor):
        if anchor in tokens:
            return True

        # 숫자 핵심어는 조사나 단위가 붙어도 같은 사건으로 본다.
        if anchor.isdigit():
            return any(
                token == anchor
                or token.startswith(anchor)
                for token in tokens
            )

        return False

    return all(has_anchor(word) for word in anchor_words)


def _special_same_event(left_tokens, right_tokens):
    # 코스피 9000선 돌파/마감/시대 기사는 동일 사건
    if (
        _contains_event_anchor(left_tokens, {"코스피", "9000"})
        and _contains_event_anchor(right_tokens, {"코스피", "9000"})
    ):
        return True

    # 동일 기관의 동일 금액 부동산 펀드/운용사 선정 기사
    common = left_tokens & right_tokens
    if "5000억" in common and "코람코" in common:
        owner_words = {"공무원연금", "우본", "우정사업본부", "국민연금"}
        left_owners = left_tokens & owner_words
        right_owners = right_tokens & owner_words

        # 기관이 같거나 한쪽 기사에서 기관명이 생략된 경우만 동일 사건 처리
        if not left_owners or not right_owners or left_owners == right_owners:
            return True

    return False


def is_same_event(left, right):
    left_tokens = event_tokens(left)
    right_tokens = event_tokens(right)

    if not left_tokens or not right_tokens:
        return False

    if _special_same_event(left_tokens, right_tokens):
        return True

    common = left_tokens & right_tokens
    union = left_tokens | right_tokens
    smaller = min(len(left_tokens), len(right_tokens))

    if not common or not union or not smaller:
        return False

    # 숫자 또는 영문/브랜드 고유명사가 겹치면 같은 사건일 가능성을 높인다.
    strong_common = {
        token for token in common
        if any(ch.isdigit() for ch in token)
        or re.search(r"[a-z]", token)
        or len(token) >= 4
    }

    coverage = len(common) / smaller
    jaccard = len(common) / len(union)

    if len(strong_common) >= 2 and coverage >= 0.66:
        return True

    if len(common) >= 3 and coverage >= 0.72:
        return True

    if len(common) >= 4 and jaccard >= 0.48:
        return True

    return False


def is_similar_keyword(left, right):
    left_key = normalize_keyword(left)
    right_key = normalize_keyword(right)

    if not left_key or not right_key:
        return False

    if left_key == right_key:
        return True

    if is_same_event(left, right):
        return True

    if min(len(left_key), len(right_key)) >= 12:
        shorter, longer = sorted((left_key, right_key), key=len)
        if shorter in longer and len(shorter) / len(longer) >= 0.68:
            return True

    if SequenceMatcher(None, left_key, right_key).ratio() >= 0.74:
        return True

    left_words = keyword_tokens(left)
    right_words = keyword_tokens(right)

    if left_words and right_words:
        overlap = len(left_words & right_words)
        union = len(left_words | right_words)
        smaller = min(len(left_words), len(right_words))

        if overlap >= 2 and (
            overlap / union >= 0.46
            or overlap / smaller >= 0.68
        ):
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
            "category_label": str(
                item.get("category", "") or fallback_category_label
            ).strip(),
        }

    return {
        "keyword": str(item or "").strip(),
        "reason": "",
        "source_url": "",
        "source": "",
        "published_at": "",
        "category_label": fallback_category_label,
    }


def is_duplicate_candidate(candidate, accepted):
    source_url = normalize_source_url(candidate.get("source_url"))

    for previous in accepted:
        previous_url = normalize_source_url(previous.get("source_url"))

        if source_url and previous_url and source_url == previous_url:
            return True

        if is_similar_keyword(
            candidate.get("keyword"),
            previous.get("keyword"),
        ):
            return True

    return False


def build_news_context(candidate):
    return json.dumps(
        {
            key: candidate.get(key, "")
            for key in (
                "keyword",
                "reason",
                "source_url",
                "source",
                "published_at",
            )
        },
        ensure_ascii=False,
    )
