import json
import re
import requests
from html import unescape
from django.conf import settings


CATEGORY_SEARCH_WORDS = {
    "architecture": [
        "건축",
        "건설 현장",
        "아파트 하자",
        "재건축",
        "공사비",
        "건축자재",
        "인테리어",
        "리모델링",
        "건설 안전",
    ],
    "realestate": [
        "부동산",
        "부동산 정책",
        "아파트 분양",
        "청약",
        "전세",
        "주택담보대출",
        "재건축",
        "부동산 시장",
    ],
    "finance": [
        "금리",
        "주식 시장",
        "환율",
        "코스피",
        "비트코인",
        "미국 증시",
        "경제 전망",
    ],
    "tech": [
        "AI",
        "챗GPT",
        "반도체",
        "아이폰",
        "맥북",
        "구글",
        "네이버",
        "전기차",
    ],
    "life": [
        "주말 나들이",
        "육아",
        "맛집",
        "여행",
        "생활정보",
        "건강관리",
        "가족 여행",
    ],
}

CATEGORY_LABELS = {
    "architecture": "건축",
    "realestate": "부동산",
    "finance": "금융",
    "tech": "테크",
    "life": "일상",
}

CATEGORY_ALIASES = {
    "all": "tech",
    "건축": "architecture",
    "부동산": "realestate",
    "금융": "finance",
    "테크": "tech",
    "일상": "life",
    "생활": "life",
}

RECOMMENDATION_LIMIT = 10


BAD_KEYWORDS = {
    "있다", "없다", "한다", "했다", "됐다", "된다", "위해", "통해", "대한", "관련",
    "오늘", "내일", "올해", "내년", "지난", "이번", "최근", "최신", "속보", "단독",
    "종합", "기자", "뉴스", "사진", "영상", "오전", "오후", "가능", "확인",
    "함께", "우리", "사회", "문화", "기억", "회장", "후보", "국힘", "민주",
    "선거", "대선", "정치", "국회", "대표", "대통령", "제공", "무단", "전재",

    # 자극적인 뉴스 제목 표현 제거
    "흔들었다", "흔든다", "흔들까", "뒤흔든", "충격", "파격", "논란", "대박",
    "난리", "역대급", "초비상", "비상", "공포", "경악", "발칵", "술렁",
    "왜", "무슨", "무엇", "진짜", "정말", "설마",
}

SENSATIONAL_WORDS = [
    "흔들었다", "흔든다", "흔들까", "뒤흔든", "충격", "파격", "논란", "대박",
    "난리", "역대급", "초비상", "경악", "발칵", "술렁", "설마", "진짜",
]

RUMOR_WORDS = [
    "루머", "소문", "예상", "가능성", "유출", "추정", "미확인",
]


def normalize_category(category):
    category = (category or "tech").strip()
    return CATEGORY_ALIASES.get(category, category)


def clean_html(text):
    if not text:
        return ""

    text = unescape(str(text))
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("&quot;", '"').replace("&amp;", "&")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def clean_title(title):
    title = clean_html(title)

    title = re.sub(r"\[[^\]]+\]", "", title)
    title = re.sub(r"\([^)]*\)", "", title)
    title = title.replace("...", " ")
    title = title.replace("…", " ")
    title = title.replace("\"", "")
    title = title.replace("'", "")
    title = title.replace("“", "")
    title = title.replace("”", "")
    title = title.replace("‘", "")
    title = title.replace("’", "")
    title = re.sub(r"[|·•]", " ", title)
    title = re.sub(r"\s+", " ", title)

    return title.strip()


def compact_text(value):
    return re.sub(r"\s+", "", str(value or "").lower())


def has_any(text, words):
    compact = compact_text(text)
    return any(compact_text(word) in compact for word in words)


def fetch_naver_news(query, display=10):
    client_id = getattr(settings, "NAVER_CLIENT_ID", "")
    client_secret = getattr(settings, "NAVER_CLIENT_SECRET", "")

    if not client_id or not client_secret:
        raise RuntimeError("NAVER_CLIENT_ID 또는 NAVER_CLIENT_SECRET 설정이 비어 있습니다.")

    url = "https://openapi.naver.com/v1/search/news.json"

    headers = {
        "X-Naver-Client-Id": client_id,
        "X-Naver-Client-Secret": client_secret,
    }

    params = {
        "query": query,
        "display": display,
        "start": 1,
        "sort": "date",
    }

    response = requests.get(url, headers=headers, params=params, timeout=10)
    response.raise_for_status()

    data = response.json()
    results = []

    for item in data.get("items", []):
        title = clean_title(item.get("title", ""))
        description = clean_html(item.get("description", ""))
        link = item.get("originallink") or item.get("link") or ""

        if not title:
            continue

        results.append({
            "title": title,
            "description": description,
            "link": link,
            "query": query,
            "pubDate": item.get("pubDate", ""),
        })

    return results


def is_bad_title(title, category):
    title = title or ""
    compact = title.replace(" ", "")

    if category in ["architecture", "realestate", "life"]:
        political_words = ["대선", "선거", "후보", "국힘", "민주", "대통령", "국회", "정당"]
        if any(word in compact for word in political_words):
            return True

    if len(title) < 6:
        return True

    return False


