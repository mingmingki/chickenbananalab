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


# CBL_CONSTRUCTION_NAVER_CATEGORY_PATCH_START
# 건설 세부 카테고리 추천키워드 지원
try:
    CATEGORY_SEARCH_WORDS.update({
        "construction_work": [
            "건설 실무", "건설 현장", "시공", "공정관리", "적산", "수량산출",
            "원가관리", "공사비", "견적", "계약 클레임", "품질관리", "건설 안전",
        ],
        "construction_tech": [
            "건설기술", "스마트건설", "BIM", "건설 AI", "건설 자동화",
            "레빗", "다이나모", "드론 측량", "건설로봇", "모듈러 건축", "OSC",
        ],
        "construction_real": [
            "건설 부동산", "아파트 분양", "청약", "재건축", "재개발", "정비사업",
            "분양가", "공사비", "부동산 정책", "오피스텔 분양", "주택 시장",
        ],
    })
    CATEGORY_LABELS.update({
        "construction_work": "건설실무",
        "construction_tech": "건설기술",
        "construction_real": "건설부동산",
    })
    CATEGORY_ALIASES.update({
        "건설": "construction_work",
        "건축": "construction_work",
        "건설실무": "construction_work",
        "시공": "construction_work",
        "건설기술": "construction_tech",
        "BIM": "construction_tech",
        "bim": "construction_tech",
        "건설부동산": "construction_real",
        "건설 부동산": "construction_real",
        "부동산": "construction_real",
        "construction_work": "construction_work",
        "construction_tech": "construction_tech",
        "construction_real": "construction_real",
    })
    if "CATEGORY_CORE_WORDS" in globals():
        CATEGORY_CORE_WORDS.update({
            "construction_work": ["건설", "시공", "공정", "공사비"],
            "construction_tech": ["건설기술", "BIM", "스마트건설", "AI"],
            "construction_real": ["분양", "청약", "재건축", "부동산"],
        })
except Exception as _cbl_construction_naver_error:
    print("CBL construction naver category patch skipped:", _cbl_construction_naver_error)
# CBL_CONSTRUCTION_NAVER_CATEGORY_PATCH_END


# CBL_TODAY_KEYWORD_NEW_CATEGORY_SOURCE_START
# 오늘자 추천키워드 실제 검색 기준을 신규 8개 카테고리로 교체
CATEGORY_SEARCH_WORDS = {
    "construction_work": [
        "건설 현장",
        "시공관리",
        "공정관리",
        "품질관리",
        "안전관리",
        "하자보수",
        "건설자재",
        "공사일보",
        "도면검토",
        "물량산출",
    ],
    "construction_tech": [
        "스마트건설",
        "건설 AI",
        "건설 로봇",
        "드론 측량",
        "모듈러 건축",
        "프리콘",
        "건설 자동화",
        "디지털트윈 건설",
        "스마트 안전",
    ],
    "construction_real": [
        "아파트 분양",
        "청약",
        "재건축",
        "재개발",
        "공사비",
        "분양가",
        "부동산 정책",
        "건설사 분양",
        "주택 공급",
    ],
    "bim": [
        "Revit BIM",
        "레빗 BIM",
        "BIM 모델링",
        "BIM 협업",
        "BIM 물량산출",
        "Revit 패밀리",
        "BIM 도면검토",
        "BIM 템플릿",
    ],
    "dynamo_automation": [
        "Dynamo Revit",
        "다이나모 자동화",
        "Dynamo Python",
        "Revit Dynamo",
        "파라미터 자동화",
        "엑셀 연동 자동화",
        "BIM 자동화",
        "반복작업 자동화",
    ],
    "four_d_five_d": [
        "4D BIM",
        "5D BIM",
        "Navisworks",
        "공정 시뮬레이션",
        "BIM 원가",
        "공정 수량 연동",
        "5D 원가관리",
        "BIM 공정관리",
    ],
    "program": [
        "업무용 프로그램",
        "PDF 프로그램",
        "ZIP 프로그램",
        "파일 관리 프로그램",
        "화면 녹화 프로그램",
        "문서 자동화 프로그램",
        "업무 자동화 프로그램",
    ],
    "tool_recommend": [
        "AI 도구",
        "생산성 도구",
        "업무 자동화 툴",
        "무료 툴 추천",
        "PDF 툴 추천",
        "협업툴 추천",
        "개발툴 추천",
        "업무 효율 툴",
    ],
}

