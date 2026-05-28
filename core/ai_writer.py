import os
import json
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv(override=True)


def get_openai_client():
    api_key = os.getenv("OPENAI_API_KEY", "").strip()

    if not api_key:
        raise ValueError("OPENAI_API_KEY가 .env 파일에 없습니다.")

    return OpenAI(api_key=api_key)


def generate_ai_post(category, keywords, writing_style, extra_prompt="", include_tags=True, make_thumbnail=True):
    style_map = {
        "practical": "실무자 관점으로 쉽게 정리",
        "issue": "최신 이슈 분석형",
        "guide": "초보자 가이드형",
        "checklist": "체크리스트형",
        "review": "리뷰/경험담형",
    }

    category_map = {
        "architecture": "건축",
        "realestate": "부동산",
        "finance": "금융",
        "tech": "테크",
        "life": "일상",
    }

    category_name = category_map.get(category, category)
    style_name = style_map.get(writing_style, "실무자 관점으로 쉽게 정리")

    prompt = f"""
너는 ChickenBanana Lab 블로그의 전문 콘텐츠 작성자다.

아래 조건에 맞춰 블로그 글 초안을 작성해라.

카테고리: {category_name}
주요 키워드: {keywords}
글 작성 방향: {style_name}
추가 요청사항: {extra_prompt}

작성 조건:
- 한국어로 작성
- 제목은 검색과 클릭을 고려해서 작성
- 본문은 HTML 형식으로 작성
- 본문에는 h2, h3, p, ul, li 태그를 적절히 사용
- 과장된 허위 정보 금지
- 애드센스 블로그에 어울리게 정보성으로 작성
- 너무 짧게 쓰지 말고 실질적인 내용을 포함
- 결과는 반드시 JSON 형식만 반환

반환 형식:
{{
  "title": "글 제목",
  "thumbnail_text": "썸네일에 넣을 짧은 문구",
  "content": "HTML 본문",
  "tags": "태그1,태그2,태그3,태그4,태그5",
  "thumbnail_prompt": "썸네일 이미지 생성용 프롬프트"
}}
"""

    client = get_openai_client()

    response = client.responses.create(
        model="gpt-5.5",
        input=prompt,
    )

    text = response.output_text.strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = {
            "title": f"{keywords} 정리",
            "thumbnail_text": keywords[:30],
            "content": text,
            "tags": keywords if include_tags else "",
            "thumbnail_prompt": "",
        }

    return {
        "title": data.get("title", f"{keywords} 정리")[:200],
        "thumbnail_text": data.get("thumbnail_text", keywords[:30])[:100],
        "content": data.get("content", ""),
        "tags": data.get("tags", "") if include_tags else "",
        "thumbnail_prompt": data.get("thumbnail_prompt", "") if make_thumbnail else "",
    }