def make_special_blog_keyword(title, query, category):
    """
    뉴스 제목을 그대로 쓰면 자극적인 카드가 되므로,
    특정 주제는 블로그용 검색 키워드로 안전하게 바꾼다.
    """
    title = clean_title(title)
    query = clean_title(query)
    text = f"{title} {query}"
    compact = compact_text(text)

    # MacBook Neo / 599달러 맥북 이슈
    if (
        "맥북" in compact or "macbook" in compact
    ) and (
        "599" in compact or "네오" in compact or "neo" in compact or "저가형" in compact or "보급형" in compact
    ):
        return "MacBook Neo 공식 출시, MacBook Air M4와 비교"

    # 일반 맥북 에어 비교
    if ("맥북에어" in compact or "macbookair" in compact) and ("m4" in compact or "m5" in compact):
        if "m4" in compact:
            return "MacBook Air M4 구매 전 확인할 점"
        if "m5" in compact:
            return "MacBook Air M5 구매 전 확인할 점"

    # 아이폰 이슈
    if ("아이폰" in compact or "iphone" in compact) and ("출시" in compact or "가격" in compact or "스펙" in compact):
        return "아이폰 최신 모델 가격과 스펙 정리"

    # AI / ChatGPT 이슈
    if ("챗gpt" in compact or "chatgpt" in compact or "openai" in compact) and category == "tech":
        return "ChatGPT 최신 기능과 활용 방법"

    return ""


def make_blog_keyword(title, query, category="tech"):
    special_keyword = make_special_blog_keyword(title, query, category)

    if special_keyword:
        return special_keyword

    title = clean_title(title)

    title = re.split(r"[:?！!]", title)[0].strip()
    words = title.split()

    filtered = []

    for word in words:
        clean_word = re.sub(r"[^가-힣A-Za-z0-9]", "", word)

        if not clean_word:
            continue

        if clean_word in BAD_KEYWORDS:
            continue

        if len(clean_word) <= 1:
            continue

        # 자극적인 표현 제거
        if has_any(clean_word, SENSATIONAL_WORDS):
            continue

        # 루머성 표현 제거
        if has_any(clean_word, RUMOR_WORDS):
            continue

        filtered.append(clean_word)

    if not filtered:
        return query

    keyword = " ".join(filtered[:5]).strip()

    keyword = re.sub(r"\s+", " ", keyword).strip()

    if len(keyword) > 40:
        keyword = keyword[:40].strip()

    if len(keyword) < 3:
        keyword = query

    return keyword


def score_news_item(item, category):
    title = item.get("title", "")
    description = item.get("description", "")
    query = item.get("query", "")

    title_compact = title.replace(" ", "")
    desc_compact = description.replace(" ", "")

    score = 0

    if query and query.replace(" ", "") in title_compact:
        score += 5

    category_words = CATEGORY_SEARCH_WORDS.get(category, [])

    for word in category_words:
        word_compact = word.replace(" ", "")

        if word_compact in title_compact:
            score += 3

        if word_compact in desc_compact:
            score += 1

    # 자극적인 기사 제목은 추천 우선순위 낮춤
    if has_any(title, SENSATIONAL_WORDS):
        score -= 3

    if has_any(title, RUMOR_WORDS):
        score -= 2

    if len(title) > 60:
        score -= 1

    return score


def make_recommend_reason(item, keyword):
    title = item.get("title", "")
    query = item.get("query", "")

    text = f"{title} {query}"
    compact = compact_text(text)

    if (
        "맥북" in compact or "macbook" in compact
    ) and (
        "599" in compact or "네오" in compact or "neo" in compact or "저가형" in compact or "보급형" in compact
    ):
        return "MacBook 관련 최신 뉴스 흐름입니다. 공식 제품 정보와 최신 MacBook Air 기준으로 확인해 글감으로 사용하세요."

    if has_any(title, SENSATIONAL_WORDS) or has_any(title, RUMOR_WORDS):
        return "최근 뉴스 흐름을 블로그용 키워드로 정리했습니다. 공식 자료 확인 후 작성하는 것을 권장합니다."

    return f"관련 뉴스 흐름: {clean_title(title)[:48]}..."


def make_default_recommendations(category, search_words, reason):
    category_label = CATEGORY_LABELS.get(category, "테크")

    recommendations = []

    for word in search_words[:RECOMMENDATION_LIMIT]:
        recommendations.append({
            "category": category_label,
            "keyword": word,
            "reason": reason,
        })

    return recommendations