CATEGORY_LABELS = {
    "construction_work": "건설실무",
    "construction_tech": "건설기술",
    "construction_real": "건설부동산",
    "bim": "REVIT/BIM",
    "dynamo_automation": "Dynamo/자동화",
    "four_d_five_d": "4D/5D",
    "program": "업무용 프로그램",
    "tool_recommend": "툴소개/툴추천",
}

CATEGORY_ALIASES = {
    "all": "construction_work",

    "건축": "construction_work",
    "architecture": "construction_work",
    "건설실무": "construction_work",
    "construction_work": "construction_work",

    "건설기술": "construction_tech",
    "construction_tech": "construction_tech",
    "테크": "construction_tech",
    "tech": "construction_tech",

    "부동산": "construction_real",
    "realestate": "construction_real",
    "건설부동산": "construction_real",
    "construction_real": "construction_real",

    "BIM": "bim",
    "REVIT/BIM": "bim",
    "bim": "bim",

    "Dynamo": "dynamo_automation",
    "Dynamo/자동화": "dynamo_automation",
    "다이나모": "dynamo_automation",
    "dynamo_automation": "dynamo_automation",

    "4D/5D": "four_d_five_d",
    "4D": "four_d_five_d",
    "5D": "four_d_five_d",
    "four_d_five_d": "four_d_five_d",

    "프로그램": "program",
    "업무용 프로그램": "program",
    "program": "program",

    "툴소개/툴추천": "tool_recommend",
    "툴추천": "tool_recommend",
    "추천툴": "tool_recommend",
    "tool_recommend": "tool_recommend",

    # 예전 값이 들어와도 오류 안 나게 임시 연결
    "금융": "construction_real",
    "finance": "construction_real",
    "일상": "tool_recommend",
    "life": "tool_recommend",
}

def normalize_category(category):
    category = (category or "construction_work").strip()
    return CATEGORY_ALIASES.get(category, category)
# CBL_TODAY_KEYWORD_NEW_CATEGORY_SOURCE_END



# CBL_TODAY_KEYWORD_V25_NO_EMPTY_START
# 신규 8개 카테고리 추천키워드 보강
# - V24 제목 필터가 새 카테고리를 전부 탈락시키는 문제 수정
# - 네이버 결과가 0개여도 503이 아니라 기본 글감 반환

CBL_TODAY_CATEGORY_LABELS_V25 = {
    "construction_work": "건설실무",
    "construction_tech": "건설기술",
    "construction_real": "건설부동산",
    "bim": "REVIT/BIM",
    "dynamo_automation": "Dynamo/자동화",
    "four_d_five_d": "4D/5D",
    "program": "업무용 프로그램",
    "tool_recommend": "툴소개/툴추천",
}

