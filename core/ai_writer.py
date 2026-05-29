import os
import re
import json
import html
import uuid
import base64

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv(override=True)


TEXT_MODEL = os.getenv("OPENAI_TEXT_MODEL", "gpt-5.5")
IMAGE_MODEL = os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-1")
IMAGE_QUALITY = os.getenv("OPENAI_IMAGE_QUALITY", "low")


def get_openai_client():
    api_key = os.getenv("OPENAI_API_KEY", "").strip()

    if not api_key:
        raise ValueError("OPENAI_API_KEY가 .env 파일에 없습니다.")

    return OpenAI(api_key=api_key)


def clamp_number(value, min_value, max_value, default):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default

    return max(min_value, min(number, max_value))


def extract_json(text):
    """
    AI 응답에서 JSON만 안전하게 추출.
    모델이 실수로 ```json 코드블록을 붙이거나 앞뒤 설명을 붙여도 최대한 파싱.
    """
    text = (text or "").strip()

    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text, flags=re.IGNORECASE).strip()
        text = re.sub(r"```$", "", text).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", text, re.DOTALL)

    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    return None


def generate_ai_post(
    category,
    keywords,
    writing_style,
    extra_prompt="",
    include_tags=True,
    make_thumbnail=True,
    image_count=0,
    planned_title="",
):
    """
    AI 블로그 글 생성:
    - 제목
    - 썸네일 문구
    - HTML 본문
    - 태그
    - 썸네일 이미지 프롬프트
    - 본문 이미지 프롬프트 + 캡션
    """
    image_count = clamp_number(image_count, 0, 5, 0)

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

    planned_title = (planned_title or "").strip()

    planned_title_instruction = ""

    if planned_title:
        planned_title_instruction = f"""
이번 글은 아래 세부 주제에 맞춰 작성해라.

세부 제목:
{planned_title}

작성 규칙:
- 제목은 가능하면 세부 제목을 그대로 사용하거나 자연스럽게 다듬어라.
- 다른 주제로 벗어나지 마라.
- 같은 키워드의 다른 글과 내용이 겹치지 않게 작성해라.
"""

    if image_count > 0:
        placeholders = ", ".join([f"[[IMAGE_{i}]]" for i in range(1, image_count + 1)])

        image_instruction = f"""
본문 중간에 이미지가 들어갈 자연스러운 위치를 골라 아래 플레이스홀더를 정확히 한 번씩 넣어라.
플레이스홀더: {placeholders}

그리고 content_images 배열에는 이미지 {image_count}장에 대한 prompt와 caption을 작성해라.

본문 이미지 prompt 조건:
- 실제 블로그 본문 중간에 들어갈 정보성 이미지 느낌
- 과도한 텍스트, 로고, 워터마크 금지
- 주제와 카테고리에 맞는 현실적이고 깔끔한 이미지
- 한국 블로그 콘텐츠에 어울리는 이미지
- 인물 얼굴 클로즈업은 피하고, 상황이나 장소가 느껴지는 이미지로 작성
- 방송 화면 캡쳐, 방송사 로고, 자막, 특정 매체 화면처럼 보이게 만들지 마라.
- 저작권 문제가 생길 수 있는 실제 방송 장면, 포털 리뷰 사진, 특정 브랜드 이미지를 묘사하지 마라.

caption 조건:
- 사진 아래에 들어갈 짧은 설명
- 20~45자 정도
- '입니다', '합니다' 같은 종결어미 쓰지 않기
- 검정 기사 문장 느낌보다 짧은 이미지 설명 느낌
- 예: 서소문 고가차도 해체 공사 구간의 안전 펜스와 통제 동선
"""
    else:
        image_instruction = """
본문에는 이미지 플레이스홀더를 넣지 마라.
content_images는 빈 배열로 반환해라.
"""

    prompt = f"""
너는 ChickenBanana Lab 블로그의 전문 콘텐츠 작성자다.

아래 조건에 맞춰 블로그 글 초안을 작성해라.

카테고리: {category_name}
주요 키워드: {keywords}
글 작성 방향: {style_name}
추가 요청사항: {extra_prompt}

{planned_title_instruction}

작성 조건:
- 한국어로 작성
- 제목은 검색과 클릭을 고려해서 작성
- 본문은 HTML 형식으로 작성
- 본문 최상단에 h1 태그는 쓰지 마라
- 과장된 허위 정보 금지
- 확인되지 않은 내용은 단정하지 말고 '알려져 있습니다', '후기에서 언급됩니다', '확인이 필요합니다'처럼 신중하게 표현
- 애드센스 블로그에 어울리게 정보성으로 작성
- 썸네일 이미지 프롬프트는 본문 content 안에 넣지 마라
- 결과는 반드시 JSON 형식만 반환

글 분량 판단 조건:
- 글은 무조건 길게 쓰지 마라.
- 주제가 간단한 생활정보, 맛집 위치, 메뉴 소개, 짧은 이슈라면 핵심만 담아 900~1,300자 정도로 작성해라.
- 주제가 맛집/여행 소개형이면 위치, 메뉴, 방문 팁, 어울리는 사람 중심으로 1,100~1,600자 정도로 작성해라.
- 주제가 비교, 분석, 교육, 사용법, 투자 리스크, 개발 방법, 건축 실무처럼 설명이 필요한 내용이면 1,500~2,500자 정도로 작성해라.
- 프로그램 사용법, 개발 튜토리얼, 자동매매 로직, 건축 실무 체크리스트처럼 단계 설명이 필요한 글은 충분히 길게 작성해라.
- 독자가 이미 아는 일반론을 길게 늘리지 마라.
- 같은 말을 반복해서 글자 수를 채우지 마라.
- 짧은 글이어도 검색자가 궁금해하는 핵심 답변은 빠뜨리지 마라.
- 긴 글은 소제목, 표, 리스트를 활용해서 읽기 쉽게 나눠라.

분량 결정 기준:
- 간단 정보형이면 짧고 명확하게 작성
- 맛집/여행 소개형이면 중간 분량으로 작성
- 비교/분석형이면 충분히 설명
- 교육/가이드형이면 단계별로 자세히 작성
- 프로그램 사용법/개발 글이면 따라 할 수 있게 길게 작성
- 금융/코인 글이면 리스크, 조건, 예시, 주의점을 포함해서 중간 이상 분량으로 작성
- 건축/시공 글이면 현장 실무 기준, 체크리스트, 비용·안전 포인트를 포함해서 충분히 작성

작성 스타일:
- 전체 톤은 사람이 직접 블로그에 쓰는 자연스러운 설명체로 작성해라.
- 보고서체, 논문체, 공공기관 안내문체, 뉴스 기사체처럼 쓰지 마라.
- 문장은 너무 정중한 "~습니다"만 반복하지 말고, "~해요", "~좋습니다", "~괜찮습니다", "~볼 만합니다"를 자연스럽게 섞어라.
- 단, 반말은 쓰지 마라.
- 독자에게 옆에서 알려주는 느낌으로 작성해라.
- 첫 문단은 너무 딱딱한 Q&A보다 자연스러운 공감 문장으로 시작해라.
- 예: 안면도 여행을 준비하다 보면 꽃게장이나 게국지 한 번쯤은 검색하게 됩니다.
- 예: 방송에 나온 맛집을 찾는 분들이라면 이 식당 이름을 한 번쯤 보셨을 거예요.
- "확인하세요", "권합니다", "달라질 수 있습니다", "방문 전 확인" 같은 표현은 글 전체에서 2~3번 이하로만 사용해라.
- 같은 표현을 반복하지 마라.
- 한 문단은 2~3줄 이내로 짧게 작성해라.
- 맛집, 여행, 생활 정보 글에서는 너무 조심스러운 문장보다 실제 검색자가 궁금해할 내용을 먼저 알려줘라.
- 직접 방문하지 않았는데 "제가 먹어봤는데", "직접 다녀왔는데", "제가 방문했을 때" 같은 허위 경험 표현은 절대 쓰지 마라.
- 대신 "후기에서 많이 언급되는 포인트", "여행 동선상 보기 좋은 점", "메뉴를 고를 때 볼 부분"처럼 자연스럽게 써라.
- 소제목은 딱딱한 질문형만 반복하지 말고 블로그식 문장으로 작성해라.
- 예: 안면도 여행 중 한 끼로 보기 좋은 이유
- 예: 꽃게장백반과 게국지, 이렇게 고르면 편합니다
- 예: 가기 전에 이 정도만 체크하면 충분합니다
- 표는 꼭 필요할 때만 1개 정도 사용해라.
- 표를 너무 많이 넣지 마라.
- 핵심 문장은 <mark class="yellow-highlight">강조문구</mark> 형태로 표시해라.
- 중요한 장소, 메뉴, 금액, 시간, 키워드는 <span class="blue-point">강조문구</span> 형태로 강조해라.
- 글 마지막은 딱딱한 결론보다 "이런 분들에게 어울립니다" 식으로 자연스럽게 정리해라.

품질 강화 조건:
- 이 글은 저품질 자동생성 글처럼 보이지 않아야 한다.
- 단, AI 사용 사실을 숨기기 위해 거짓 경험이나 허위 정보를 만들지 마라.
- 검색 결과에 이미 흔한 말만 반복하지 말고, 독자가 바로 활용할 수 있는 판단 기준을 넣어라.
- 글마다 도입부, 소제목, 표 구조, 마무리 문장을 다르게 구성해라.
- 같은 사이트의 다른 글과 비슷한 문장 패턴을 반복하지 마라.
- "이번 글에서는", "정리해보겠습니다", "확인해보세요" 같은 흔한 AI식 문구를 피하라.
- 독자가 실제로 궁금해할 만한 질문을 먼저 해결해라.
- 본문 중간에는 "이 부분은 이렇게 보면 편합니다" 같은 사람 설명형 문장을 자연스럽게 넣어라.
- 단순 정보 나열보다 선택 기준, 주의점, 실제 활용 상황을 포함해라.
- 글 마지막에는 3줄 요약보다 "이런 경우라면 이렇게 보면 됩니다" 식으로 자연스럽게 마무리해라.
- 문장 패턴을 다양하게 사용하고, 모든 문단을 같은 길이로 만들지 마라.
- 너무 완벽하게 정돈된 기계식 글보다 사람이 편집한 듯 자연스러운 흐름으로 작성해라.
- 직접 경험이 필요한 부분은 "직접 방문 후기", "실제 사용 후기"처럼 단정하지 말고 "후기에서 자주 보이는 부분"으로 표현해라.

반복 금지 표현:
- "방문 전 확인을 권합니다"를 반복하지 마라.
- "달라질 수 있습니다"를 반복하지 마라.
- "지도 앱에서 확인"을 여러 번 반복하지 마라.
- "중심으로 정리했습니다"를 반복하지 마라.
- "좋습니다"만 계속 반복하지 마라.
- 모든 소제목을 "~일까요?"로 끝내지 마라.
- "이번 글에서는", "정리해보겠습니다" 같은 AI식 도입문을 반복하지 마라.
- "결론적으로", "요약하면", "정리하면"을 글마다 반복하지 마라.
- "꼭 확인해보세요"를 남발하지 마라.

카테고리별 세부 작성 조건:
- 맛집/여행/생활 정보 글은 정보 안내문이 아니라 여행자가 읽는 블로그 글처럼 작성해라.
- 맛집 글은 메뉴 설명에 맛의 방향, 식사 상황, 누구와 가기 좋은지 등을 자연스럽게 넣어라.
- 단, 실제 맛을 단정하지 마라.
- "짭조름하게 밥과 먹기 좋은 메뉴", "여럿이 나눠 먹기 좋은 국물 메뉴"처럼 표현해라.
- 위치 설명은 길게 쓰지 말고 여행 동선 관점으로 짧게 작성해라.
- 영업시간, 휴무일, 가격 확인 문구는 마지막 체크리스트에서 한 번만 정리해라.
- 금융/코인 글은 투자 권유처럼 쓰지 말고, 리스크와 확인 포인트를 함께 넣어라.
- 건축/시공 글은 현장 실무자가 읽기 쉽게 공정, 안전, 비용, 체크포인트 중심으로 작성해라.
- 테크/프로그램 글은 초보자가 따라올 수 있게 용어를 쉽게 풀어라.
- 프로그램 다운로드가 필요한 글은 설치 전 주의사항, 사용 환경, 압축 해제, 보안 안내를 자연스럽게 넣어라.

지도/장소 링크 작성 조건:
- 식당, 여행지, 장소 링크가 필요할 때 URL 주소를 본문에 그대로 노출하지 마라.
- 구글지도 링크는 반드시 a 태그 버튼 형태로 작성해라.
- 형식은 아래처럼 작성해라.

<p class="map-link-wrap">
    <a href="https://www.google.com/maps/search/?api=1&query=장소명+지역명" class="map-link-btn" target="_blank" rel="noopener noreferrer">
        구글지도에서 보기
    </a>
</p>

- 예: 일송꽃게장백반 태안이라면 query=일송꽃게장백반+태안 형태로 작성해라.
- 네이버지도, 카카오맵은 검색어만 안내하고, 실제 클릭 버튼은 구글지도 버튼으로 제공해라.
- 같은 지도 링크를 여러 번 반복하지 마라.

본문 HTML 조건:
- content에는 h2, h3, p, ul, li, table, thead, tbody, tr, th, td, blockquote, mark, span, a 태그를 사용할 수 있다.
- script, iframe, style 태그는 절대 사용하지 마라.
- 본문 최상단에 h1 태그는 쓰지 마라.
- 표를 만들 때는 <table class="info-table"> 형태로 작성해라.
- 리스트가 필요한 경우 ul, li 태그를 사용해 읽기 쉽게 작성해라.
- 링크는 target="_blank" rel="noopener noreferrer"를 사용해라.

썸네일 이미지 prompt 조건:
- 대표 썸네일로 쓸 수 있는 이미지 프롬프트를 작성해라.
- 글 제목과 주제가 한눈에 느껴져야 한다.
- 텍스트를 넣기 좋은 여백을 포함해라.
- 로고, 워터마크, 실제 인물 얼굴 클로즈업은 피하라.
- 한국 블로그 썸네일에 어울리게 현실적이고 깔끔하게 작성해라.
- 실제 방송 화면, 방송사 로고, 자막, 포털 리뷰 사진처럼 보이는 이미지를 요청하지 마라.

{image_instruction}

반환 형식:
{{
  "title": "글 제목",
  "thumbnail_text": "썸네일에 넣을 짧은 문구",
  "content": "HTML 본문",
  "tags": "태그1,태그2,태그3,태그4,태그5",
  "thumbnail_prompt": "대표 썸네일 이미지 생성용 프롬프트",
  "content_images": [
    {{
      "prompt": "본문 이미지 생성용 프롬프트",
      "caption": "이미지 아래에 들어갈 짧은 설명"
    }}
  ]
}}
"""

    client = get_openai_client()

    response = client.responses.create(
        model=TEXT_MODEL,
        input=prompt,
    )

    text = response.output_text.strip()
    data = extract_json(text)

    if not data:
        data = {
            "title": f"{keywords} 정리",
            "thumbnail_text": keywords[:30],
            "content": text,
            "tags": keywords if include_tags else "",
            "thumbnail_prompt": "",
            "content_images": [],
        }

    content_images = data.get("content_images", [])

    if not isinstance(content_images, list):
        content_images = []

    content_images = content_images[:image_count]

    return {
        "title": str(data.get("title", f"{keywords} 정리"))[:200],
        "thumbnail_text": str(data.get("thumbnail_text", keywords[:30]))[:100],
        "content": str(data.get("content", "")),
        "tags": str(data.get("tags", "")) if include_tags else "",
        "thumbnail_prompt": str(data.get("thumbnail_prompt", "")) if make_thumbnail else "",
        "content_images": content_images,
    }