def recommend_keywords_from_news(category):
    category = normalize_category(category)

    if category not in CATEGORY_SEARCH_WORDS:
        category = "tech"

    search_words = CATEGORY_SEARCH_WORDS.get(category, CATEGORY_SEARCH_WORDS["tech"])
    category_label = CATEGORY_LABELS.get(category, "테크")

    all_news = []
    seen_titles = set()
    errors = []

    for query in search_words:
        try:
            news_items = fetch_naver_news(query, display=10)
        except Exception as e:
            errors.append(f"{query}: {e}")
            continue

        for item in news_items:
            title = item.get("title", "")
            title_key = re.sub(r"\s+", "", title.lower())

            if not title_key:
                continue

            if title_key in seen_titles:
                continue

            if is_bad_title(title, category):
                continue

            seen_titles.add(title_key)
            all_news.append(item)

    if not all_news:
        if errors:
            print("[NAVER_KEYWORD_ERROR]", " / ".join(errors[:5]))

        return make_default_recommendations(
            category,
            search_words,
            "네이버 뉴스 결과를 불러오지 못해 카테고리 기본 키워드를 추천했습니다.",
        )

    all_news.sort(key=lambda item: score_news_item(item, category), reverse=True)

    recommendations = []
    used_keywords = set()

    for item in all_news:
        if len(recommendations) >= RECOMMENDATION_LIMIT:
            break

        keyword = make_blog_keyword(
            item.get("title", ""),
            item.get("query", ""),
            category=category,
        )

        keyword_key = keyword.replace(" ", "").lower()

        if not keyword:
            continue

        if keyword_key in used_keywords:
            continue

        if keyword in BAD_KEYWORDS:
            continue

        if len(keyword) < 3:
            continue

        used_keywords.add(keyword_key)

        recommendations.append({
            "category": category_label,
            "keyword": keyword,
            "reason": make_recommend_reason(item, keyword),
        })

    for word in search_words:
        if len(recommendations) >= RECOMMENDATION_LIMIT:
            break

        word_key = word.replace(" ", "").lower()

        if word_key in used_keywords:
            continue

        used_keywords.add(word_key)

        recommendations.append({
            "category": category_label,
            "keyword": word,
            "reason": "오늘 카테고리 뉴스 흐름을 기준으로 추천한 글감입니다.",
        })

    return recommendations[:RECOMMENDATION_LIMIT]

# CBL_KEYWORD_PLAIN_FORMAT_V22_START

import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from django.core.cache import cache as _cbl_cache
from django.utils import timezone as _cbl_timezone


RECOMMENDATION_LIMIT = 5

_CBL_V22_BUNDLE_KEY = "cbl:today-keywords:v22:bundle"
_CBL_V22_STALE_KEY = "cbl:today-keywords:v22:stale"
_CBL_V22_LOCK_KEY = "cbl:today-keywords:v22:lock"

_CBL_V22_CACHE_SECONDS = 6 * 60 * 60
_CBL_V22_STALE_SECONDS = 24 * 60 * 60
_CBL_V22_LOCK_SECONDS = 180

_CBL_V22_CATEGORIES = {
    "architecture": {
        "label": "건축",
        "guide": "건축, 건설, 시공, 공사비, 건설안전, 건축자재, 인테리어, 리모델링, BIM, Revit, Dynamo, 스마트건설, 건설자동화, 건설로봇",
    },
    "realestate": {
        "label": "부동산",
        "guide": "부동산, 아파트, 청약, 분양, 전세, 월세, 주택시장, 재건축, 재개발, 부동산 정책",
    },
    "finance": {
        "label": "금융",
        "guide": "금리, 환율, 은행, 대출, 주식, 코스피, 코스닥, 채권, 가상자산, 비트코인, 주요 경제지표",
    },
    "tech": {
        "label": "테크",
        "guide": "AI, ChatGPT, 생성형 AI, 반도체, 소프트웨어, 클라우드, 보안, 로봇, 스마트폰, IT 기업 공식 발표",
    },
    "life": {
        "label": "일상",
        "guide": "육아, 교육, 가족여행, 생활정보, 복지, 지원제도, 지역축제, 건강생활, 문화, 어린이 체험",
    },
}

_CBL_V22_GROUPS = [
    ["architecture", "realestate", "finance"],
    ["tech", "life"],
]


def _cbl_v22_response_text(response):
    text = getattr(response, "text", None)

    if text:
        return str(text).strip()

    pieces = []

    for candidate in getattr(response, "candidates", []) or []:
        content = getattr(candidate, "content", None)

        for part in getattr(content, "parts", []) or []:
            part_text = getattr(part, "text", None)

            if part_text:
                pieces.append(str(part_text))

    return "\n".join(pieces).strip()


def _cbl_v22_empty_bundle():
    return {
        category: []
        for category in _CBL_V22_CATEGORIES
    }


def _cbl_v22_clean_keyword(value):
    value = clean_title(value)
    value = re.sub(
        r"^(속보|단독|종합|영상|포토)\s*",
        "",
        value,
        flags=re.I,
    )
    return re.sub(r"\s+", " ", value).strip(" ,.-·|:")