CBL_TODAY_CATEGORY_ALIASES_V25 = {
    "all": "construction_work",

    "건축": "construction_work",
    "건설": "construction_work",
    "건설실무": "construction_work",
    "architecture": "construction_work",
    "construction_work": "construction_work",

    "건설기술": "construction_tech",
    "construction_tech": "construction_tech",
    "테크": "construction_tech",
    "tech": "construction_tech",

    "부동산": "construction_real",
    "건설부동산": "construction_real",
    "realestate": "construction_real",
    "construction_real": "construction_real",

    "BIM": "bim",
    "REVIT/BIM": "bim",
    "revit/bim": "bim",
    "bim": "bim",

    "Dynamo": "dynamo_automation",
    "Dynamo/자동화": "dynamo_automation",
    "다이나모": "dynamo_automation",
    "dynamo_automation": "dynamo_automation",

    "4D/5D": "four_d_five_d",
    "4D": "four_d_five_d",
    "5D": "four_d_five_d",
    "four_d_five_d": "four_d_five_d",

    "프로그램": "program",
    "업무용 프로그램": "program",
    "program": "program",

    "툴소개/툴추천": "tool_recommend",
    "툴추천": "tool_recommend",
    "추천툴": "tool_recommend",
    "tool_recommend": "tool_recommend",

    # 예전 값 임시 연결
    "금융": "construction_real",
    "finance": "construction_real",
    "일상": "tool_recommend",
    "life": "tool_recommend",
}

CBL_TODAY_SEARCH_WORDS_V25 = {
    "construction_work": [
        "건설 현장", "시공관리", "공정관리", "품질관리", "안전관리",
        "하자보수", "건설자재", "공사일보", "도면검토", "물량산출",
        "실행예산", "원가관리", "공사비",
    ],
    "construction_tech": [
        "스마트건설", "건설 AI", "건설 로봇", "드론 측량", "모듈러 건축",
        "프리콘", "건설 자동화", "디지털트윈 건설", "스마트 안전", "신공법",
    ],
    "construction_real": [
        "아파트 분양", "청약", "재건축", "재개발", "공사비", "분양가",
        "부동산 정책", "건설사 분양", "주택 공급", "입주 물량",
    ],
    "bim": [
        "Revit BIM", "레빗 BIM", "BIM 모델링", "BIM 협업", "BIM 물량산출",
        "Revit 패밀리", "BIM 도면검토", "BIM 템플릿", "간섭검토",
    ],
    "dynamo_automation": [
        "Dynamo Revit", "다이나모 자동화", "Dynamo Python", "Revit Dynamo",
        "파라미터 자동화", "엑셀 연동 자동화", "BIM 자동화", "반복작업 자동화",
    ],
    "four_d_five_d": [
        "4D BIM", "5D BIM", "Navisworks", "공정 시뮬레이션",
        "BIM 원가", "공정 수량 연동", "5D 원가관리", "BIM 공정관리",
    ],
    "program": [
        "업무용 프로그램", "PDF 프로그램", "ZIP 프로그램", "파일 관리 프로그램",
        "화면 녹화 프로그램", "문서 자동화 프로그램", "업무 자동화 프로그램",
    ],
    "tool_recommend": [
        "AI 도구", "생산성 도구", "업무 자동화 툴", "무료 툴 추천",
        "PDF 툴 추천", "협업툴 추천", "개발툴 추천", "업무 효율 툴",
    ],
}

CBL_TODAY_CORE_WORDS_V25 = {
    "construction_work": ["건설 현장", "시공관리", "공정관리", "공사비", "도면검토"],
    "construction_tech": ["스마트건설", "건설 AI", "건설 로봇", "건설 자동화", "드론 측량"],
    "construction_real": ["아파트 분양", "청약", "재건축", "공사비", "부동산 정책"],
    "bim": ["Revit BIM", "레빗 BIM", "BIM 모델링", "BIM 물량산출", "BIM 도면검토"],
    "dynamo_automation": ["Dynamo Revit", "다이나모 자동화", "Dynamo Python", "파라미터 자동화", "BIM 자동화"],
    "four_d_five_d": ["4D BIM", "5D BIM", "Navisworks", "공정 시뮬레이션", "5D 원가관리"],
    "program": ["업무용 프로그램", "PDF 프로그램", "ZIP 프로그램", "문서 자동화 프로그램", "업무 자동화 프로그램"],
    "tool_recommend": ["AI 도구", "생산성 도구", "업무 자동화 툴", "무료 툴 추천", "업무 효율 툴"],
}