def generate_post_topics(
    category,
    keywords,
    writing_style,
    extra_prompt="",
    count=1,
    existing_titles=None,
):
    """
    여러 개의 글을 생성할 때 먼저 주제를 나눠주는 함수.
    예: 코인 자동매매 5개 → 서로 다른 세부 주제 5개 생성
    """
    count = clamp_number(count, 1, 10, 1)

    if count == 1:
        return [
            {
                "title": keywords,
                "keywords": keywords,
                "angle": extra_prompt,
                "search_intent": "정보 탐색",
                "extra_prompt": extra_prompt,
            }
        ]

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

    existing_titles = existing_titles or []
    existing_title_text = "\n".join([f"- {title}" for title in existing_titles[:20]])

    prompt = f"""
너는 ChickenBanana Lab 블로그의 콘텐츠 기획자다.

사용자가 하나의 큰 키워드로 여러 개의 글을 생성하려고 한다.
같은 내용이 반복되지 않도록 서로 다른 세부 주제 {count}개를 기획해라.

카테고리: {category_name}
큰 키워드: {keywords}
글 작성 방향: {style_name}
추가 요청사항: {extra_prompt}

이미 작성된 비슷한 제목:
{existing_title_text if existing_title_text else "- 없음"}

기획 조건:
- 총 {count}개의 주제를 만들어라.
- 각 글은 검색 의도가 서로 달라야 한다.
- 제목이 서로 비슷하면 안 된다.
- 같은 문장 구조를 반복하지 마라.
- 초보자용, 체크리스트, 비교, 리스크, 사례, 실전 방법, 주의점, 후기 분석, 방문 팁 등 관점을 나눠라.
- 제목은 블로그 검색 유입에 적합하게 작성해라.
- 너무 자극적이거나 허위성 있는 제목은 피하라.
- 같은 결론을 반복하는 글을 만들지 마라.
- 맛집/여행 주제라면 위치, 메뉴 선택, 후기 포인트, 방문 팁, 주변 코스처럼 관점을 나눠라.
- 금융/코인 주제라면 개념, 리스크, 전략, 보안, 체크리스트처럼 관점을 나눠라.
- 건축/시공 주제라면 공정, 안전, 비용, 현장 체크, 사례 분석처럼 관점을 나눠라.
- 프로그램/개발 주제라면 설치, 사용법, 오류 해결, 기능 비교, 보안 주의점처럼 관점을 나눠라.

제목 다양화 조건:
- 모든 제목을 질문형으로 만들지 마라.
- 일부는 "~정리", 일부는 "~체크포인트", 일부는 "~보기 좋은 이유", 일부는 "~주의할 점"처럼 섞어라.
- 제목이 서로 비슷한 문장 구조가 되지 않게 해라.
- 같은 키워드를 제목 맨 앞에 반복하지 마라.
- 사용자가 검색할 만한 자연스러운 롱테일 키워드를 섞어라.
- 너무 긴 제목은 피하고, 검색 결과에서 읽기 좋은 길이로 작성해라.

분량 기획 조건:
- 간단한 정보 주제는 짧게 끝낼 수 있는 글로 기획해라.
- 분석, 교육, 사용법, 투자 리스크, 건축 실무 주제는 깊게 설명할 수 있는 글로 기획해라.
- 같은 키워드라도 짧은 글과 긴 글이 섞이도록 관점을 다양화해라.

좋은 예시:
큰 키워드가 "코인 자동매매"라면:
1. 코인 자동매매란 무엇인가?
2. 자동매매 봇 만들 때 가장 먼저 확인할 것
3. 이동평균선 기반 코인 자동매매 전략
4. 거래소 API 키 보안 설정 방법
5. 자동매매에서 손절 기준이 중요한 이유

큰 키워드가 "태안 꽃게장백반"이라면:
1. 태안 안면도에서 꽃게장백반을 찾을 때 볼 만한 식당
2. 꽃게장백반과 게국지 메뉴 차이 쉽게 보기
3. 안면도 여행 동선에 넣기 좋은 꽃게 요리 코스
4. 방송 맛집 방문 전 체크하면 좋은 포인트
5. 가족 여행에서 꽃게장백반집 고를 때 보는 기준

반환은 반드시 JSON 형식만 사용해라.

반환 형식:
{{
  "topics": [
    {{
      "title": "글 제목",
      "keywords": "이 글에서 사용할 세부 키워드",
      "angle": "이 글의 핵심 방향",
      "search_intent": "독자가 이 글을 검색하는 이유",
      "extra_prompt": "본문 생성 시 추가로 지켜야 할 조건"
    }}
  ]
}}
"""

    client = get_openai_client()

    response = client.responses.create(
        model=TEXT_MODEL,
        input=prompt,
    )

    text = response.output_text.strip()
    data = extract_json(text)

    topics = []

    if data and isinstance(data.get("topics"), list):
        for item in data.get("topics", []):
            if not isinstance(item, dict):
                continue

            title = str(item.get("title", "")).strip()
            topic_keywords = str(item.get("keywords", "")).strip()
            angle = str(item.get("angle", "")).strip()
            search_intent = str(item.get("search_intent", "")).strip()
            item_extra_prompt = str(item.get("extra_prompt", "")).strip()

            if not title:
                continue

            topics.append(
                {
                    "title": title[:200],
                    "keywords": topic_keywords or title,
                    "angle": angle,
                    "search_intent": search_intent,
                    "extra_prompt": item_extra_prompt,
                }
            )

    fallback_angles = [
        "기초 개념을 쉽게 정리",
        "주의할 점과 리스크 정리",
        "실전 적용 방법 정리",
        "체크리스트 형태로 정리",
        "비교와 차이점 중심으로 정리",
        "초보자가 자주 실수하는 부분 정리",
        "최근 이슈와 연결해서 정리",
        "장단점 중심으로 정리",
        "운영 방법과 관리 포인트 정리",
        "생활형 블로그 문체로 정리",
    ]

    used_titles = {topic["title"] for topic in topics}
    index = 0

    while len(topics) < count:
        angle = fallback_angles[index % len(fallback_angles)]
        title = f"{keywords} {angle}"

        if title not in used_titles:
            topics.append(
                {
                    "title": title[:200],
                    "keywords": f"{keywords}, {angle}",
                    "angle": angle,
                    "search_intent": "정보 탐색",
                    "extra_prompt": angle,
                }
            )
            used_titles.add(title)

        index += 1

    return topics[:count]