def _cbl_v22_build_prompt(group, retry=False):
    now_text = _cbl_timezone.localtime().strftime(
        "%Y-%m-%d %H:%M %Z"
    )

    guides = "\n".join(
        (
            f"- {category}: "
            f"{_CBL_V22_CATEGORIES[category]['label']} / "
            f"{_CBL_V22_CATEGORIES[category]['guide']}"
        )
        for category in group
    )

    retry_rule = ""

    if retry:
        retry_rule = (
            "\n이전 응답 형식이 잘못되었습니다. "
            "이번에는 반드시 지정된 탭 구분 행만 출력하세요."
        )

    lines = [
        "너는 치킨바나나랩 블로그의 최신 키워드 편집자다.",
        "",
        f"현재 시각: {now_text}",
        retry_rule,
        "",
        "Google Search를 사용해 오늘부터 최근 72시간 이내에 실제로 발표되거나 보도된 내용을 검색한다.",
        "아래 카테고리별로 신빙성 있는 최신 블로그 주제를 정확히 5개씩 선정한다.",
        "",
        "카테고리:",
        guides,
        "",
        "검증 기준:",
        "1. 정부·공공기관·기업 공식 홈페이지·공식 뉴스룸·공시를 우선한다.",
        "2. 공식 자료가 없으면 독립적인 주요 언론 2곳 이상에서 핵심 내용이 일치해야 한다.",
        "3. 최근 72시간 밖의 내용은 제외한다.",
        "4. 루머, 익명 주장, 커뮤니티, SNS 추측은 제외한다.",
        "5. 정치 공방, 연예인 사생활, 범죄 자극 기사, 투자 선동은 제외한다.",
        "6. 계획·검토·추진·예정을 확정된 사실처럼 바꾸지 않는다.",
        "7. 기사 제목을 그대로 복사하거나 문장 중간에서 자르지 않는다.",
        "8. 키워드는 15~42자의 자연스럽고 완결된 한국어 주제로 작성한다.",
        "9. 카테고리 간 같은 사건을 중복 추천하지 않는다.",
        "10. 출처가 없는 일반 상식형 키워드는 만들지 않는다.",
        "",
        "출력 규칙:",
        "설명문, JSON, 코드블록, 번호 목록을 출력하지 않는다.",
        "각 결과를 반드시 한 줄로 출력한다.",
        "각 줄은 탭 문자로 정확히 4칸을 구분한다.",
        "형식: category<TAB>keyword<TAB>reason<TAB>source_url",
        "category는 architecture, realestate, finance, tech, life 중 현재 요청된 값만 사용한다.",
        "keyword, reason 안에는 탭과 줄바꿈을 넣지 않는다.",
        "source_url은 실제 확인한 http 또는 https URL 한 개를 넣는다.",
        "",
        "출력 예시:",
        "tech\t오픈AI 기업용 서비스 업데이트 핵심 변화\t오픈AI 공식 발표에서 새 기능 확인\thttps://example.com",
    ]

    return "\n".join(line for line in lines if line is not None).strip()


def _cbl_v22_parse_lines(raw_text, group):
    group_set = set(group)
    parsed = {
        category: []
        for category in group
    }
    seen = set()

    forbidden = [
        "충격",
        "대박",
        "역대급",
        "폭등",
        "급등 전망",
        "루머",
        "소문",
        "미확인",
        "사생활",
        "각방",
        "대통령",
        "정당",
        "국회의원",
    ]

    for raw_line in str(raw_text or "").splitlines():
        line = raw_line.strip()

        if not line:
            continue

        line = re.sub(r"^[-*•]\s*", "", line)

        parts = line.split("\t", 3)

        if len(parts) != 4:
            continue

        category = parts[0].strip().lower()
        keyword = _cbl_v22_clean_keyword(parts[1])
        reason = clean_html(parts[2])[:90]
        source_url = parts[3].strip()

        if category not in group_set:
            continue

        if not 10 <= len(keyword) <= 48:
            continue

        if any(word in keyword for word in forbidden):
            continue

        if not source_url.startswith(("http://", "https://")):
            continue

        if not reason:
            continue

        key = compact_text(keyword)

        if not key or key in seen:
            continue

        parsed[category].append({
            "category": _CBL_V22_CATEGORIES[category]["label"],
            "keyword": keyword,
            "reason": reason,
        })

        seen.add(key)

        if all(
            len(parsed[item_category]) >= RECOMMENDATION_LIMIT
            for item_category in group
        ):
            break

    return parsed


def _cbl_v22_generate_group(group, retry=False):
    from dotenv import load_dotenv
    from google import genai
    from google.genai import types

    load_dotenv(override=True)

    api_key = os.getenv("GEMINI_API_KEY", "").strip()

    if not api_key:
        raise ValueError("GEMINI_API_KEY가 없습니다.")

    model_name = (
        os.getenv("GEMINI_KEYWORD_SEARCH_MODEL", "").strip()
        or "gemini-2.5-flash"
    )

    client = genai.Client(api_key=api_key)

    response = client.models.generate_content(
        model=model_name,
        contents=_cbl_v22_build_prompt(
            group,
            retry=retry,
        ),
        config=types.GenerateContentConfig(
            tools=[
                types.Tool(
                    google_search=types.GoogleSearch()
                )
            ],
            temperature=0.0,
            max_output_tokens=8192,
        ),
    )

    parsed = _cbl_v22_parse_lines(
        _cbl_v22_response_text(response),
        group,
    )

    missing = [
        category
        for category in group
        if len(parsed.get(category, [])) < 2
    ]

    if missing and not retry:
        print(
            "[KEYWORD_V22_GROUP_RETRY]",
            f"group={','.join(group)}",
            f"missing={','.join(missing)}",
        )
        return _cbl_v22_generate_group(
            group,
            retry=True,
        )

    print(
        "[KEYWORD_V22_GROUP]",
        f"group={','.join(group)}",
        "counts=" + ",".join(
            f"{category}:{len(parsed.get(category, []))}"
            for category in group
        ),
    )

    return parsed