CBL_TODAY_FALLBACK_KEYWORDS_V25 = {
    "construction_work": [
        "건설 현장 공정관리 체크리스트",
        "시공 전 도면검토가 중요한 이유",
        "물량산출 오류를 줄이는 기본 흐름",
        "공사일보 작성 시 꼭 확인할 항목",
        "하자보수를 줄이는 현장 품질관리 방법",
        "건설 안전관리 실무 체크포인트",
        "공사비 검토 전에 확인해야 할 자료",
        "협력업체와 공정 협의할 때 필요한 기준",
    ],
    "construction_tech": [
        "스마트건설 기술이 현장관리에 쓰이는 방식",
        "건설 AI가 바꾸는 현장 업무 흐름",
        "드론 측량을 건설 현장에 적용하는 방법",
        "건설 로봇 도입 전에 확인할 점",
        "모듈러 건축과 기존 시공 방식의 차이",
        "디지털트윈이 건설 현장에 필요한 이유",
        "스마트 안전 기술의 현장 적용 사례",
        "프리콘 단계에서 기술검토가 중요한 이유",
    ],
    "construction_real": [
        "공사비 상승이 분양가에 미치는 영향",
        "청약 전 분양가를 비교하는 기준",
        "재건축 사업에서 공사비가 중요한 이유",
        "입주 물량이 지역 부동산 시장에 미치는 영향",
        "건설사 분양 일정을 볼 때 확인할 점",
        "부동산 정책이 건설 현장에 미치는 영향",
        "재개발 사업에서 시공사 선정이 중요한 이유",
        "아파트 분양 공고에서 확인해야 할 항목",
    ],
    "bim": [
        "Revit 모델링을 시작할 때 먼저 정리할 기준",
        "BIM 협업에서 템플릿이 중요한 이유",
        "Revit 패밀리 작성 전 확인해야 할 구조",
        "BIM 물량산출을 실무에 적용하는 흐름",
        "도면검토와 BIM 모델 검토의 차이",
        "Revit 프로젝트 템플릿을 정리하는 방법",
        "BIM 간섭검토에서 자주 놓치는 부분",
        "BIM 모델 품질을 높이는 기본 기준",
    ],
    "dynamo_automation": [
        "Dynamo로 파라미터를 자동 입력하는 기본 흐름",
        "Revit 반복작업을 자동화할 때 주의할 점",
        "Dynamo와 Python을 같이 쓰는 이유",
        "엑셀 데이터를 Dynamo에 연결하는 방법",
        "다이나모 노드 구조를 쉽게 이해하는 방법",
        "BIM 자동화에서 가장 먼저 자동화할 업무",
        "Dynamo 그래프를 정리하는 실무 기준",
        "파라미터 자동화가 물량산출에 필요한 이유",
    ],
    "four_d_five_d": [
        "4D BIM 공정 시뮬레이션을 쓰는 이유",
        "5D BIM에서 수량과 원가를 연결하는 흐름",
        "Navisworks로 공정을 검토할 때 확인할 점",
        "공정표와 BIM 모델을 연결하는 기본 구조",
        "5D 원가관리에서 물량 기준이 중요한 이유",
        "4D 시뮬레이션 적용 전에 준비할 자료",
        "공정과 수량을 연결할 때 생기는 실무 문제",
        "BIM 공정관리와 일반 공정관리의 차이",
    ],
    "program": [
        "업무용 프로그램을 고를 때 확인할 기준",
        "PDF 작업을 빠르게 처리하는 프로그램 활용법",
        "파일 압축 프로그램을 업무에 맞게 쓰는 방법",
        "화면녹화 프로그램이 업무 공유에 필요한 이유",
        "문서 작업을 자동화하면 좋은 업무 유형",
        "업무용 프로그램 설치 전 확인할 항목",
        "파일 관리 프로그램이 필요한 업무 상황",
        "업무 자동화 프로그램을 만들 때 필요한 기능",
    ],
    "tool_recommend": [
        "업무 효율을 높이는 AI 도구 추천",
        "무료 생산성 도구를 고를 때 확인할 기준",
        "PDF 작업에 유용한 툴 비교",
        "협업툴을 선택할 때 보는 기능",
        "반복 업무를 줄이는 자동화 도구 활용법",
        "개발자가 아니어도 쓸 수 있는 업무 자동화 툴",
        "무료 툴과 유료 툴을 비교하는 기준",
        "업무 효율을 높이는 추천툴 정리",
    ],
}

