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

    for word in search_words[:7]:
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
        if len(recommendations) >= 7:
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
        if len(recommendations) >= 7:
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

    return recommendations[:7]