def _cbl_v22_generate_bundle():
    bundle = _cbl_v22_empty_bundle()
    group_results = {}

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {
            executor.submit(
                _cbl_v22_generate_group,
                group,
            ): group
            for group in _CBL_V22_GROUPS
        }

        for future in as_completed(futures):
            group = futures[future]

            try:
                group_results[tuple(group)] = future.result()

            except Exception as error:
                print(
                    "[KEYWORD_V22_GROUP_ERROR]",
                    f"group={','.join(group)}",
                    f"error={type(error).__name__}: {error}",
                )
                group_results[tuple(group)] = {
                    category: []
                    for category in group
                }

    for group in _CBL_V22_GROUPS:
        result = group_results.get(
            tuple(group),
            {},
        )

        for category in group:
            bundle[category] = result.get(
                category,
                [],
            )[:RECOMMENDATION_LIMIT]

    counts = {
        category: len(items)
        for category, items in bundle.items()
    }

    missing = [
        category
        for category, count in counts.items()
        if count == 0
    ]

    if missing:
        raise ValueError(
            "검색 결과가 비어 있는 카테고리: "
            + ", ".join(missing)
        )

    print(
        "[KEYWORD_V22_GENERATED]",
        "counts=" + ",".join(
            f"{category}:{count}"
            for category, count in counts.items()
        ),
    )

    return bundle


def _cbl_v22_get_bundle():
    cached = _cbl_cache.get(
        _CBL_V22_BUNDLE_KEY
    )

    if isinstance(cached, dict):
        print("[KEYWORD_V22_CACHE_HIT]")
        return cached

    lock_acquired = _cbl_cache.add(
        _CBL_V22_LOCK_KEY,
        "1",
        _CBL_V22_LOCK_SECONDS,
    )

    if lock_acquired:
        try:
            bundle = _cbl_v22_generate_bundle()

            _cbl_cache.set(
                _CBL_V22_BUNDLE_KEY,
                bundle,
                _CBL_V22_CACHE_SECONDS,
            )

            _cbl_cache.set(
                _CBL_V22_STALE_KEY,
                bundle,
                _CBL_V22_STALE_SECONDS,
            )

            print("[KEYWORD_V22_BUNDLE_SUCCESS]")

            return bundle

        except Exception as error:
            print(
                "[KEYWORD_V22_ERROR]",
                f"error={type(error).__name__}: {error}",
            )

        finally:
            _cbl_cache.delete(
                _CBL_V22_LOCK_KEY
            )

    else:
        for _ in range(120):
            time.sleep(0.5)

            cached = _cbl_cache.get(
                _CBL_V22_BUNDLE_KEY
            )

            if isinstance(cached, dict):
                print(
                    "[KEYWORD_V22_SHARED_RESULT]"
                )
                return cached

            if not _cbl_cache.get(
                _CBL_V22_LOCK_KEY
            ):
                break

    stale = _cbl_cache.get(
        _CBL_V22_STALE_KEY
    )

    if isinstance(stale, dict):
        print("[KEYWORD_V22_STALE]")
        return stale

    return _cbl_v22_empty_bundle()


def recommend_keywords_from_news(category):
    category = normalize_category(category)

    if category not in _CBL_V22_CATEGORIES:
        category = "tech"

    bundle = _cbl_v22_get_bundle()
    items = bundle.get(category, [])

    if not isinstance(items, list):
        return []

    print(
        "[KEYWORD_V22_CATEGORY]",
        f"category={category}",
        f"items={len(items)}",
    )

    return items[:RECOMMENDATION_LIMIT]


# CBL_KEYWORD_PLAIN_FORMAT_V22_END

# CBL_NAVER_ONLY_KEYWORDS_V24_START
#
# 오늘의 키워드 V24.2
# - 네이버 뉴스 API만 사용
# - 검색어 풀은 카테고리별 약 30개
# - 실제 호출은 핵심 2개 + 날짜별 순환 3개
# - 제목 기준 카테고리 판정
# - 429 방지를 위한 전역 호출 간격 및 재시도
# - 카테고리별 5개 반환
#

import html as _cbl_v24_html
import json as _cbl_v24_json
import random as _cbl_v24_random
import re as _cbl_v24_re
import threading as _cbl_v24_threading
import time as _cbl_v24_time
from datetime import timedelta as _cbl_v24_timedelta
from email.utils import parsedate_to_datetime as _cbl_v24_parse_email_date
from urllib.error import HTTPError as _cbl_v24_HTTPError
from urllib.parse import quote as _cbl_v24_quote
from urllib.parse import urlparse as _cbl_v24_urlparse
from urllib.request import Request as _cbl_v24_Request
from urllib.request import urlopen as _cbl_v24_urlopen

from django.conf import settings as _cbl_v24_settings
from django.core.cache import cache as _cbl_v24_cache
from django.utils import timezone as _cbl_v24_timezone


_CBL_V24_LIMIT = 5
_CBL_V24_QUERY_COUNT = 5
_CBL_V24_CACHE_SECONDS = 30 * 60
_CBL_V24_STALE_SECONDS = 24 * 60 * 60
_CBL_V24_MIN_REQUEST_INTERVAL = 0.65

_CBL_V24_REQUEST_LOCK = _cbl_v24_threading.Lock()
_CBL_V24_LAST_REQUEST_AT = 0.0

_CBL_V24_LABELS = {
    "architecture": "건축",
    "realestate": "부동산",
    "finance": "금융",
    "tech": "테크",
    "life": "일상",
}