def _cbl_today_normalize_category_v25(category):
    category = str(category or "construction_work").strip()
    lowered = category.lower()
    return CBL_TODAY_CATEGORY_ALIASES_V25.get(
        category,
        CBL_TODAY_CATEGORY_ALIASES_V25.get(lowered, category if category in CBL_TODAY_CATEGORY_LABELS_V25 else "construction_work")
    )

try:
    CATEGORY_SEARCH_WORDS.update(CBL_TODAY_SEARCH_WORDS_V25)
except Exception:
    CATEGORY_SEARCH_WORDS = dict(CBL_TODAY_SEARCH_WORDS_V25)

try:
    CATEGORY_CORE_WORDS.update(CBL_TODAY_CORE_WORDS_V25)
except Exception:
    CATEGORY_CORE_WORDS = dict(CBL_TODAY_CORE_WORDS_V25)

try:
    CATEGORY_LABELS.update(CBL_TODAY_CATEGORY_LABELS_V25)
except Exception:
    CATEGORY_LABELS = dict(CBL_TODAY_CATEGORY_LABELS_V25)

try:
    CATEGORY_ALIASES.update(CBL_TODAY_CATEGORY_ALIASES_V25)
except Exception:
    CATEGORY_ALIASES = dict(CBL_TODAY_CATEGORY_ALIASES_V25)

try:
    _CBL_V24_LABELS.update(CBL_TODAY_CATEGORY_LABELS_V25)
except Exception:
    pass

try:
    _CBL_V24_TITLE_REQUIRED.update(CBL_TODAY_SEARCH_WORDS_V25)
except Exception:
    _CBL_V24_TITLE_REQUIRED = dict(CBL_TODAY_SEARCH_WORDS_V25)

# 기존 normalize_category도 새 기준으로 교체
def normalize_category(category):
    return _cbl_today_normalize_category_v25(category)

# V24 필터 완화: 제목뿐 아니라 description까지 보고 새 카테고리도 통과
def _cbl_v24_is_category_match(category, title, description=""):
    category = _cbl_today_normalize_category_v25(category)

    cleaner = globals().get("_cbl_v24_clean", lambda value: str(value or ""))
    normalized_title = cleaner(title).lower()
    normalized_text = (cleaner(title) + " " + cleaner(description)).lower()

    if not normalized_title:
        return False

    hard_block = globals().get("_CBL_V24_HARD_BLOCK", [])
    if any(str(blocked).lower() in normalized_title for blocked in hard_block):
        return False

    required = globals().get("_CBL_V24_TITLE_REQUIRED", {}).get(category, [])
    if not required:
        required = CBL_TODAY_SEARCH_WORDS_V25.get(category, [])

    if not required:
        return True

    return any(str(keyword).lower() in normalized_text for keyword in required if keyword)

try:
    _CBL_V25_ORIGINAL_RECOMMEND = recommend_keywords_from_news
except Exception:
    _CBL_V25_ORIGINAL_RECOMMEND = None

def _cbl_v25_normalize_items(items, category):
    category = _cbl_today_normalize_category_v25(category)
    label = CBL_TODAY_CATEGORY_LABELS_V25.get(category, category)

    normalized = []

    for item in items or []:
        if not isinstance(item, dict):
            continue

        keyword = str(item.get("keyword") or "").strip()
        if not keyword:
            continue

        copied = dict(item)
        copied["category"] = label
        copied["category_slug"] = category
        copied.setdefault("reason", "네이버 뉴스 기반 추천 키워드입니다.")
        copied.setdefault("source", "")
        copied.setdefault("source_url", "")
        normalized.append(copied)

    return normalized

