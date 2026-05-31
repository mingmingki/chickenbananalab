import json
import re
import urllib.parse
import urllib.request
from html import unescape
from django.conf import settings


CATEGORY_SEARCH_WORDS = {
    "architecture": [
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


# 너무 일반적이라 키워드로 쓰면 어색한 단어
BAD_KEYWORDS = {
    "있다", "없다", "한다", "했다", "됐다", "된다", "위해", "통해", "대한", "관련",
    "오늘", "내일", "올해", "내년", "지난", "이번", "최근", "최신", "속보", "단독",
    "종합", "기자", "뉴스", "사진", "영상", "오전", "오후", "가능", "확인",
    "함께", "우리", "사회", "문화", "기억", "회장", "후보", "국힘", "민주",
    "선거", "대선", "정치", "국회", "대표", "대통령",
}


def clean_html(text):
    if not text:
        return ""

    text = unescape(text)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("&quot;", '"').replace("&amp;", "&")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def clean_title(title):
    title = clean_html(title)

    # 언론사식 괄호, 따옴표, 특수기호 정리
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


def fetch_naver_news(query, display=10):
    client_id = getattr(settings, "NAVER_CLIENT_ID", "")
    client_secret = getattr(settings, "NAVER_CLIENT_SECRET", "")

    if not client_id or not client_secret:
        raise RuntimeError("NAVER_CLIENT_ID 또는 NAVER_CLIENT_SECRET 설정이 비어 있습니다.")

    encoded_query = urllib.parse.quote(query)
    url = (
        "https://openapi.naver.com/v1/search/news.json"
        f"?query={encoded_query}"
        f"&display={display}"
        "&start=1"
        "&sort=date"
    )

    request = urllib.request.Request(url)
    request.add_header("X-Naver-Client-Id", client_id)
    request.add_header("X-Naver-Client-Secret", client_secret)

    with urllib.request.urlopen(request, timeout=10) as response:
        body = response.read().decode("utf-8")
        data = json.loads(body)

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
        })

    return results


def is_bad_title(title, category):
    compact = title.replace(" ", "")

    # 너무 정치/선거 뉴스가 건축 카테고리에 섞이는 것 방지
    if category in ["architecture", "realestate", "life"]:
        political_words = ["대선", "선거", "후보", "국힘", "민주", "대통령", "국회", "정당"]
        if any(word in compact for word in political_words):
            return True

    # 너무 짧거나 의미 없는 제목 제거
    if len(title) < 8:
        return True

    return False


def make_blog_keyword(title, query):
    """
    뉴스 제목을 블로그 글감 키워드처럼 짧게 압축
    """

    title = clean_title(title)

    # 콜론, 쉼표, 물음표 뒤쪽은 과감히 제거
    title = re.split(r"[:?！!]", title)[0].strip()

    # 긴 제목은 앞쪽 핵심만 사용
    words = title.split()

    # 불필요한 단어 제거
    filtered = []
    for word in words:
        clean_word = re.sub(r"[^가-힣A-Za-z0-9]", "", word)

        if not clean_word:
            continue

        if clean_word in BAD_KEYWORDS:
            continue

        if len(clean_word) <= 1:
            continue

        filtered.append(clean_word)

    if not filtered:
        return query

    # 검색어가 제목에 있으면 검색어 주변 단어를 우선 사용
    keyword_words = []

    for word in filtered:
        keyword_words.append(word)

        if len(keyword_words) >= 4:
            break

    keyword = " ".join(keyword_words).strip()

    # 너무 길면 자르기
    if len(keyword) > 24:
        keyword = keyword[:24].strip()

    # 그래도 너무 짧으면 검색어 보강
    if len(keyword) < 4:
        keyword = query

    return keyword


def score_news_item(item, category):
    title = item["title"]
    description = item.get("description", "")
    query = item.get("query", "")

    score = 0

    # 검색어가 제목에 직접 들어가면 가산점
    if query and query.replace(" ", "") in title.replace(" ", ""):
        score += 5

    # 카테고리별 핵심 단어 가산점
    category_words = CATEGORY_SEARCH_WORDS.get(category, [])
    for word in category_words:
        if word.replace(" ", "") in title.replace(" ", ""):
            score += 3

    # 설명까지 포함하면 약간 가산점
    for word in category_words:
        if word.replace(" ", "") in description.replace(" ", ""):
            score += 1

    # 제목이 너무 길면 감점
    if len(title) > 55:
        score -= 1

    return score


def recommend_keywords_from_news(category):
    if category == "all":
        category = "tech"

    search_words = CATEGORY_SEARCH_WORDS.get(category, CATEGORY_SEARCH_WORDS["tech"])
    category_label = CATEGORY_LABELS.get(category, "테크")

    all_news = []
    seen_titles = set()

    for query in search_words:
        try:
            news_items = fetch_naver_news(query, display=8)
        except Exception:
            continue

        for item in news_items:
            title_key = re.sub(r"\s+", "", item["title"].lower())

            if title_key in seen_titles:
                continue

            if is_bad_title(item["title"], category):
                continue

            seen_titles.add(title_key)
            all_news.append(item)

    if not all_news:
        return [
            {
                "category": category_label,
                "keyword": search_words[0],
                "reason": "오늘 뉴스 결과가 부족해 카테고리 기본 키워드를 추천했습니다.",
            }
        ]

    # 점수 높은 뉴스 우선 정렬
    all_news.sort(key=lambda item: score_news_item(item, category), reverse=True)

    recommendations = []
    used_keywords = set()

    for item in all_news:
        if len(recommendations) >= 7:
            break

        keyword = make_blog_keyword(item["title"], item.get("query", ""))

        keyword_key = keyword.replace(" ", "").lower()

        if keyword_key in used_keywords:
            continue

        # 단어 하나짜리 이상한 키워드 방지
        if keyword in BAD_KEYWORDS:
            continue

        if len(keyword) < 3:
            continue

        used_keywords.add(keyword_key)

        recommendations.append({
            "category": category_label,
            "keyword": keyword,
            "reason": f"관련 뉴스: {item['title'][:48]}...",
        })

    # 부족하면 검색어 기반으로 보충
    for word in search_words:
        if len(recommendations) >= 7:
            break

        word_key = word.replace(" ", "").lower()
        if word_key in used_keywords:
            continue

        recommendations.append({
            "category": category_label,
            "keyword": word,
            "reason": "오늘 카테고리 뉴스 흐름을 기준으로 추천한 글감입니다.",
        })

    return recommendations[:7]