CATEGORY_SEARCH_WORDS = {
    "architecture": [
        "건축",
        "건설",
        "건설 현장",
        "건축 설계",
        "건축자재",
        "건설 안전",
        "건설사",
        "건설 수주",
        "공사비",
        "공사 원가",
        "공사 기간",
        "아파트 하자",
        "건축물 안전",
        "건설 품질",
        "건설 사고",
        "스마트 건설",
        "건설 자동화",
        "건설 로봇",
        "BIM",
        "레빗",
        "다이나모",
        "모듈러 건축",
        "프리패브",
        "제로에너지 건축",
        "친환경 건축",
        "인테리어",
        "리모델링",
        "건축법",
        "중대재해 건설",
        "건설 자재 가격",
    ],
    "realestate": [
        "부동산",
        "아파트",
        "부동산 정책",
        "부동산 시장",
        "아파트 분양",
        "아파트 매매",
        "아파트 전세",
        "청약",
        "청약 일정",
        "미분양",
        "전세",
        "월세",
        "전세 가격",
        "집값",
        "주택 가격",
        "주택 공급",
        "주택담보대출",
        "재건축",
        "재개발",
        "정비사업",
        "오피스텔",
        "상업용 부동산",
        "토지",
        "공시가격",
        "보유세",
        "취득세",
        "양도소득세",
        "전세사기",
        "역전세",
        "부동산 규제",
    ],
    "finance": [
        "금리",
        "미국 증시",
        "기준금리",
        "한국은행",
        "미국 금리",
        "FOMC",
        "환율",
        "원달러 환율",
        "주식 시장",
        "국내 증시",
        "뉴욕 증시",
        "코스피",
        "코스닥",
        "나스닥",
        "S&P500",
        "증권사",
        "은행",
        "신용대출",
        "주택담보대출 금리",
        "채권",
        "국채",
        "ETF",
        "배당주",
        "반도체 주식",
        "비트코인",
        "이더리움",
        "가상자산",
        "금 가격",
        "국제 유가",
        "경제 전망",
    ],
    "tech": [
        "AI",
        "반도체",
        "인공지능",
        "생성형 AI",
        "챗GPT",
        "제미나이",
        "클로드 AI",
        "AI 에이전트",
        "AI 반도체",
        "GPU",
        "HBM",
        "삼성전자 반도체",
        "SK하이닉스",
        "클라우드",
        "데이터센터",
        "사이버 보안",
        "개인정보 보안",
        "랜섬웨어",
        "소프트웨어",
        "앱",
        "로봇",
        "휴머노이드 로봇",
        "자율주행",
        "전기차",
        "배터리",
        "아이폰",
        "맥북",
        "애플",
        "구글",
        "네이버",
        "카카오",
        "마이크로소프트",
        "스페이스X",
    ],
    "life": [
        "생활정보",
        "육아",
        "정부 지원금",
        "생활 지원금",
        "실업급여",
        "육아휴직",
        "출산 지원",
        "아동수당",
        "교육",
        "초등학생 교육",
        "건강관리",
        "건강검진",
        "다이어트",
        "수면 건강",
        "가족 건강",
        "주말 나들이",
        "가족 여행",
        "국내 여행",
        "해외 여행",
        "여행 할인",
        "맛집",
        "음식",
        "요리",
        "생활비 절약",
        "전기요금",
        "교통비",
        "가전제품",
        "청소",
        "해충 퇴치",
        "날씨",
    ],
}

CATEGORY_CORE_WORDS = {
    "architecture": ["건설", "건축"],
    "realestate": ["부동산", "아파트"],
    "finance": ["금리", "미국 증시"],
    "tech": ["AI", "반도체"],
    "life": ["생활정보", "육아"],
}

_CBL_V24_TITLE_REQUIRED = {
    "architecture": [
        "건설", "건축", "공사", "시공", "건설사", "건설업",
        "bim", "레빗", "다이나모", "인테리어", "설계",
        "건설로봇", "건설 로봇", "건설자동화", "건설 자동화",
        "건축물", "안전관리", "건축자재", "건설자재",
        "공사비", "모듈러", "프리패브", "제로에너지",
        "친환경 건축", "리모델링", "건축법", "중대재해",
        "아파트 하자",
    ],
    "realestate": [
        "부동산", "아파트", "주택", "청약", "분양", "전세",
        "월세", "재건축", "재개발", "오피스텔", "매매",
        "정비사업", "용적률", "공시가격", "집값", "토지",
        "미분양", "역전세", "전세사기", "보유세", "취득세",
        "양도소득세", "주택공급", "주택 공급",
    ],
    "finance": [
        "금융", "금리", "기준금리", "한국은행", "주식",
        "증시", "코스피", "코스닥", "나스닥", "s&p500",
        "환율", "원달러", "은행", "채권", "국채",
        "비트코인", "이더리움", "가상자산", "대출",
        "fomc", "뉴욕증시", "뉴욕 증시", "유가",
        "신용대출", "배당", "펀드", "etf", "금 가격",
        "경제 전망",
    ],
    "tech": [
        "ai", "인공지능", "생성형 ai", "챗gpt", "chatgpt",
        "제미나이", "클로드", "ai 에이전트", "반도체",
        "gpu", "hbm", "클라우드", "데이터센터", "보안",
        "랜섬웨어", "소프트웨어", "로봇", "휴머노이드",
        "자율주행", "전기차", "배터리", "아이폰", "맥북",
        "애플", "구글", "네이버", "카카오", "마이크로소프트",
        "스페이스x", "서버", "앱",
    ],
    "life": [
        "생활", "육아", "육아휴직", "출산", "아동수당",
        "건강", "건강검진", "다이어트", "수면", "여행",
        "맛집", "음식", "요리", "교육", "지원금",
        "실업급여", "생활비", "전기요금", "교통비",
        "가전", "청소", "해충", "바퀴벌레", "날씨",
        "휴가", "가족",
    ],
}