def _cbl_v25_fallback_recommendations(category):
    category = _cbl_today_normalize_category_v25(category)
    label = CBL_TODAY_CATEGORY_LABELS_V25.get(category, category)

    limit = int(globals().get("_CBL_V24_LIMIT", 5) or 5)
    words = CBL_TODAY_FALLBACK_KEYWORDS_V25.get(
        category,
        CBL_TODAY_FALLBACK_KEYWORDS_V25["construction_work"],
    )

    published_at = ""
    try:
        published_at = _cbl_v24_timezone.localtime().isoformat()
    except Exception:
        pass

    result = []

    for keyword in words[:limit]:
        result.append({
            "category": label,
            "category_slug": category,
            "keyword": keyword,
            "reason": "기본 추천 글감 · 바로 생성 가능",
            "source": "CBL 기본추천",
            "source_url": "",
            "published_at": published_at,
        })

    return result

def recommend_keywords_from_news(category):
    category = _cbl_today_normalize_category_v25(category)

    if _CBL_V25_ORIGINAL_RECOMMEND:
        try:
            result = _CBL_V25_ORIGINAL_RECOMMEND(category)
            result = _cbl_v25_normalize_items(result, category)

            if result:
                return result[:int(globals().get("_CBL_V24_LIMIT", 5) or 5)]

        except Exception as error:
            print(
                "[NAVER_V25_RECOMMEND_ERROR]",
                f"category={category}",
                f"error={type(error).__name__}: {error}",
            )

    fallback = _cbl_v25_fallback_recommendations(category)

    try:
        cache = globals().get("_cbl_v24_cache")
        if cache:
            cache.set(
                f"cbl:naver-keywords:v24_2:{category}",
                fallback,
                5 * 60,
            )
    except Exception:
        pass

    print(
        "[NAVER_V25_FALLBACK]",
        f"category={category}",
        f"items={len(fallback)}",
    )

    return fallback
# CBL_TODAY_KEYWORD_V25_NO_EMPTY_END






# CBL_TODAY_KEYWORD_V26_STRICT_FILTER_START
# 오늘자 추천키워드 V26
# - 드론/공정/AI 같은 단어 하나만 보고 엉뚱한 뉴스가 들어오는 문제 방지
# - 카테고리별 핵심 문맥이 없으면 뉴스 추천에서 제외
# - 부족한 수량은 카테고리 기본 글감으로 채움

CBL_TODAY_SEARCH_WORDS_V26 = {
    "construction_work": [
        "건설 현장 안전",
        "건설 현장 시공",
        "건설 공정관리",
        "건설 도면검토",
        "건설 물량산출",
        "공사비 건설",
        "건설 품질관리",
    ],
    "construction_tech": [
        "스마트건설",
        "건설 로봇",
        "건설현장 AI",
        "건설 드론",
        "BIM 스마트건설",
        "건설 디지털트윈",
        "모듈러 건축",
    ],
    "construction_real": [
        "아파트 분양",
        "청약 분양",
        "재건축 공사비",
        "재개발 정비사업",
        "부동산 주택공급",
        "건설사 분양",
        "분양가 공사비",
    ],
    "bim": [
        "Revit BIM",
        "레빗 BIM",
        "BIM 물량산출",
        "BIM 도면검토",
        "BIM 모델링",
        "Revit 패밀리",
        "설계도서 3D BIM",
    ],
    "dynamo_automation": [
        "Dynamo Revit",
        "다이나모 자동화",
        "Dynamo Python",
        "Revit Dynamo 자동화",
        "BIM 자동화 Dynamo",
        "Revit 파라미터 자동화",
    ],
    "four_d_five_d": [
        "4D BIM",
        "5D BIM",
        "Navisworks BIM",
        "BIM 공정관리",
        "BIM 원가관리",
        "BIM 공정 시뮬레이션",
        "5D 원가관리",
    ],
    "program": [
        "업무용 프로그램",
        "PDF 프로그램",
        "ZIP 프로그램",
        "문서 자동화 프로그램",
        "파일 관리 프로그램",
        "화면 녹화 프로그램",
        "업무 자동화 프로그램",
    ],
    "tool_recommend": [
        "AI 도구 추천",
        "생산성 도구 추천",
        "업무 자동화 툴",
        "무료 툴 추천",
        "PDF 툴 추천",
        "협업툴 추천",
        "업무 효율 툴",
    ],
}