def recommend_today_keywords(
    category="",
    today="",
    count=7,
):
    """
    AI 자동글 생성용 키워드 추천.
    실시간 뉴스 크롤링이 아니라, 오늘 날짜 기준으로 블로그에 쓸 만한 주제를 추천.
    """
    count = clamp_number(count, 3, 10, 7)

    category_map = {
        "architecture": "건축",
        "realestate": "부동산",
        "finance": "금융",
        "tech": "테크",
        "life": "일상",
        "": "전체",
        "all": "전체",
    }

    category_name = category_map.get(category, "전체")

    prompt = f"""
너는 ChickenBanana Lab 블로그의 콘텐츠 키워드 기획자다.

오늘 날짜: {today}
추천 카테고리: {category_name}

아래 사이트 카테고리에 맞춰 오늘 블로그에 작성하기 좋은 키워드 {count}개를 추천해라.

사이트 카테고리:
- 건축
- 부동산
- 금융
- 테크
- 일상

중요:
- 추천 카테고리가 "전체"가 아니라면 반드시 해당 카테고리 키워드만 추천해라.
- 추천 카테고리가 "건축"이면 건축/시공/하자/안전/공정/건설 이슈만 추천해라.
- 추천 카테고리가 "부동산"이면 부동산/전세/매매/청약/분양/정책 관련 키워드만 추천해라.
- 추천 카테고리가 "금융"이면 금융/코인/주식/자동매매/리스크/경제 관련 키워드만 추천해라.
- 추천 카테고리가 "테크"이면 AI/프로그램/앱/개발/기기/사용법 관련 키워드만 추천해라.
- 추천 카테고리가 "일상"이면 생활정보/육아/맛집/여행/지원금/후기 관련 키워드만 추천해라.
- 다른 카테고리 키워드를 섞지 마라.

추천 조건:
- 검색 유입이 생길 만한 키워드로 작성
- 너무 추상적인 키워드 금지
- 블로그 글 제목으로 확장 가능한 키워드
- 뉴스/이슈형, 정보형, 생활형 키워드를 적절히 섞기
- 금융/코인 키워드는 투자 권유가 아니라 정보/리스크/체크포인트 중심
- 맛집/방송/생활 키워드는 자연스러운 검색어 형태
- 건축/시공 키워드는 안전, 비용, 공정, 이슈 중심
- 테크 키워드는 사용법, 비교, 오류 해결, 프로그램, AI 중심
- 비슷한 키워드를 반복하지 마라.
- 선정적이거나 위험한 키워드는 제외
- 실제 최신 사실을 단정하지 말고, 글감으로 쓸 만한 검색 키워드 형태로 추천

반환은 반드시 JSON 형식만 사용해라.

반환 형식:
{{
  "keywords": [
    {{
      "keyword": "추천 키워드",
      "category": "건축/부동산/금융/테크/일상 중 하나",
      "reason": "추천 이유 한 줄"
    }}
  ]
}}
"""

    client = get_openai_client()

    response = client.responses.create(
        model=TEXT_MODEL,
        input=prompt,
    )

    text = response.output_text.strip()
    data = extract_json(text)

    results = []

    if data and isinstance(data.get("keywords"), list):
        for item in data.get("keywords", []):
            if not isinstance(item, dict):
                continue

            keyword = str(item.get("keyword", "")).strip()
            item_category = str(item.get("category", "")).strip()
            reason = str(item.get("reason", "")).strip()

            if not keyword:
                continue

            results.append({
                "keyword": keyword[:120],
                "category": item_category[:20] or "추천",
                "reason": reason[:120],
            })

    fallback_keywords = [
        {"keyword": "비트코인 자동매매 주의할 점", "category": "금융", "reason": "자동매매 관심층 유입용"},
        {"keyword": "코인 API 키 보안 설정 방법", "category": "금융", "reason": "실전 사용자가 검색하기 좋은 주제"},
        {"keyword": "Django 블로그 만들기 초보 가이드", "category": "테크", "reason": "개발 과정 콘텐츠로 확장 가능"},
        {"keyword": "AI 자동 글쓰기 블로그 운영 방법", "category": "테크", "reason": "사이트 방향과 맞는 주제"},
        {"keyword": "건설현장 안전관리 체크리스트", "category": "건축", "reason": "실무형 검색 유입 가능"},
        {"keyword": "아파트 하자보수 체크포인트", "category": "건축", "reason": "생활형 건축 콘텐츠"},
        {"keyword": "부동산 전세 계약 전 확인사항", "category": "부동산", "reason": "검색 수요가 꾸준한 주제"},
        {"keyword": "아이폰 맥북 연동 사용법", "category": "테크", "reason": "테크 생활형 콘텐츠"},
        {"keyword": "육아휴직 급여 신청 방법", "category": "일상", "reason": "생활 정보형 검색 주제"},
        {"keyword": "방송 맛집 방문 전 체크할 점", "category": "일상", "reason": "맛집 글 확장 가능"},
    ]

    index = 0

    while len(results) < count:
        results.append(fallback_keywords[index % len(fallback_keywords)])
        index += 1

    return results[:count]

