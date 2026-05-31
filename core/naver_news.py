import json
import re
import urllib.parse
import urllib.request
from html import unescape
from django.conf import settings


CATEGORY_SEARCH_WORDS = {
    "architecture": ["건축", "건설", "아파트 하자", "인테리어", "리모델링", "건축자재"],
    "realestate": ["부동산", "아파트 분양", "청약", "전세", "주택담보대출", "부동산 정책"],
    "finance": ["금리", "주식", "환율", "코스피", "비트코인", "경제 전망"],
    "tech": ["AI", "챗GPT", "아이폰", "반도체", "네이버", "구글", "맥북"],
    "life": ["생활정보", "여행", "육아", "맛집", "건강", "주말 나들이"],
}

CATEGORY_LABELS = {
    "architecture": "건축",
    "realestate": "부동산",
    "finance": "금융",
    "tech": "테크",
    "life": "일상",
}

STOP_WORDS = {
    "오늘", "관련", "최신", "뉴스", "속보", "단독", "종합", "기자",
    "그리고", "하지만", "이번", "내년", "올해", "지난", "대한",
    "위해", "통해", "대해", "한다", "했다", "된다", "있는", "없는",
    "한국", "정부", "시장", "기업", "서울", "국내", "해외",
}


def clean_html(text):
    if not text:
        return ""

    text = unescape(text)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("&quot;", '"').replace("&amp;", "&")
    return text.strip()


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

    items = data.get("items", [])

    results = []
    for item in items:
        title = clean_html(item.get("title", ""))
        description = clean_html(item.get("description", ""))
        link = item.get("originallink") or item.get("link") or ""

        if not title:
            continue

        results.append({
            "title": title,
            "description": description,
            "link": link,
        })

    return results


def extract_keyword_candidates(text):
    if not text:
        return []

    # 한글/영문/숫자 단어 추출
    words = re.findall(r"[가-힣A-Za-z0-9]{2,}", text)

    cleaned = []
    for word in words:
        word = word.strip()

        if len(word) < 2:
            continue

        if word in STOP_WORDS:
            continue

        if word.isdigit():
            continue

        cleaned.append(word)

    return cleaned


def recommend_keywords_from_news(category):
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
            title = item["title"]

            # 제목 중복 제거
            normalized_title = re.sub(r"\s+", "", title.lower())
            if normalized_title in seen_titles:
                continue

            seen_titles.add(normalized_title)
            all_news.append(item)

    if not all_news:
        return [
            {
                "category": category_label,
                "keyword": "오늘의 이슈",
                "reason": "네이버 뉴스 API 결과가 없어 기본 키워드를 추천했습니다.",
            }
        ]

    score = {}
    sample_titles = {}

    for item in all_news:
        text = f"{item['title']} {item['description']}"
        words = extract_keyword_candidates(text)

        for word in words:
            score[word] = score.get(word, 0) + 1
            sample_titles.setdefault(word, item["title"])

    # 너무 일반적인 검색어 제거
    for base_word in search_words:
        score.pop(base_word, None)

    ranked = sorted(score.items(), key=lambda x: x[1], reverse=True)

    recommendations = []
    used = set()

    for word, count in ranked:
        if word in used:
            continue

        if len(recommendations) >= 7:
            break

        used.add(word)

        reason_title = sample_titles.get(word, "")
        reason = f"오늘 최신 뉴스에서 반복적으로 언급된 이슈입니다."

        if reason_title:
            reason = f"관련 뉴스: {reason_title[:45]}..."

        recommendations.append({
            "category": category_label,
            "keyword": word,
            "reason": reason,
        })

    if not recommendations:
        recommendations.append({
            "category": category_label,
            "keyword": search_words[0],
            "reason": "오늘 뉴스 흐름을 기준으로 추천한 기본 키워드입니다.",
        })

    return recommendations