try:
    CATEGORY_SEARCH_WORDS.update(CBL_TODAY_SEARCH_WORDS_V26)
except Exception:
    CATEGORY_SEARCH_WORDS = dict(CBL_TODAY_SEARCH_WORDS_V26)

try:
    CATEGORY_CORE_WORDS.update({
        k: v[:5] for k, v in CBL_TODAY_SEARCH_WORDS_V26.items()
    })
except Exception:
    CATEGORY_CORE_WORDS = {k: v[:5] for k, v in CBL_TODAY_SEARCH_WORDS_V26.items()}


def _cbl_v26_text(value):
    return str(value or "").replace("<b>", "").replace("</b>", "").strip()


def _cbl_v26_has_any(text, words):
    text = _cbl_v26_text(text).lower()
    return any(str(w).lower() in text for w in words)


def _cbl_v26_has_all_groups(text, groups):
    text = _cbl_v26_text(text).lower()
    for group in groups:
        if not any(str(w).lower() in text for w in group):
            return False
    return True


def _cbl_v26_is_blocked(text):
    text = _cbl_v26_text(text).lower()

    blocked_words = [
        "된장", "맛집", "레시피", "아이돌", "유퀴즈", "허남준",
        "연예", "가수", "배우", "영화", "드라마",
        "증권 뉴스브리핑", "gam", "ibm", "재테크", "비트코인",
        "웰다잉", "작가의집", "출간", "인재 양성코스",
        "정유 공정", "원유 정제", "바이오", "정기대의원회",
    ]

    return any(w in text for w in blocked_words)


def _cbl_v26_is_good_news_item(category, item):
    category = _cbl_today_normalize_category_v25(category)

    title = _cbl_v26_text(item.get("keyword") or item.get("title") or "")
    desc = _cbl_v26_text(item.get("description") or item.get("reason") or "")
    source = _cbl_v26_text(item.get("source") or "")

    # 기본추천은 항상 통과
    if "CBL 기본추천" in source or "기본 글감" in desc:
        return True

    text = f"{title} {desc}"

    if not title:
        return False

    if _cbl_v26_is_blocked(text):
        return False

    if category == "construction_work":
        return _cbl_v26_has_any(text, [
            "건설", "공사", "시공", "현장", "안전", "공정관리", "품질관리",
            "하자", "자재", "도면", "물량", "철도공단", "건설노동자",
        ])

    if category == "construction_tech":
        return _cbl_v26_has_all_groups(text, [
            ["건설", "현장", "시공", "건축", "토목", "국토교통", "스마트건설"],
            ["AI", "인공지능", "로봇", "드론", "BIM", "디지털트윈", "모듈러", "자동화", "스마트", "기술"],
        ])

    if category == "construction_real":
        return _cbl_v26_has_any(text, [
            "분양", "청약", "재건축", "재개발", "정비사업", "부동산",
            "아파트", "주택", "입주", "공사비", "분양가", "건설사", "뉴스테이",
        ])

    if category == "bim":
        return _cbl_v26_has_any(text, [
            "BIM", "Revit", "레빗", "설계도서", "2D", "3D", "MEP",
            "배관", "물량 산출", "물량산출", "도면", "모델링", "간섭검토",
        ])

    if category == "dynamo_automation":
        return _cbl_v26_has_any(text, [
            "Dynamo", "다이나모", "Revit Dynamo", "Dynamo Python",
            "파라미터 자동화", "BIM 자동화",
        ])

    if category == "four_d_five_d":
        return _cbl_v26_has_any(text, [
            "4D BIM", "5D BIM", "Navisworks", "나비스웍스",
            "BIM 공정", "BIM 원가", "5D 원가", "공정 시뮬레이션",
        ])

    if category == "program":
        return _cbl_v26_has_any(text, [
            "업무용 프로그램", "PDF 프로그램", "ZIP 프로그램",
            "파일 관리 프로그램", "화면 녹화 프로그램", "문서 자동화 프로그램",
            "업무 자동화 프로그램", "소프트웨어", "앱",
        ])

    if category == "tool_recommend":
        return _cbl_v26_has_any(text, [
            "AI 도구", "생산성 도구", "업무 자동화 툴", "무료 툴",
            "PDF 툴", "협업툴", "업무 효율 툴", "추천툴", "툴 추천",
        ])

    return False