_CBL_V24_HARD_BLOCK = [
    "오늘의 운세", "띠별 운세", "별자리 운세",
    "프로야구", "프로축구", "챔피언스리그", "선발 라인업",
    "열애설", "결별설", "아이돌 컴백", "예능 출연",
    "대통령 도착", "정상회담 참석", "국회 본회의",
    "토이 스토리", "우디", "픽사", "박스오피스",
    "영화 개봉", "드라마 첫방",
]

_CBL_V24_REAL_ESTATE_PRIORITY = [
    "부동산", "아파트", "주택", "청약", "분양", "전세",
    "월세", "재건축", "재개발", "정비사업", "오피스텔",
    "집값", "미분양",
]

_CBL_V24_ARCHITECTURE_EXCLUSIVE = [
    "건설", "건축", "공사", "시공", "건설사", "bim",
    "레빗", "다이나모", "건축자재", "건설자재", "공사비",
    "모듈러", "프리패브", "건설 로봇", "건설로봇",
    "건설 자동화", "건설자동화",
]


def _cbl_v24_clean(value):
    value = _cbl_v24_html.unescape(str(value or ""))
    value = _cbl_v24_re.sub(r"<[^>]+>", " ", value)
    value = _cbl_v24_re.sub(r"\s+", " ", value).strip()
    return value


def _cbl_v24_title_key(value):
    value = _cbl_v24_clean(value).lower()
    return _cbl_v24_re.sub(r"[\W_]+", "", value)


def _cbl_v24_domain(url):
    try:
        domain = _cbl_v24_urlparse(str(url or "")).netloc.lower()
        return domain.removeprefix("www.")
    except Exception:
        return ""


def _cbl_v24_parse_date(value):
    try:
        parsed = _cbl_v24_parse_email_date(str(value or ""))
        if parsed.tzinfo is None:
            parsed = parsed.replace(
                tzinfo=_cbl_v24_timezone.get_current_timezone()
            )
        return parsed.astimezone(
            _cbl_v24_timezone.get_current_timezone()
        )
    except Exception:
        return None


def _cbl_v24_get_queries(category, count=_CBL_V24_QUERY_COUNT):
    category = str(category or "").strip().lower()
    all_words = list(CATEGORY_SEARCH_WORDS.get(category, []))
    core_words = list(CATEGORY_CORE_WORDS.get(category, []))

    optional_words = [
        word for word in all_words
        if word not in core_words
    ]

    optional_count = max(0, int(count) - len(core_words))

    seed = (
        f"{_cbl_v24_timezone.localdate().isoformat()}:"
        f"{category}:v24_2"
    )
    randomizer = _cbl_v24_random.Random(seed)
    randomizer.shuffle(optional_words)

    selected = core_words + optional_words[:optional_count]

    print(
        "[NAVER_V24_2_QUERIES]",
        f"category={category}",
        f"queries={selected}",
    )

    return selected


def _cbl_v24_wait_for_request_slot():
    global _CBL_V24_LAST_REQUEST_AT

    with _CBL_V24_REQUEST_LOCK:
        now = _cbl_v24_time.monotonic()
        elapsed = now - _CBL_V24_LAST_REQUEST_AT
        wait_seconds = _CBL_V24_MIN_REQUEST_INTERVAL - elapsed

        if wait_seconds > 0:
            _cbl_v24_time.sleep(wait_seconds)

        _CBL_V24_LAST_REQUEST_AT = _cbl_v24_time.monotonic()


def _cbl_v24_fetch_page(query, start=1, display=50):
    client_id = str(
        getattr(_cbl_v24_settings, "NAVER_CLIENT_ID", "") or ""
    ).strip()
    client_secret = str(
        getattr(_cbl_v24_settings, "NAVER_CLIENT_SECRET", "") or ""
    ).strip()

    if not client_id or not client_secret:
        raise RuntimeError(
            "NAVER_CLIENT_ID 또는 NAVER_CLIENT_SECRET 설정이 비어 있습니다."
        )

    url = (
        "https://openapi.naver.com/v1/search/news.json"
        f"?query={_cbl_v24_quote(query)}"
        f"&display={int(display)}"
        f"&start={int(start)}"
        "&sort=date"
    )

    request = _cbl_v24_Request(
        url,
        headers={
            "X-Naver-Client-Id": client_id,
            "X-Naver-Client-Secret": client_secret,
            "User-Agent": "ChickenBananaLab/1.0",
        },
    )

    last_error = None

    for attempt in range(3):
        _cbl_v24_wait_for_request_slot()

        try:
            with _cbl_v24_urlopen(request, timeout=12) as response:
                raw = response.read().decode(
                    "utf-8",
                    errors="replace",
                )

            data = _cbl_v24_json.loads(raw)
            return data.get("items", []) or []

        except _cbl_v24_HTTPError as error:
            last_error = error

            if error.code != 429 or attempt >= 2:
                raise

            retry_after = 1.5 * (attempt + 1)

            print(
                "[NAVER_V24_2_RATE_LIMIT_RETRY]",
                f"query={query}",
                f"attempt={attempt + 1}",
                f"sleep={retry_after}",
            )

            _cbl_v24_time.sleep(retry_after)

    if last_error:
        raise last_error

    return []