def generate_image_bytes(prompt, size="1024x1024"):
    """
    OpenAI 이미지 생성.
    반환값: 이미지 bytes
    """
    prompt = (prompt or "").strip()

    if not prompt:
        return None

    client = get_openai_client()

    response = client.images.generate(
        model=IMAGE_MODEL,
        prompt=prompt,
        size=size,
        quality=IMAGE_QUALITY,
    )

    if not response.data:
        return None

    b64_image = response.data[0].b64_json

    if not b64_image:
        return None

    return base64.b64decode(b64_image)


def make_generated_image_file(prompt, prefix="ai-image"):
    """
    Post.thumbnail.save()에 넣을 수 있는 파일 객체 생성.
    """
    image_bytes = generate_image_bytes(prompt)

    if not image_bytes:
        return None, None

    safe_prefix = re.sub(r"[^a-zA-Z0-9_-]", "-", prefix)
    filename = f"{safe_prefix}-{uuid.uuid4().hex}.png"

    return filename, ContentFile(image_bytes)


def save_inline_image(prompt, prefix="inline"):
    """
    본문 중간 이미지 생성 후 media/post_inline_images/에 저장.
    반환값: 이미지 URL
    """
    image_bytes = generate_image_bytes(prompt)

    if not image_bytes:
        return ""

    safe_prefix = re.sub(r"[^a-zA-Z0-9_-]", "-", prefix)
    filename = f"post_inline_images/{safe_prefix}-{uuid.uuid4().hex}.png"

    saved_path = default_storage.save(filename, ContentFile(image_bytes))

    return default_storage.url(saved_path)