try:
    _CBL_V26_ORIGINAL_RECOMMEND = recommend_keywords_from_news
except Exception:
    _CBL_V26_ORIGINAL_RECOMMEND = None


def _cbl_v26_make_fallback_item(category, keyword):
    category = _cbl_today_normalize_category_v25(category)
    label = CBL_TODAY_CATEGORY_LABELS_V25.get(category, category)

    published_at = ""
    try:
        published_at = _cbl_v24_timezone.localtime().isoformat()
    except Exception:
        pass

    return {
        "category": label,
        "category_slug": category,
        "keyword": keyword,
        "reason": "기본 추천 글감 · 바로 생성 가능",
        "source": "CBL 기본추천",
        "source_url": "",
        "published_at": published_at,
    }


def _cbl_v26_fill_with_fallback(category, items, limit):
    category = _cbl_today_normalize_category_v25(category)

    result = []
    seen = set()

    for item in items or []:
        keyword = _cbl_v26_text(item.get("keyword") or "")
        if not keyword:
            continue

        key = keyword.replace(" ", "").lower()
        if key in seen:
            continue

        seen.add(key)
        result.append(item)

        if len(result) >= limit:
            return result[:limit]

    fallback_words = CBL_TODAY_FALLBACK_KEYWORDS_V25.get(
        category,
        CBL_TODAY_FALLBACK_KEYWORDS_V25["construction_work"],
    )

    for keyword in fallback_words:
        key = keyword.replace(" ", "").lower()
        if key in seen:
            continue

        seen.add(key)
        result.append(_cbl_v26_make_fallback_item(category, keyword))

        if len(result) >= limit:
            break

    return result[:limit]


def recommend_keywords_from_news(category):
    category = _cbl_today_normalize_category_v25(category)
    limit = int(globals().get("_CBL_V24_LIMIT", 5) or 5)

    raw_items = []

    if _CBL_V26_ORIGINAL_RECOMMEND:
        try:
            raw_items = _CBL_V26_ORIGINAL_RECOMMEND(category) or []
        except Exception as error:
            print(
                "[NAVER_V26_RECOMMEND_ERROR]",
                f"category={category}",
                f"error={type(error).__name__}: {error}",
            )
            raw_items = []

    filtered = []

    for item in raw_items:
        if not isinstance(item, dict):
            continue

        if _cbl_v26_is_good_news_item(category, item):
            copied = dict(item)
            copied["category_slug"] = category
            copied["category"] = CBL_TODAY_CATEGORY_LABELS_V25.get(category, category)
            filtered.append(copied)

    final_items = _cbl_v26_fill_with_fallback(category, filtered, limit)

    print(
        "[NAVER_V26_STRICT]",
        f"category={category}",
        f"raw={len(raw_items)}",
        f"filtered={len(filtered)}",
        f"final={len(final_items)}",
    )

    return final_items
# CBL_TODAY_KEYWORD_V26_STRICT_FILTER_END