def _cbl_v24_is_category_match(category, title, description=""):
    normalized_title = _cbl_v24_clean(title).lower()

    if not normalized_title:
        return False

    if any(
        blocked.lower() in normalized_title
        for blocked in _CBL_V24_HARD_BLOCK
    ):
        return False

    required = _CBL_V24_TITLE_REQUIRED.get(category, [])

    if not any(
        keyword.lower() in normalized_title
        for keyword in required
    ):
        return False

    # 주택·분양·재건축 중심 기사는 부동산을 우선한다.
    if category == "architecture":
        has_realestate = any(
            keyword.lower() in normalized_title
            for keyword in _CBL_V24_REAL_ESTATE_PRIORITY
        )
        has_architecture = any(
            keyword.lower() in normalized_title
            for keyword in _CBL_V24_ARCHITECTURE_EXCLUSIVE
        )

        if has_realestate and not has_architecture:
            return False

    return True


def _cbl_v24_collect(category):
    category = str(category or "").strip().lower()

    if category not in CATEGORY_SEARCH_WORDS:
        category = "tech"

    now = _cbl_v24_timezone.localtime()
    oldest = now - _cbl_v24_timedelta(days=7)

    candidates = []
    seen = set()

    for query_index, query in enumerate(
        _cbl_v24_get_queries(category)
    ):
        try:
            page = _cbl_v24_fetch_page(
                query=query,
                start=1,
                display=50,
            )
        except Exception as error:
            print(
                "[NAVER_V24_2_PAGE_ERROR]",
                f"category={category}",
                f"query={query}",
                f"error={type(error).__name__}: {error}",
            )
            continue

        for item in page:
            title = _cbl_v24_clean(item.get("title", ""))
            description = _cbl_v24_clean(
                item.get("description", "")
            )
            source_url = str(
                item.get("originallink")
                or item.get("link")
                or ""
            ).strip()
            published_at = _cbl_v24_parse_date(
                item.get("pubDate", "")
            )

            if not title or not source_url or published_at is None:
                continue

            if published_at < oldest:
                continue

            if not _cbl_v24_is_category_match(
                category,
                title,
                description,
            ):
                continue

            key = _cbl_v24_title_key(title)

            if not key or key in seen:
                continue

            seen.add(key)

            age_hours = max(
                0.0,
                (now - published_at).total_seconds() / 3600,
            )

            score = 1000.0 - age_hours - (query_index * 2.0)

            candidates.append({
                "title": title,
                "description": description,
                "source_url": source_url,
                "domain": _cbl_v24_domain(source_url),
                "published_at": published_at,
                "score": score,
            })

    candidates.sort(
        key=lambda item: (
            item["score"],
            item["published_at"],
        ),
        reverse=True,
    )

    selected = []
    domain_counts = {}

    for item in candidates:
        domain = item["domain"] or "unknown"
        count = domain_counts.get(domain, 0)

        if count >= 2:
            continue

        published_at = item["published_at"]
        is_today = published_at.date() == now.date()

        selected.append({
            "category": _CBL_V24_LABELS[category],
            "category_slug": category,
            "keyword": item["title"][:160],
            "reason": (
                "네이버 뉴스 · 오늘 기사 · 본문 생성 전 팩트체크"
                if is_today
                else "네이버 뉴스 · 최근 기사 · 본문 생성 전 팩트체크"
            ),
            "source": domain,
            "source_url": item["source_url"],
            "published_at": published_at.isoformat(),
        })

        domain_counts[domain] = count + 1

        if len(selected) >= _CBL_V24_LIMIT:
            break

    print(
        "[NAVER_V24_2_COLLECT]",
        f"category={category}",
        f"candidates={len(candidates)}",
        f"selected={len(selected)}",
    )

    return selected


def recommend_keywords_from_news(category):
    category = str(category or "").strip().lower()

    aliases = {
        "건축": "architecture",
        "건설": "architecture",
        "부동산": "realestate",
        "금융": "finance",
        "경제": "finance",
        "테크": "tech",
        "기술": "tech",
        "it": "tech",
        "일상": "life",
        "생활": "life",
    }

    category = aliases.get(category, category)

    if category not in CATEGORY_SEARCH_WORDS:
        category = "tech"

    cache_key = f"cbl:naver-keywords:v24_2:{category}"
    stale_key = f"cbl:naver-keywords:v24_2:stale:{category}"

    cached = _cbl_v24_cache.get(cache_key)

    if isinstance(cached, list) and cached:
        print(
            "[NAVER_V24_2_CACHE_HIT]",
            f"category={category}",
            f"items={len(cached)}",
        )
        return cached[:_CBL_V24_LIMIT]

    try:
        result = _cbl_v24_collect(category)

        if result:
            _cbl_v24_cache.set(
                cache_key,
                result,
                _CBL_V24_CACHE_SECONDS,
            )
            _cbl_v24_cache.set(
                stale_key,
                result,
                _CBL_V24_STALE_SECONDS,
            )
            return result[:_CBL_V24_LIMIT]

    except Exception as error:
        print(
            "[NAVER_V24_2_ERROR]",
            f"category={category}",
            f"error={type(error).__name__}: {error}",
        )

    stale = _cbl_v24_cache.get(stale_key)

    if isinstance(stale, list) and stale:
        print(
            "[NAVER_V24_2_STALE]",
            f"category={category}",
            f"items={len(stale)}",
        )
        return stale[:_CBL_V24_LIMIT]

    return []


# CBL_NAVER_ONLY_KEYWORDS_V24_END