def build_inline_image_html(image_url, caption):
    """
    본문에 삽입할 이미지 HTML 생성.
    캡션은 사진 아래 가운데 회색 글씨로 CSS 적용.
    """
    safe_url = html.escape(image_url or "")
    safe_caption = html.escape(caption or "")

    if not safe_url:
        return ""

    return f"""
<div class="ai-inline-image-block">
    <img src="{safe_url}" alt="{safe_caption}">
    <p class="ai-inline-image-caption">{safe_caption}</p>
</div>
"""


def replace_image_placeholders(content, image_blocks):
    """
    content 안의 [[IMAGE_1]], [[IMAGE_2]] 같은 위치에 이미지 HTML 삽입.
    만약 AI가 플레이스홀더를 빼먹으면 글 맨 아래에 이미지 추가.
    """
    updated_content = content or ""

    for index, image_block in enumerate(image_blocks, start=1):
        marker = f"[[IMAGE_{index}]]"

        html_block = build_inline_image_html(
            image_block.get("url", ""),
            image_block.get("caption", ""),
        )

        if not html_block:
            continue

        if marker in updated_content:
            updated_content = updated_content.replace(marker, html_block, 1)
        else:
            updated_content += html_block

    updated_content = re.sub(r"\[\[IMAGE_\d+\]\]", "", updated_content)

    return updated_content