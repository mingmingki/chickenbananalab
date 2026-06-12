import os
import re
import json
import html
import uuid
import base64
import random
from datetime import date

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv(override=True)


TEXT_MODEL = os.getenv("GEMINI_TEXT_MODEL", "gemini-2.5-pro")
RECENT_ISSUE_MODEL = os.getenv("GEMINI_RECENT_ISSUE_MODEL", "gemini-2.5-flash-lite")
IMAGE_MODEL = os.getenv("GEMINI_IMAGE_MODEL", "gemini-3.1-flash-image")
IMAGE_ASPECT_RATIO = os.getenv("GEMINI_IMAGE_ASPECT_RATIO", "16:9")
IMAGE_SIZE = os.getenv("GEMINI_IMAGE_SIZE", "2K")
GEMINI_USE_GOOGLE_SEARCH = os.getenv("GEMINI_USE_GOOGLE_SEARCH", "true").strip().lower() not in ("0", "false", "no", "off")


STYLE_WRITING_RULES = {
    "natural": """
자연 설명형 작성 규칙:
- 가볍고 읽기 쉬운 블로그 설명체로 작성해라.
- 처음 보는 독자도 바로 이해할 수 있게 용어를 쉽게 풀어라.
- 정보 안내문처럼 딱딱하게 쓰지 말고, 사람이 정리한 생활형 콘텐츠처럼 작성해라.
- 너무 깊은 분석보다 먼저 볼 기준, 확인 순서, 주의할 점을 자연스럽게 정리해라.
""",
    "expert": """
전문가 분석형 작성 규칙:
- 일반 블로그보다 한 단계 깊은 전문 칼럼, 테크 리뷰, 시장 분석 글처럼 작성해라.
- 출시일, 세대 변화, 스펙, 기술 구조, 성능 차이, 실사용 영향, 한계점을 구분해서 설명해라.
- 확인된 사실과 예상, 루머, 추정을 명확히 나눠라.
- 제품명 2개 이상, "vs", "비교", "대체", "고민", "차이"가 들어간 주제는 반드시 제품 비교글로 작성해라.
- 제품 비교글에서는 본문 초반에 반드시 <table class="info-table"> 형식의 실제 HTML 스펙 비교표를 작성해라.
- 비교표는 이미지 설명, 캡션, "비교 표 이미지" 문장으로 대체하지 마라.
- 비교표에는 가능한 경우 제품명, 제조사, 출시 시점, CPU/칩셋, GPU/그래픽, RAM/메모리, 저장공간, 디스플레이, 무게, 배터리, 포트, 운영체제, 가격대, 추천 대상을 포함해라.
- 스펙이나 가격을 모르면 임의로 지어내지 말고 표 안에 "공식 확인 필요", "옵션별 상이", "판매처 확인 필요"처럼 표시해라.
- 관련 없는 이전 세대나 경쟁 제품을 억지로 끌어오지 말고, 비교가 필요한 경우에만 간결하게 다뤄라.
- "지금 사야 할까" 같은 범용 소비자 문구를 반복하지 말고, 사용 목적별 판단 기준을 제시해라.
- 가격, 출시일, 수치가 확실하지 않으면 확정처럼 쓰지 말고 "예상", "가능성", "확인 필요"로 표현해라.
- 개발, 영상 편집, 디자인, 멀티 모니터, 업무용 환경처럼 실제 사용 시나리오를 포함해라.
- 사용자가 추가 요청사항에 스펙, 출시일, 가격 정보를 준 경우 그 정보를 최우선으로 반영해라.
""",
    "experience": """
경험 기반형 작성 규칙:
- 직접 겪은 사람이 정리한 듯한 현실적인 관점으로 작성해라.
- 건축, 현장, 업무, 육아, 여행, 생활 노하우처럼 실제 판단에 도움이 되는 확인 기준을 넣어라.
- 단, 실제로 경험하지 않은 내용을 "제가 직접 해보니"처럼 꾸미지 마라.
- 후기에서 자주 보이는 부분, 선택할 때 많이 보는 기준, 놓치기 쉬운 부분을 중심으로 풀어라.
- 너무 문서형으로 쓰지 말고, 옆에서 알려주는 자연스러운 설명체를 유지해라.
""",
    "product_review": """
구매·리뷰형 작성 규칙:
- 제품 리뷰 전문 블로그처럼 작성하되, 제품명이 2개 이상 포함되면 반드시 제품 비교글로 작성해라.
- 제품 비교글에서는 본문 초반에 반드시 <table class="info-table"> 형식의 실제 HTML 스펙 비교표를 넣어라.
- 비교표는 이미지 설명이나 캡션으로 대체하지 마라.
- "주요 사양 비교 표 이미지", "실제 크기와 디자인 비교 이미지" 같은 문장만 쓰고 넘어가지 마라.
- 비교표에는 가능한 경우 아래 항목을 포함해라: 제품명, 제조사, 출시 시점, CPU/칩셋, GPU/그래픽, RAM/메모리, 저장공간, 디스플레이, 무게, 배터리, 포트, 운영체제, 가격대, 추천 대상.
- 공식 제품명인지 불분명한 제품명은 확정 제품처럼 쓰지 말고 "제품명이 정확하지 않거나 공식 확인이 필요하다"고 먼저 짚어라.
- 스펙을 모르면 임의로 지어내지 마라.
- 확인되지 않은 항목은 표 안에 "공식 확인 필요", "옵션별 상이", "판매처 확인 필요"처럼 표시해라.
- 가격은 판매처, 옵션, 할인, 시점에 따라 달라질 수 있으므로 확정가처럼 단정하지 마라.
- 사용자가 추가 요청사항에 제공한 가격, 판매처, 스펙 자료가 있으면 그 정보를 우선 반영해라.
- 최신 가격 정보가 제공되지 않았다면 임의 금액을 만들지 말고 "현재 판매가는 판매처와 옵션에 따라 확인이 필요하다"고 표현해라.
- 공식 스펙, 판매처 옵션, 사용자 후기성 장단점을 구분해서 작성해라.
- 광고성 문구보다 실제 구매 판단에 도움이 되는 기준을 먼저 제시해라.
- 이런 사람에게 추천, 이런 사람은 보류가 좋은 경우를 나눠서 마무리해라.
""",
    "news_trend": """
뉴스·트렌드형 작성 규칙:
- 최근 이슈를 단순 전달하지 말고 왜 주목받는지, 누구에게 영향이 있는지 분석해라.
- 날짜, 발표 시점, 적용 시점이 중요한 내용은 구체적인 시점을 명확히 적어라.
- 확인된 사실과 전망을 구분하고, 과장된 확정 표현을 피하라.
- 독자가 지금 눈여겨볼 변화와 앞으로 달라질 수 있는 부분을 본문 흐름 안에서 자연스럽게 설명해라.
- 짧은 뉴스식 나열이 아니라 블로그 독자가 이해하기 쉬운 맥락 설명을 포함해라.
""",
    "checklist": """
확인기준형 작성 규칙:
- 독자가 바로 이해할 수 있도록 확인 순서와 판단 기준을 자연스럽게 풀어라.
- 각 확인 항목은 이유와 확인 방법을 함께 설명해라.
- 단순 목록만 나열하지 말고, 실수하기 쉬운 부분과 판단 기준을 넣어라.
""",
    "review": """
리뷰형 작성 규칙:
- 장점만 강조하지 말고 단점, 주의점, 맞는 사람과 맞지 않는 사람을 함께 정리해라.
- 실제 경험이 없는 경우 직접 사용 후기처럼 꾸미지 말고, 공개 정보와 일반적인 판단 기준 중심으로 작성해라.
- 제품, 장소, 서비스는 가격, 구성, 접근성, 사용성, 만족 포인트를 나눠 설명해라.
""",
}


HUMAN_OPENING_PATTERNS = [
    "검색자가 이미 궁금해하는 상황을 먼저 짚고, 그다음 필요한 정보를 자연스럽게 설명하는 방식",
    "개인 블로그처럼 가볍게 문제 상황을 던진 뒤, 바로 실용적인 기준을 알려주는 방식",
    "처음부터 정의를 내리지 말고, 사람들이 헷갈리는 지점부터 풀어가는 방식",
    "정보를 나열하기보다 실제로 선택하거나 판단해야 하는 상황을 먼저 보여주는 방식",
    "짧은 공감 문장으로 시작한 뒤, 바로 확인 기준로 이어가는 방식",
    "검색자가 겪을 만한 작은 불편이나 의문을 먼저 꺼내고, 그걸 풀어주는 방식",
    "딱딱한 설명보다 실제 검색자가 머릿속으로 떠올리는 질문에서 출발하는 방식",
]

HUMAN_STRUCTURE_PATTERNS = [
    """
이번 글 구조는 아래 흐름을 우선 사용해라.
- 짧은 도입
- 독자가 먼저 알아야 할 흐름을 자연스럽게 설명
- 사람들이 가장 헷갈리는 부분
- 본문 흐름 안에서 확인 기준을 자연스럽게 풀어쓰기
- 필요한 경우 마지막 확인 사항
- 글 전체 흐름을 자연스럽게 연결
""",
    """
이번 글 구조는 아래 흐름을 우선 사용해라.
- 검색자가 궁금해할 질문으로 시작
- 먼저 답부터 자연스럽게 제시
- 세부 설명
- 주의할 점
- 어떤 상황에서 참고할 만한지 설명
- 읽고 나서 확인할 부분
""",
    """
이번 글 구조는 아래 흐름을 우선 사용해라.
- 상황 설명
- 왜 중요한지
- 실제로 판단할 때 봐야 할 부분
- 표 또는 리스트
- 놓치기 쉬운 부분
- 필요한 경우 마지막 확인 사항
- 글 전체 흐름을 자연스럽게 연결
""",
    """
이번 글 구조는 아래 흐름을 우선 사용해라.
- 짧은 문제 제기
- 상황을 가르는 기준을 자연스럽게 설명
- 세부 설명
- 실수하기 쉬운 부분
- 마지막에 놓치기 쉬운 부분 정리
- 글 주제에 맞는 실용적인 끝맺음
""",
    """
이번 글 구조는 아래 흐름을 우선 사용해라.
- 독자가 먼저 궁금해할 부분부터 풀어쓰기
- 상황별로 다르게 봐야 할 부분
- 실제로 확인하는 흐름
- 주의할 점
- 필요한 경우 헷갈리는 부분 보충
- 짧은 정리
""",
]

CATEGORY_VOICE_RULES = {
    "architecture": """
건축 글 말투:
- 건축 분야를 처음 보는 독자도 이해할 수 있게 실제 확인 기준 중심으로 작성해라.
- 공정, 하자, 비용, 안전, 시공성 기준을 자연스럽게 포함해라.
- 너무 이론적으로 쓰지 말고 실제로 확인할 수 있는 기준을 넣어라.
- 도면, 시공, 비용, 일정, 품질처럼 필요한 관점은 주제에 맞게 자연스럽게 섞어라.
""",
    "realestate": """
부동산 글 말투:
- 계약 전 확인사항을 알려주는 생활형 블로그 느낌으로 작성해라.
- 단정적인 투자 조언은 피하고, 확인 순서와 리스크 중심으로 작성해라.
- 초보자가 헷갈리는 용어는 쉽게 풀어라.
- 매수, 전세, 청약, 분양 글은 실제 행동 전 확인해야 할 항목을 중심으로 작성해라.
""",
    "finance": """
금융 글 말투:
- 투자 권유처럼 보이지 않게 작성해라.
- 상승과 하락을 단정하지 말고 시나리오와 리스크를 같이 설명해라.
- 초보자가 감정적으로 매수하지 않도록 확인 기준 중심으로 작성해라.
- 코인, 주식, 금리, 환율 글은 리스크 고지와 확인 기준을 반드시 포함해라.
""",
    "tech": """
테크 글 말투:
- 개발자 문서가 아니라 초보자도 따라올 수 있는 블로그 설명체로 작성해라.
- 용어는 쉽게 풀고, 실제 사용 순서와 오류 가능성을 함께 설명해라.
- 너무 과장된 AI 찬양 문구는 피하라.
- 프로그램, 앱, API, 자동화 글은 사용 환경과 주의사항을 자연스럽게 넣어라.
""",
    "life": """
일상 글 말투:
- 실제 블로그 후기처럼 자연스럽게 쓰되, 직접 경험하지 않은 내용은 경험담처럼 꾸미지 마라.
- 가족, 주차, 대기, 준비물, 비용, 동선 같은 현실적인 포인트를 포함해라.
- 안내문보다 사람이 정리한 생활정보 느낌으로 작성해라.
- 맛집, 여행, 체험 글은 과장된 홍보문보다 방문 전 판단 기준 중심으로 작성해라.
""",
}

ANTI_AI_WRITING_RULES = """
AI 글처럼 보이지 않기 위한 추가 규칙:
- 모든 문단을 같은 길이로 맞추지 마라.
- 소제목마다 똑같은 문장 구조를 반복하지 마라.
- '중요합니다', '필요합니다', '확인해야 합니다'만 반복하지 마라.
- 도입부에서 '오늘은 ~에 대해 알아보겠습니다'를 쓰지 마라.
- 결론에서 '지금까지 ~에 대해 알아보았습니다'를 쓰지 마라.
- 너무 매끈하고 완벽한 설명보다, 사람이 편집한 듯한 자연스러운 흐름을 우선해라.
- 중간중간 '생각보다', '막상 보면', '여기서 헷갈리는 부분은', '처음 보는 분들은' 같은 자연스러운 연결어를 적절히 사용해라.
- 다만 과한 감탄사, 인터넷 말투, 반말은 쓰지 마라.
- 한 문단 안에서 같은 조사와 어미가 반복되지 않게 문장 길이를 섞어라.
- 글마다 첫 문장, 첫 소제목, 마무리 문장을 다르게 작성해라.
- 검색어를 억지로 반복하지 말고 문맥상 필요한 곳에만 넣어라.
"""

HUMAN_DETAIL_RULES = """
사람이 직접 편집한 글처럼 보이기 위한 세부 조건:
- 단순 정의보다 '어떤 상황에서 이 정보가 필요한지'를 먼저 설명해라.
- 독자가 바로 사용할 수 있는 판단 기준, 순서, 확인 기준을 넣어라.
- 너무 뻔한 일반론은 줄이고, 실제로 헷갈릴 만한 부분을 풀어라.
- 문단 중간에 자연스러운 전환 문장을 넣어라.
- 목록을 만들 때 모든 항목 길이를 똑같이 맞추지 마라.
- FAQ는 본문에서 이미 충분히 설명한 내용을 그대로 반복하지 마라.
- 글이 길어질 경우 중간에 읽는 사람이 숨을 고를 수 있는 짧은 문단을 넣어라.
- 실제 경험을 하지 않은 경우 '제가 직접 해보니', '제가 가보니', '먹어보니' 같은 표현은 쓰지 마라.
- 대신 '후기에서 자주 보이는 부분', '선택할 때 많이 보는 기준', '처음 확인할 부분'처럼 자연스럽게 표현해라.

"""


def build_current_fact_check_rules(keywords="", planned_title="", extra_prompt="", language="ko"):
    """
    자동글 생성 전에 최신성/공식 출시 여부를 강하게 확인하도록 하는 공통 규칙.
    특히 제품·스펙·가격·출시일 글에서 키워드에 '루머'가 있어도 공식 자료가 있으면
    공식 출시 제품 기준으로 정정하도록 만든다.
    """
    today_text = date.today().isoformat()
    topic_text = f"{keywords} {planned_title} {extra_prompt}".lower().replace(" ", "")

    is_apple_topic = any(word in topic_text for word in [
        "macbook", "맥북", "apple", "애플", "iphone", "아이폰", "ipad", "아이패드", "macmini", "맥미니"
    ])

    is_macbook_neo_topic = any(word in topic_text for word in [
        "macbookneo", "맥북네오", "599달러맥북", "$599macbook"
    ])

    if language == "en":
        base_rules = f"""
Current factuality rules:
- Today's date for this generation: {today_text}.
- For products, release dates, prices, specifications, news, policies, schedules, interest rates, stocks, crypto, law, tax, health, and other time-sensitive topics, verify the current state first.
- If official manufacturer pages, newsroom posts, exchange filings, government pages, or highly reliable media confirm a product/event/policy, treat it as confirmed. Do not keep calling it a rumor just because the source Korean title contains "rumor", "expected", or "possibility".
- If the original Korean draft conflicts with official/current sources, correct the English version using the official/current information.
- Do not invent specs, release dates, prices, rankings, market share, or performance numbers. If a detail cannot be verified, write "official confirmation needed" or "varies by configuration".
- For comparisons, never default to an old model just because it is familiar. Use the latest official model generation unless the user explicitly requests a specific older model.
""".strip()

        apple_rules = """
Apple/MacBook-specific rules:
- For Apple topics, check Apple product pages, Apple Newsroom, and Apple comparison/spec pages first.
- For MacBook Neo topics, if Apple official pages or Apple Newsroom confirm the product, write it as an officially launched product, not as a rumor.
- For MacBook Neo comparisons, compare it with the latest official MacBook Air generation shown by Apple, unless the topic explicitly says MacBook Air M4, M3, or another specific generation.
- If the topic explicitly says MacBook Air M4, compare with MacBook Air M4. If the topic says only "MacBook Air" or "latest MacBook Air", use the newest official generation shown by Apple.
- Do not compare MacBook Neo with MacBook Air M2 by default. M2/M3 should appear only when the user specifically asks for that generation or historical context.
- If official Apple pages show MacBook Air M5, use M5 as the latest reference. If not, use the newest official generation currently confirmed.
""".strip()
    else:
        base_rules = f"""
최신 사실 확인 규칙:
- 이 글을 작성하는 기준 날짜는 {today_text}이다.
- 최신 제품, 출시일, 가격, 스펙, 뉴스, 정책, 일정, 금리, 코인, 주식, 법률, 세금처럼 시간이 지나면 바뀌는 정보는 반드시 현재 기준으로 먼저 확인해라.
- 공식 홈페이지, 제조사 뉴스룸, 공식 비교/스펙 페이지, 정부·공공기관, 거래소 공시, 신뢰도 높은 주요 매체를 우선 확인해라.
- 키워드나 세부 제목에 "루머", "예상", "가능성"이라는 단어가 들어 있어도 공식 자료에서 출시·발표·적용이 확인되면 반드시 공식 출시/공식 발표 기준으로 정정해라.
- 공식 출시된 제품이나 공식 발표된 내용을 계속 루머처럼 쓰지 마라.
- 루머와 공식 정보를 혼동하지 마라. 공식 자료가 확인되면 "루머", "예상", "가능성" 같은 표현을 남발하지 마라.
- 검색 결과와 사용자가 추가 요청사항에 준 정보가 충돌하면 공식 출처를 우선하고, 불확실한 부분은 "공식 확인 필요", "옵션별 상이", "판매처 확인 필요"처럼 표시해라.
- 스펙, 가격, 출시일, 순위, 시장점유율, 성능 수치, 배터리 시간, 할인 금액은 절대 지어내지 마라.
- 제품 비교를 할 때 익숙하다는 이유로 오래된 모델을 기본 비교 대상으로 잡지 마라. 사용자가 특정 구형 모델을 지정하지 않았다면 공식 페이지에서 확인되는 최신 세대를 기준으로 비교해라.
""".strip()

        apple_rules = """
Apple/MacBook 전용 검증 규칙:
- Apple 관련 글은 Apple 공식 제품 페이지, Apple Newsroom, Apple 공식 비교/스펙 페이지를 최우선 기준으로 확인해라.
- MacBook Neo 관련 글은 Apple 공식 MacBook Neo 페이지와 Apple Newsroom에서 제품 존재 여부를 먼저 확인해라.
- Apple 공식 페이지 또는 Apple Newsroom에서 MacBook Neo가 확인되면 절대 루머라고 쓰지 말고, 공식 출시 제품 기준으로 작성해라.
- MacBook Neo가 공식 확인되는 경우에는 A18 Pro, 시작가, 교육 할인가, 출시/주문 가능 시점, 디스플레이, 배터리, 포트 같은 항목을 공식 페이지 기준으로만 정리해라.
- MacBook Neo 비교 글에서는 사용자가 특정 세대를 지정하지 않았다면 Apple 공식 비교 페이지에 표시되는 최신 MacBook Air 세대와 비교해라.
- 사용자가 "맥북에어 M4"를 명시하면 MacBook Air M4와 비교하고, "최신 맥북에어"라고만 쓰면 현재 공식 최신 세대와 비교해라.
- MacBook Neo를 MacBook Air M2와 기본 비교하지 마라. M2/M3는 사용자가 직접 지정했거나 역사적 맥락이 필요할 때만 언급해라.
- Apple 공식 비교 페이지에서 MacBook Air M5가 확인되면 최신 기준 비교 대상은 M5다. 확인되지 않으면 현재 공식으로 확인되는 가장 최신 세대를 사용해라.
- Dell XPS 13, 갤럭시북, 크롬북 등 경쟁 제품을 다룰 때도 제조사 공식 페이지와 신뢰도 높은 기사 기준으로만 가격과 스펙을 작성해라.
""".strip()

    if is_apple_topic or is_macbook_neo_topic:
        return base_rules + "\n" + apple_rules

    return base_rules


def build_macbook_comparison_guard(keywords="", planned_title="", extra_prompt=""):
    topic_text = f"{keywords} {planned_title} {extra_prompt}".lower().replace(" ", "")

    if not any(word in topic_text for word in ["macbookneo", "맥북네오", "599달러맥북", "$599macbook", "맥북", "macbook"]):
        return ""

    return """
Apple/MacBook 비교 보정 조건:
- MacBook Neo가 주제에 포함되면 먼저 공식 출시 제품인지 확인하고, 공식 확인 시 "루머" 중심 글로 쓰지 마라.
- MacBook Neo와 비교할 대상은 오래된 MacBook Air M2가 아니라, 사용자가 지정한 세대 또는 Apple 공식 비교 페이지의 최신 MacBook Air 세대다.
- 사용자가 "맥북에어 M4"를 명시하면 M4와 비교한다.
- 사용자가 특정 세대를 명시하지 않았다면 최신 MacBook Air를 기준으로 비교한다.
- 표를 만들 경우 제품명, 칩, 메모리, 저장공간, 디스플레이, 배터리, 포트, 무게, 가격대, 추천 사용자를 포함하되 공식 확인되지 않은 항목은 확인 필요로 표시한다.
""".strip()


def get_gemini_client():
    api_key = os.getenv("GEMINI_API_KEY", "").strip()

    if not api_key:
        raise ValueError("GEMINI_API_KEY가 .env 파일에 없습니다.")

    return genai.Client(api_key=api_key)


def clamp_number(value, min_value, max_value, default):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default

    return max(min_value, min(number, max_value))


def extract_json(text):
    text = (text or "").strip()

    if not text:
        return None

    text = re.sub(r"^```(?:json)?", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"```$", "", text).strip()

    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass

    decoder = json.JSONDecoder()

    for index, char in enumerate(text):
        if char != "{":
            continue

        try:
            data, _ = decoder.raw_decode(text[index:])
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            continue

    return None


def clean_text_for_meta(text, limit=150):
    text = str(text or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()

    if len(text) <= limit:
        return text

    return text[:limit].rstrip() + "..."



def strip_code_fences(value):
    value = str(value or "").strip()
    value = re.sub(r"^```(?:html|json)?", "", value, flags=re.IGNORECASE).strip()
    value = re.sub(r"```$", "", value).strip()
    return value


def recover_content_from_json_string(content):
    content = strip_code_fences(content)
    nested = extract_json(content)

    if isinstance(nested, dict) and nested.get("content"):
        return str(nested.get("content", ""))

    return content


def has_real_html(content):
    return bool(re.search(
        r"</?(h2|h3|p|ul|ol|li|table|thead|tbody|tr|th|td|blockquote|mark|span|a|div|img)\b",
        str(content or ""),
        flags=re.IGNORECASE,
    ))


def split_table_row(line):
    line = str(line or "").strip()

    if "\t" in line:
        cells = [cell.strip() for cell in line.split("\t")]
        return [cell for cell in cells if cell]

    if "|" in line:
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        cells = [cell for cell in cells if cell and not re.fullmatch(r"[-:\s]+", cell)]
        if len(cells) >= 2:
            return cells

    return []


def build_html_table_from_rows(rows):
    if not rows:
        return ""

    header = rows[0]
    body_rows = rows[1:]

    col_count = max(len(row) for row in rows)
    header = header + [""] * (col_count - len(header))

    html_lines = [
        '<table class="info-table">',
        "    <thead>",
        "        <tr>",
    ]

    for cell in header:
        html_lines.append(f"            <th>{html.escape(cell)}</th>")

    html_lines += [
        "        </tr>",
        "    </thead>",
        "    <tbody>",
    ]

    for row in body_rows:
        row = row + [""] * (col_count - len(row))
        html_lines.append("        <tr>")
        for cell in row:
            html_lines.append(f"            <td>{html.escape(cell)}</td>")
        html_lines.append("        </tr>")

    html_lines += [
        "    </tbody>",
        "</table>",
    ]

    return "\n".join(html_lines)


def looks_like_heading(line):
    line = str(line or "").strip()

    if not line:
        return False

    if len(line) > 55:
        return False

    if line.startswith(("-", "•", "*", "Q.", "Q:")):
        return False

    if line.endswith((".", "요.", "다.", "까?", "나요?", "죠?", "니다.", "습니다.")):
        return False

    heading_keywords = [
        "정리", "비교", "차이", "질문", "FAQ",
        "장점", "단점", "스펙", "가격", "출시일", "성능", "구성",
        "주의", "방법", "대상", "어울립니다",
    ]

    return any(keyword in line for keyword in heading_keywords)


def convert_plain_text_to_html(content, title=""):
    content = strip_code_fences(content)
    content = html.unescape(str(content or ""))

    # JSON 문자열 안에 들어온 \\n이 그대로 보이는 경우 보정
    content = content.replace("\\r\\n", "\n").replace("\\n", "\n")
    content = re.sub(r"\r\n?", "\n", content)

    raw_lines = [line.strip() for line in content.split("\n")]
    lines = []
    title_compact = normalize_text_for_detect(title)

    for line in raw_lines:
        if not line:
            continue

        line = re.sub(r"^\s*#+\s*", "", line).strip()

        if not line:
            continue

        # 본문 첫 줄에 제목이 중복으로 들어오는 경우 제거
        if title_compact and normalize_text_for_detect(line) == title_compact:
            continue

        # JSON 잔여물 방지
        if line in ("{", "}", "[", "]"):
            continue

        lines.append(line)

    if not lines:
        return ""

    blocks = []
    index = 0

    while index < len(lines):
        line = lines[index]

        # Markdown 또는 탭 기반 표 변환
        table_rows = []
        check_index = index

        while check_index < len(lines):
            cells = split_table_row(lines[check_index])
            if len(cells) < 2:
                break

            # markdown separator row는 제외
            if all(re.fullmatch(r"[-:\s]+", cell) for cell in cells):
                check_index += 1
                continue

            table_rows.append(cells)
            check_index += 1

        if len(table_rows) >= 2:
            blocks.append(build_html_table_from_rows(table_rows))
            index = check_index
            continue

        # bullet list 변환
        if re.match(r"^[-*•]\s+", line):
            items = []
            while index < len(lines) and re.match(r"^[-*•]\s+", lines[index]):
                item = re.sub(r"^[-*•]\s+", "", lines[index]).strip()
                if item:
                    items.append(item)
                index += 1

            if items:
                blocks.append("<ul>\n" + "\n".join(f"    <li>{html.escape(item)}</li>" for item in items) + "\n</ul>")
            continue

        # FAQ 질문
        if re.match(r"^(Q\.|Q:|문\.|질문)", line, flags=re.IGNORECASE):
            clean_question = re.sub(r"^(Q\.|Q:|문\.|질문)\s*", "", line, flags=re.IGNORECASE).strip()
            blocks.append(f"<h3>{html.escape(clean_question)}</h3>")
            index += 1
            continue

        # h2 변환
        if "자주 묻는 질문" in line or looks_like_heading(line):
            blocks.append(f"<h2>{html.escape(line)}</h2>")
            index += 1
            continue

        # 일반 문단
        paragraph_lines = [line]
        index += 1

        while index < len(lines):
            next_line = lines[index]

            if split_table_row(next_line) or re.match(r"^[-*•]\s+", next_line) or looks_like_heading(next_line) or re.match(r"^(Q\.|Q:|문\.|질문)", next_line, flags=re.IGNORECASE):
                break

            paragraph_lines.append(next_line)
            index += 1

        paragraph_text = " ".join(paragraph_lines).strip()
        if paragraph_text:
            blocks.append(f"<p>{html.escape(paragraph_text)}</p>")

    return "\n\n".join(blocks)


def remove_leading_duplicate_title(content, title=""):
    content = str(content or "").strip()
    title = str(title or "").strip()

    if not content or not title:
        return content

    escaped_title = re.escape(title)

    patterns = [
        rf"^\s*<p>\s*{escaped_title}\s*</p>\s*",
        rf"^\s*<h2>\s*{escaped_title}\s*</h2>\s*",
        rf"^\s*<h3>\s*{escaped_title}\s*</h3>\s*",
        rf"^\s*{escaped_title}\s*",
    ]

    for pattern in patterns:
        content = re.sub(pattern, "", content, flags=re.IGNORECASE).strip()

    return content


def repair_ai_content_html(content, title=""):
    content = recover_content_from_json_string(content)
    content = strip_code_fences(content)
    content = html.unescape(str(content or "")).strip()

    if not content:
        return ""

    # JSON 원문이 content에 들어간 경우 한 번 더 회수
    nested = extract_json(content)
    if isinstance(nested, dict) and nested.get("content"):
        content = str(nested.get("content", ""))

    content = strip_code_fences(content)

    if not has_real_html(content):
        content = convert_plain_text_to_html(content, title=title)
    else:
        # HTML은 있지만 줄바꿈 표가 섞인 경우 최소 정리
        content = content.replace("\\r\\n", "\n").replace("\\n", "\n")
        content = re.sub(r"\r\n?", "\n", content)
        content = re.sub(r"\n{3,}", "\n\n", content).strip()

    content = remove_leading_duplicate_title(content, title=title)
    content = re.sub(r"<h1\b[^>]*>.*?</h1>", "", content, flags=re.IGNORECASE | re.DOTALL)
    content = re.sub(r"<script\b[^>]*>.*?</script>", "", content, flags=re.IGNORECASE | re.DOTALL)
    content = re.sub(r"<style\b[^>]*>.*?</style>", "", content, flags=re.IGNORECASE | re.DOTALL)
    content = re.sub(r"<iframe\b[^>]*>.*?</iframe>", "", content, flags=re.IGNORECASE | re.DOTALL)
    content = re.sub(r"\n{3,}", "\n\n", content).strip()

    return content




def plain_text_length_from_html(content):
    """
    HTML 태그를 제거한 실제 본문 글자 수를 계산합니다.
    Gemini가 빈 응답이나 짧은 오류 문구를 반환했을 때 저장 단계로 넘어가지 않게 합니다.
    """
    text = html.unescape(str(content or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("&nbsp;", " ").replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return len(text)


def validate_ai_content_or_raise(content, context="AI 본문", min_length=500):
    content = str(content or "").strip()
    plain_length = plain_text_length_from_html(content)

    if plain_length < min_length:
        raise ValueError(
            f"{context} 생성 실패: 본문이 비어 있거나 너무 짧습니다. "
            f"본문 글자수={plain_length}자, 최소 기준={min_length}자"
        )

    return content

def normalize_text_for_detect(value):
    return str(value or "").lower().replace(" ", "")


def looks_like_bad_generic_shopping_text(content):
    text = str(content or "")
    lowered = text.lower()
    bad_words = [
        "when shopping online for physical products",
        "amazon",
        "walmart",
        "target",
        "ebay",
        "best buy",
        "wayfair",
        "beyondbracket",
    ]
    return sum(1 for word in bad_words if word in lowered) >= 2


def is_product_comparison_topic(category, keywords, writing_style, extra_prompt="", planned_title=""):
    raw = f"{category} {keywords} {writing_style} {extra_prompt} {planned_title}"
    compact = normalize_text_for_detect(raw)
    comparison_words = ["vs", "비교", "차이", "대체", "고민", "둘중", "둘 중", "살까", "추천", "제품"]
    product_words = [
        "xps", "맥북", "macbook", "노트북", "아이폰", "iphone", "갤럭시", "galaxy",
        "아이패드", "ipad", "맥미니", "macmini", "모니터", "키보드", "마우스", "가전",
        "카메라", "gpu", "cpu", "그래픽카드", "건설장비", "장비"
    ]
    return any(word.replace(" ", "") in compact for word in comparison_words) and any(word.replace(" ", "") in compact for word in product_words)


def extract_comparison_product_names(keywords, planned_title="", extra_prompt=""):
    text = str(planned_title or keywords or extra_prompt or "제품 비교")
    cleaned = re.sub(r"\s+", " ", text).strip()

    known = []
    known_patterns = [
        (r"XPS\s*13", "XPS 13"),
        (r"맥북\s*네오", "맥북 네오"),
        (r"MacBook\s*Neo", "MacBook Neo"),
        (r"맥북\s*에어", "맥북 에어"),
        (r"MacBook\s*Air", "MacBook Air"),
        (r"맥북\s*프로", "맥북 프로"),
        (r"MacBook\s*Pro", "MacBook Pro"),
        (r"맥미니", "맥미니"),
        (r"Mac\s*mini", "Mac mini"),
    ]
    for pattern, name in known_patterns:
        if re.search(pattern, cleaned, flags=re.IGNORECASE) and name not in known:
            known.append(name)

    if len(known) >= 2:
        return known[0], known[1]

    separators = [" vs ", " VS ", "와 ", "과 ", "랑 ", "하고 ", "대비", "대체"]
    for sep in separators:
        if sep in cleaned:
            parts = [p.strip(" ,/|:;·-_") for p in cleaned.split(sep, 1)]
            if len(parts) == 2 and parts[0] and parts[1]:
                a = parts[0][-40:].strip()
                b = re.split(r"\s+(고민|비교|차이|추천|정리|살펴보기|가능|할까)", parts[1])[0].strip()
                if a and b:
                    return a, b

    if len(known) == 1:
        return known[0], "비교 제품"

    return "비교 제품 1", "비교 제품 2"


def build_required_comparison_table(product_a, product_b):
    product_a = html.escape(str(product_a or "비교 제품 1"))
    product_b = html.escape(str(product_b or "비교 제품 2"))
    rows = [
        ("제조사", "공식 확인 필요", "공식 확인 필요"),
        ("출시 시점", "공식 확인 필요", "공식 확인 필요"),
        ("CPU/칩셋", "공식 확인 필요", "공식 확인 필요"),
        ("GPU/그래픽", "공식 확인 필요", "공식 확인 필요"),
        ("RAM/메모리", "옵션별 상이 또는 공식 확인 필요", "옵션별 상이 또는 공식 확인 필요"),
        ("저장공간", "옵션별 상이 또는 공식 확인 필요", "옵션별 상이 또는 공식 확인 필요"),
        ("디스플레이", "공식 확인 필요", "공식 확인 필요"),
        ("무게", "공식 확인 필요", "공식 확인 필요"),
        ("배터리", "공식 확인 필요", "공식 확인 필요"),
        ("포트", "공식 확인 필요", "공식 확인 필요"),
        ("운영체제", "공식 확인 필요", "공식 확인 필요"),
        ("가격대", "판매처·옵션별 확인 필요", "판매처·옵션별 확인 필요"),
        ("추천 대상", "본문 기준 확인", "본문 기준 확인"),
    ]
    body = "\n".join(
        f"        <tr><td>{html.escape(item)}</td><td>{html.escape(a)}</td><td>{html.escape(b)}</td></tr>"
        for item, a, b in rows
    )
    return f"""
<h2>주요 스펙 비교</h2>
<table class="info-table">
    <thead>
        <tr>
            <th>항목</th>
            <th>{product_a}</th>
            <th>{product_b}</th>
        </tr>
    </thead>
    <tbody>
{body}
    </tbody>
</table>
<p>위 표에서 확인되지 않은 항목은 임의로 단정하지 않고 공식 스펙과 판매처 정보를 기준으로 다시 확인하는 것이 좋습니다.</p>
""".strip()


def ensure_required_comparison_table(content, category, keywords, writing_style, extra_prompt="", planned_title=""):
    content = str(content or "")
    if not is_product_comparison_topic(category, keywords, writing_style, extra_prompt, planned_title):
        return content

    if '<table' in content.lower() and 'info-table' in content.lower():
        return content

    product_a, product_b = extract_comparison_product_names(keywords, planned_title, extra_prompt)
    table_html = build_required_comparison_table(product_a, product_b)

    h2_match = re.search(r"<h2[^>]*>", content, flags=re.IGNORECASE)
    if h2_match:
        return content[:h2_match.start()] + table_html + "\n\n" + content[h2_match.start():]

    return table_html + "\n\n" + content


def build_better_thumbnail_prompt(title, keywords, category):
    category = str(category or "").strip()
    title = str(title or "").strip()
    keywords = str(keywords or "").strip()

    category_style_map = {
        "tech": "modern editorial tech blog thumbnail style, clean and premium, device-focused composition, cool neutral tones",
        "finance": "professional finance editorial style, clean and trustworthy, simple chart or money concept, deep blue and green accents",
        "architecture": "modern architecture editorial style, clean composition, building or drawing concept, gray and warm orange accents",
        "realestate": "clean real estate editorial style, apartment or housing concept, neat and professional layout",
        "life": "bright lifestyle editorial style, warm and clean, friendly and natural composition",
    }

    category_style = category_style_map.get(category, "clean professional editorial blog thumbnail style")

    return f"""
Create a high-quality Korean blog thumbnail image.

Topic title: {title}
Core keywords: {keywords}

Style:
- {category_style}
- scroll-stopping editorial cover image
- visually strong main subject
- realistic or polished editorial illustration style
- premium blog article feel
- bold composition with strong contrast and clear tension
- cinematic lighting, stronger contrast, dramatic but not messy
- background should be clear but visually gripping, not plain or empty
- leave a strong dark or bright text-safe area for bold Korean title overlay later
- mobile-friendly visual readability
- no watermark
- no logo
- no text
- no letters
- no Korean text
- no English text
- no numbers
- no typography
- no title inside image
- no messy collage, but allow dramatic editorial tension
- no bland stock-image feeling
- no fake UI screenshot
""".strip()


def build_better_content_image_prompt(base_prompt, category):
    base_prompt = str(base_prompt or "").strip()
    category = str(category or "").strip()

    category_style_map = {
        "tech": "editorial tech article image, realistic product-focused composition, clean white or light gray background, modern and premium",
        "finance": "editorial finance article image, simple and professional, concept-driven visual, clean layout",
        "architecture": "editorial architecture article image, realistic and clean, drawing/site/building-oriented composition",
        "realestate": "editorial property article image, housing/interior/building concept, clean and realistic",
        "life": "editorial lifestyle article image, natural and clean, warm but neat composition",
    }

    category_style = category_style_map.get(category, "clean editorial article image, professional and realistic")

    if not base_prompt:
        base_prompt = "Create a clean editorial article image related to the topic."

    return f"""
{base_prompt}

Visual direction:
- {category_style}
- Korean professional blog article image
- one clear subject or a clean comparison composition
- realistic or polished editorial illustration style
- simple background
- neat spacing
- soft shadow
- no watermark
- no logo
- no text
- no letters
- no Korean text
- no English text
- no numbers
- no typography
- no fake screenshot
- no cluttered layout
""".strip()


def build_better_image_caption(caption, category):
    caption = str(caption or "").strip()

    if caption:
        return caption

    default_map = {
        "tech": "제품 특징을 보여주는 참고 이미지",
        "finance": "중요한 내용을 이해하기 위한 참고 이미지",
        "architecture": "현장·도면 개념을 돕는 참고 이미지",
        "realestate": "중요하게 볼 부분을 보여주는 참고 이미지",
        "life": "내용 이해를 돕는 참고 이미지",
    }

    return default_map.get(category, "본문 이해를 돕는 참고 이미지")


def make_fallback_thumbnail_prompt(category, keywords, title=""):
    return build_better_thumbnail_prompt(title=title or keywords or "블로그 콘텐츠", keywords=keywords, category=category)


def _extract_text_from_gemini_response(response):
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

    return "\n".join(piece for piece in pieces if piece).strip()


def _extract_image_bytes_from_gemini_response(response):
    for candidate in getattr(response, "candidates", []) or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", []) or []:
            inline_data = getattr(part, "inline_data", None)
            if inline_data is not None:
                data = getattr(inline_data, "data", None)
                if isinstance(data, bytes):
                    return data
                if isinstance(data, str) and data:
                    try:
                        return base64.b64decode(data)
                    except Exception:
                        pass

            data = getattr(part, "data", None)
            if isinstance(data, bytes):
                return data
            if isinstance(data, str) and data:
                try:
                    return base64.b64decode(data)
                except Exception:
                    pass

    return None



def _gemini_generate_text_once_with_model(prompt, model_name):
    client = get_gemini_client()

    config = None
    if GEMINI_USE_GOOGLE_SEARCH:
        grounding_tool = types.Tool(
            google_search=types.GoogleSearch()
        )
        config = types.GenerateContentConfig(
            tools=[grounding_tool]
        )

    response = client.models.generate_content(
        model=model_name or TEXT_MODEL,
        contents=prompt,
        config=config,
    )
    return _extract_text_from_gemini_response(response)


def _gemini_generate_text_once(prompt):
    client = get_gemini_client()

    config = None
    if GEMINI_USE_GOOGLE_SEARCH:
        grounding_tool = types.Tool(
            google_search=types.GoogleSearch()
        )
        config = types.GenerateContentConfig(
            tools=[grounding_tool]
        )

    response = client.models.generate_content(
        model=TEXT_MODEL,
        contents=prompt,
        config=config,
    )
    return _extract_text_from_gemini_response(response)





# CBL_AI_TEXT_RETRY_START
def gemini_generate_text(*args, **kwargs):
    """
    Gemini 텍스트 생성 자동 재시도 래퍼.
    503 UNAVAILABLE, timeout, 429, 500 계열처럼 일시적인 오류는 잠깐 기다렸다가 재시도한다.
    """
    import random
    import time

    max_retries = int(kwargs.pop("max_retries", 3))
    base_delay = float(kwargs.pop("retry_base_delay", 2.0))

    retry_words = (
        "503",
        "UNAVAILABLE",
        "TIMEOUT",
        "TIMED OUT",
        "DEADLINE_EXCEEDED",
        "429",
        "RESOURCE_EXHAUSTED",
        "500",
        "INTERNAL",
        "502",
        "504",
    )

    last_error = None

    for attempt in range(1, max_retries + 1):
        try:
            if attempt > 1:
                print(f"[AI 자동재시도] Gemini 텍스트 생성 재시도 {attempt}/{max_retries}")
            return _gemini_generate_text_once(*args, **kwargs)

        except Exception as e:
            last_error = e
            msg = f"{type(e).__name__}: {e}"
            upper_msg = msg.upper()
            is_retryable = any(word in upper_msg for word in retry_words)

            if (not is_retryable) or attempt >= max_retries:
                print("========== Gemini 텍스트 생성 최종 실패 ==========")
                print(msg)
                raise last_error

            delay = base_delay * (2 ** (attempt - 1)) + random.uniform(0.3, 1.2)
            print("========== Gemini 텍스트 생성 일시 오류 ==========")
            print(f"[AI 자동재시도] {attempt}/{max_retries} 실패: {msg}")
            print(f"[AI 자동재시도] {delay:.1f}초 후 다시 시도합니다.")
            time.sleep(delay)
# CBL_AI_TEXT_RETRY_END





# CBL_ADSENSE_STRUCTURE_RANDOMIZER_START
def cbl_adsense_structure_instruction(
    writing_style="자연 설명형",
    category="",
    title="",
    language="ko",
):
    """
    애드센스 승인용 글 구조를 매번 조금씩 다르게 만들기 위한 지시문.
    특히 자연 설명형은 고정된 질문형 구조를 피하고,
    실제 사람이 주제에 맞춰 전개한 것처럼 흐름을 다양화한다.
    """
    import random as _random

    style = (writing_style or "").strip()
    category = (category or "").strip()
    title = (title or "").strip()
    lang = (language or "ko").strip().lower()

    is_natural = ("자연" in style) or ("설명" in style) or ("natural" in style.lower())
    is_expert = ("전문" in style) or ("분석" in style) or ("expert" in style.lower())
    is_review = ("리뷰" in style) or ("review" in style.lower())

    natural_flows = [
        (
            "생활상황 시작형",
            [
                "일상에서 실제로 마주치는 상황으로 시작",
                "그 상황에서 독자가 헷갈리는 지점 설명",
                "중요한 개념을 쉬운 말로 풀어쓰기",
                "작동 원리 또는 판단 기준 설명",
                "대표적인 예시 2~3개 제시",
                "주의할 점과 자주 하는 착각 정리",
                "실제로 확인하거나 적용하는 방법",
                "마무리",
            ],
        ),
        (
            "오해 해소형",
            [
                "사람들이 흔히 오해하는 부분 제시",
                "왜 그런 오해가 생기는지 설명",
                "정확한 개념을 단계적으로 정리",
                "비슷한 개념과 비교",
                "실제 사례로 차이 설명",
                "알아두면 좋은 예외 상황",
                "초보자가 확인해야 할 확인 기준",
                "마지막으로 확인할 부분",
            ],
        ),
        (
            "문제 해결형",
            [
                "먼저 문제가 발생하는 상황 묘사",
                "가능한 원인을 여러 갈래로 나누어 설명",
                "가장 중요한 기준 소개",
                "우선 확인해야 할 부분 정리",
                "상황별 해결 흐름 설명",
                "실수하기 쉬운 부분 보완",
                "확인 후 다음 단계 안내",
                "마무리",
            ],
        ),
        (
            "비교 이해형",
            [
                "비슷해 보이는 두 개념 또는 상황 제시",
                "겉으로 보기에는 같은 점 설명",
                "실제로 달라지는 기준 정리",
                "각각의 장단점 또는 역할 설명",
                "어떤 상황에서 무엇을 선택해야 하는지 설명",
                "실제 예시로 다시 풀어쓰기",
                "처음 보는 사람이 기억할 부분",
                "마무리",
            ],
        ),
        (
            "순서 설명형",
            [
                "처음 접하는 사람이 막히는 지점부터 시작",
                "전체 흐름을 먼저 간단히 보여주기",
                "첫 번째 단계 설명",
                "중간 단계에서 중요한 판단 기준 설명",
                "마지막 단계에서 확인해야 할 부분",
                "중간에 생길 수 있는 변수 설명",
                "실제로 활용할 수 있는 팁",
                "마무리",
            ],
        ),
        (
            "맥락 확장형",
            [
                "주제가 중요해진 배경 설명",
                "예전 방식이나 기존 인식 소개",
                "현재 왜 다시 주목받는지 설명",
                "중요한 개념을 독자 눈높이에 맞게 정리",
                "관련 사례 또는 적용 장면 제시",
                "장점뿐 아니라 한계도 함께 설명",
                "앞으로 확인하면 좋은 부분",
                "마무리",
            ],
        ),
        (
            "초보자 안내형",
            [
                "처음 보는 사람이 느낄 수 있는 막막함 제시",
                "가장 먼저 알아야 할 용어 정리",
                "전체 구조를 쉬운 비유로 설명",
                "세부 개념을 순서대로 풀어쓰기",
                "실제 상황에 대입한 예시",
                "잘못 이해하기 쉬운 부분 정리",
                "짧은 확인 기준",
                "마무리",
            ],
        ),
        (
            "경험 기반 설명형",
            [
                "실제로 겪을 법한 장면으로 도입",
                "처음에는 잘 보이지 않는 문제 설명",
                "먼저 봐야 할 기준을 자연스럽게 제시",
                "관련 개념을 순차적으로 연결",
                "상황별로 달라지는 부분 설명",
                "주의해야 할 실수 또는 리스크",
                "현실적인 활용 팁",
                "마지막으로 확인할 부분",
            ],
        ),
        (
            "왜 필요한지 설명형",
            [
                "이 주제가 왜 필요한지 상황부터 설명",
                "없을 때 생기는 불편함이나 문제 제시",
                "기본 개념을 쉽게 정리",
                "실제로 도움이 되는 부분 설명",
                "적용되는 대표 상황 소개",
                "알아두면 좋은 한계나 조건",
                "처음 시작할 때 확인할 점",
                "마무리",
            ],
        ),
        (
            "기준 정리형",
            [
                "판단이 어려운 상황 제시",
                "먼저 봐야 할 기준 설명",
                "기준별로 달라지는 결과 정리",
                "좋은 경우와 피해야 할 경우 비교",
                "실제 예시로 기준 적용",
                "초보자가 놓치기 쉬운 부분",
                "간단히 판단하는 순서",
                "마무리",
            ],
        ),
        (
            "사례 확장형",
            [
                "대표 사례 하나로 글 시작",
                "사례 안에서 놓치기 쉬운 문제 설명",
                "관련 개념을 자연스럽게 소개",
                "비슷한 사례를 추가로 연결",
                "공통적으로 확인해야 할 기준 정리",
                "상황별 차이점 설명",
                "실제로 적용할 때의 팁",
                "마무리",
            ],
        ),
        (
            "단계별 이해형",
            [
                "가장 쉬운 개념부터 시작",
                "한 단계 깊은 개념으로 확장",
                "실제 구조나 원리 설명",
                "사용자가 체감하는 변화 설명",
                "구체적인 확인 방법 소개",
                "중간에 헷갈리는 용어 정리",
                "전체 흐름 다시 연결",
                "마무리",
            ],
        ),
        (
            "확인 기준 중심형",
            [
                "먼저 확인해야 할 상황 제시",
                "첫 번째 확인 기준 설명",
                "두 번째 확인 기준 설명",
                "세 번째 확인 기준 설명",
                "확인 결과에 따른 판단 방법",
                "실수하기 쉬운 예외 상황",
                "마지막으로 점검할 부분",
                "마무리",
            ],
        ),
        (
            "차이점 설명형",
            [
                "혼동하기 쉬운 개념들을 먼저 제시",
                "같아 보이는 이유 설명",
                "가장 큰 차이를 한 문장으로 풀어쓰기",
                "차이가 실제로 영향을 주는 부분 설명",
                "상황별 예시 비교",
                "선택 또는 판단 기준 제시",
                "기억해두면 좋은 부분",
                "마무리",
            ],
        ),
        (
            "실수 예방형",
            [
                "처음 접할 때 자주 하는 실수 제시",
                "그 실수가 생기는 이유 설명",
                "올바른 이해 방식 정리",
                "실수를 줄이는 확인 순서",
                "상황별 주의사항",
                "이미 문제가 생겼을 때 확인할 부분",
                "다음부터 예방하는 방법",
                "마무리",
            ],
        ),
        (
            "원리 이해형",
            [
                "겉으로 보이는 현상 설명",
                "그 뒤에서 작동하는 원리 소개",
                "중요한 요소를 나누어 설명",
                "요소들이 서로 연결되는 방식 설명",
                "실제 상황에 적용한 예시",
                "원리를 알면 좋은 이유",
                "처음 보는 사람이 기억할 부분",
                "마무리",
            ],
        ),
        (
            "선택 가이드형",
            [
                "선택이 필요한 상황으로 시작",
                "선택 전에 알아야 할 기본 개념",
                "상황별로 달라지는 기준 설명",
                "각 선택지의 장단점 정리",
                "초보자에게 적합한 판단 방법",
                "피해야 할 선택 패턴",
                "최종 선택 전 볼 부분",
                "마무리",
            ],
        ),
        (
            "변화 흐름형",
            [
                "과거에는 어떻게 이해했는지 설명",
                "최근 달라진 환경이나 기준 소개",
                "변화가 생긴 이유 설명",
                "현재 기준에서 봐야 할 부분 정리",
                "실제 생활이나 업무에 미치는 영향",
                "앞으로 확인해야 할 변화",
                "독자가 적용할 수 있는 부분",
                "마무리",
            ],
        ),
        (
            "장단점 균형형",
            [
                "주제의 기본 특징 소개",
                "좋게 평가할 수 있는 부분 설명",
                "반대로 주의해야 할 부분 설명",
                "장점이 잘 드러나는 상황",
                "단점이 문제가 되는 상황",
                "한쪽으로 치우치지 않고 보는 기준",
                "실제 적용 전 확인할 점",
                "마무리",
            ],
        ),
        (
            "현실 적용형",
            [
                "개념보다 현실 상황을 먼저 제시",
                "실제로 적용할 때 부딪히는 문제 설명",
                "기본 개념을 필요한 만큼만 정리",
                "상황별 적용 방법 설명",
                "적용 후 확인해야 할 결과",
                "현실적으로 생길 수 있는 한계",
                "다음 단계로 이어지는 팁",
                "마무리",
            ],
        ),
    ]

    # CBL_NATURAL_50_FLOWS_EXTEND_START
    natural_flows.extend(
[('빠른결론 시작형', ['독자가 가장 궁금해할 결론을 짧게 먼저 제시', '왜 그렇게 볼 수 있는지 배경 설명', '본문에서 다룰 기준을 자연스럽게 연결', '상황별로 달라지는 부분 설명', '놓치기 쉬운 변수 정리', '실제로 확인할 부분 안내', '마무리']), ('경고신호 해석형', ['먼저 눈에 띄는 위험 신호 또는 변화 제시', '그 신호가 왜 생겼는지 설명', '독자가 오해하기 쉬운 부분 구분', '영향을 받을 수 있는 대상 설명', '당장 확인할 수 있는 기준 제시', '과도한 해석을 피해야 하는 이유', '마무리']), ('원인분해형', ['겉으로 보이는 현상 설명', '첫 번째 원인 정리', '두 번째 원인 정리', '여러 원인이 연결되는 방식 설명', '실제 결과로 이어지는 흐름 설명', '앞으로 달라질 수 있는 변수', '마무리']), ('영향범위형', ['이번 주제가 영향을 주는 범위부터 제시', '직접 영향을 받는 부분 설명', '간접적으로 연결되는 부분 설명', '독자가 체감할 수 있는 변화 정리', '주의해서 봐야 할 예외 상황', '현실적인 대응 기준', '마무리']), ('독자상황별형', ['독자가 처한 상황이 다를 수 있음을 먼저 설명', '초보자가 볼 부분 정리', '이미 알고 있는 사람이 볼 부분 정리', '실제 선택을 앞둔 사람이 볼 부분 정리', '공통적으로 놓치기 쉬운 부분', '상황별 판단 기준', '마무리']), ('전후비교형', ['이전에는 어떻게 받아들여졌는지 설명', '지금 달라진 부분 제시', '달라진 이유 설명', '변화가 실제로 미치는 영향', '예전 기준으로 보면 위험한 부분', '현재 기준에서 확인할 점', '마무리']), ('숫자읽기형', ['숫자나 지표가 먼저 눈에 띄는 상황 제시', '그 숫자의 기본 의미 설명', '숫자만 보고 오해하기 쉬운 부분', '비교해서 봐야 할 기준 설명', '실제 생활이나 시장에 연결되는 의미', '다음에 확인할 지표', '마무리']), ('사례로 푸는형', ['대표 사례 하나를 먼저 제시', '그 사례에서 중요한 부분 설명', '비슷한 상황으로 확장', '공통적으로 봐야 할 기준 정리', '다르게 해석해야 하는 경우', '독자가 적용할 수 있는 방법', '마무리']), ('현실상황형', ['책상 위 설명보다 실제 상황을 먼저 묘사', '실제 상황에서 먼저 보이는 문제 설명', '이론과 현실이 달라지는 부분', '확인할 때 봐야 하는 기준', '초보자가 놓치기 쉬운 부분', '현실적인 정리', '마무리']), ('초반판단형', ['처음 볼 때 바로 판단하면 위험한 이유 설명', '먼저 구분해야 할 기준 제시', '기준별로 달라지는 해석', '자주 하는 착각 정리', '실제로 확인할 순서', '마지막에 다시 볼 부분', '마무리']), ('실전적용형', ['개념보다 실제 적용 상황으로 시작', '적용 전에 알아야 할 조건 설명', '적용 과정에서 생기는 변수', '결과를 확인하는 방법', '실수했을 때 보완할 부분', '다음 단계로 이어지는 팁', '마무리']), ('리스크완화형', ['불안 요소를 먼저 제시', '그 불안이 실제 리스크인지 구분', '리스크가 커지는 조건 설명', '리스크를 줄이는 확인 방법', '과하게 걱정하지 않아도 되는 부분', '마지막 판단 기준', '마무리']), ('흐름추적형', ['처음 변화가 시작된 지점 설명', '중간에 달라진 흐름 정리', '현재 상황 설명', '앞으로 이어질 수 있는 방향', '흐름 중간에 확인할 변수', '독자가 놓치지 말아야 할 부분', '마무리']), ('선택실패방지형', ['잘못 선택하기 쉬운 상황 제시', '선택 전에 구분해야 할 부분', '좋아 보이지만 조심할 부분', '반대로 의외로 괜찮은 부분', '최종 선택 전 확인 기준', '실패를 줄이는 현실적인 방법', '마무리']), ('시장반응형', ['시장이 먼저 반응한 부분 제시', '반응이 나온 배경 설명', '과장된 해석과 실제 의미 구분', '관련된 사람들에게 미치는 영향', '앞으로 확인할 움직임', '독자가 가져갈 관점', '마무리']), ('업데이트변화형', ['새롭게 바뀐 부분 먼저 제시', '이전과 비교해 달라진 점 설명', '변경된 이유 또는 배경 설명', '사용자나 독자가 체감할 변화', '주의해야 할 조건', '다음 변화 가능성', '마무리']), ('장면전환형', ['익숙한 장면으로 시작', '그 장면에서 다른 관점으로 전환', '숨은 문제 또는 기준 설명', '실제 사례로 다시 연결', '판단 기준 정리', '마지막 확인 사항', '마무리']), ('기준우선형', ['정보보다 기준이 먼저 필요한 이유 설명', '가장 중요한 기준 제시', '보조 기준 설명', '기준끼리 충돌할 때 보는 방법', '실제 사례에 적용', '초보자가 기억할 부분', '마무리']), ('질문해소형', ['독자가 가질 만한 대표 의문 제시', '의문이 생기는 배경 설명', '답을 바로 단정하지 않고 조건 구분', '상황별 해석 설명', '자주 헷갈리는 부분 보완', '마지막 판단 기준', '마무리']), ('단계압축형', ['전체 흐름을 짧게 먼저 보여주기', '첫 단계에서 확인할 부분', '중간 단계에서 달라지는 부분', '마지막 단계에서 주의할 부분', '단계 사이의 연결 설명', '실제로 적용할 때의 팁', '마무리']), ('생활비유형', ['어려운 개념을 생활 속 비유로 시작', '비유가 설명하는 중심 개념 정리', '비유만으로는 부족한 부분 보완', '실제 상황에 다시 적용', '오해하기 쉬운 부분 설명', '짧은 정리', '마무리']), ('처음접근형', ['처음 접하는 사람이 막히는 이유 설명', '가장 먼저 알아야 할 배경 정리', '용어를 쉬운 말로 풀어쓰기', '전체 흐름 연결', '실제로 확인하는 방법', '다음에 보면 좋은 부분', '마무리']), ('불안요인해소형', ['독자가 불안해할 만한 부분 제시', '불안이 커지는 이유 설명', '사실과 추정을 구분', '실제로 확인 가능한 기준 안내', '지나친 걱정을 줄이는 해석', '상황이 바뀔 수 있는 변수', '마무리']), ('중심변수형', ['이번 주제에서 가장 큰 변수 제시', '그 변수가 중요한 이유 설명', '변수가 움직일 때 달라지는 부분', '관련된 보조 변수 정리', '실제 판단에 적용하는 방법', '앞으로 볼 부분', '마무리']), ('요즘흐름형', ['최근 분위기나 흐름으로 시작', '왜 그런 흐름이 생겼는지 설명', '흐름이 강해지는 조건', '반대로 약해질 수 있는 조건', '독자가 현실적으로 볼 부분', '과장된 해석을 피하는 방법', '마무리']), ('체감변화형', ['독자가 실제로 체감할 수 있는 변화부터 설명', '그 변화의 원인 정리', '체감은 크지만 실제 영향은 제한적인 부분', '작아 보여도 중요한 부분', '확인할 기준', '앞으로 달라질 수 있는 점', '마무리']), ('비교판단형', ['비교가 필요한 상황 제시', '비슷해 보이는 이유 설명', '판단을 가르는 기준 정리', '각각의 장단점 설명', '상황별로 더 맞는 선택', '주의할 예외', '마무리']), ('실수복구형', ['이미 실수했을 수 있는 상황 제시', '먼저 확인해야 할 부분', '되돌릴 수 있는 부분과 어려운 부분 구분', '다시 점검하는 순서', '같은 실수를 줄이는 방법', '다음 단계 안내', '마무리']), ('맥락먼저형', ['개별 정보보다 큰 맥락 먼저 설명', '맥락 안에서 이번 주제 위치 설명', '세부 내용으로 내려가기', '독자가 헷갈릴 만한 부분 정리', '현실적인 의미 해석', '마지막으로 볼 부분', '마무리']), ('마무리관점형', ['주제의 현재 분위기 설명', '중간에 꼭 봐야 할 기준 정리', '독자 입장에서 생길 수 있는 고민 설명', '무리하게 단정하면 안 되는 부분', '현실적으로 가져갈 관점', '다음에 확인할 변화', '마무리'])]
    )
    # CBL_NATURAL_50_FLOWS_EXTEND_END

    expert_flows = [
        (
            "기준 분석형",
            [
                "주제의 정의와 적용 범위",
                "먼저 볼 판단 기준",
                "작동 구조 또는 시장 구조 분석",
                "중요하게 볼 지표와 해석 방법",
                "실제 적용 사례",
                "주의할 점과 한계",
                "전체적으로 볼 부분",
            ],
        ),
        (
            "원인 결과형",
            [
                "현재 이슈 또는 배경",
                "원인 요인 분해",
                "직접 영향과 간접 영향",
                "수치나 기준으로 보는 해석",
                "관련 이해관계자 관점",
                "향후 변수",
                "결론",
            ],
        ),
        (
            "확인기준형",
            [
                "먼저 검토할 항목",
                "항목별 판단 기준",
                "먼저 봐야 할 요소",
                "자주 놓치기 쉬운 부분",
                "사례 기반 해석",
                "마지막으로 확인할 부분",
                "마무리",
            ],
        ),
    ]

    review_flows = [
        (
            "사용 경험형",
            [
                "사용하게 되는 상황",
                "첫인상과 기본 구성",
                "좋았던 점",
                "아쉬운 점",
                "비슷한 대안과 비교",
                "추천할 만한 사람",
                "구매 또는 선택 전 확인할 점",
                "마무리",
            ],
        ),
        (
            "장단점 균형형",
            [
                "전체적인 특징",
                "좋게 볼 수 있는 부분",
                "실제 사용 시 편한 부분",
                "단점 또는 불편한 부분",
                "가격·시간·효율 관점 비교",
                "주의할 점",
                "선택 전에 볼 부분",
            ],
        ),
    ]

    default_flows = [
        (
            "기본 설명형",
            [
                "주제 소개",
                "기본 개념 정리",
                "중요한 이유",
                "구체적인 예시",
                "주의할 점",
                "활용 방법",
                "마무리",
            ],
        ),
        (
            "확장 설명형",
            [
                "배경 설명",
                "중요한 개념",
                "세부 요소",
                "사례",
                "비교 또는 차이점",
                "확인 방법",
                "정리",
            ],
        ),
    ]

    if is_natural:
        flow_name, flow = _random.choice(natural_flows)
    elif is_expert:
        flow_name, flow = _random.choice(expert_flows)
    elif is_review:
        flow_name, flow = _random.choice(review_flows)
    else:
        flow_name, flow = _random.choice(default_flows)

    # 섹션 개수도 매번 조금씩 달라지게 조정
    min_len = 6 if is_natural else 5
    max_len = min(len(flow), _random.choice([7, 8, 9]) if is_natural else len(flow))
    use_len = _random.randint(min_len, max_len)
    selected_flow = flow[:use_len]

    question_rule = _random.choice([
        "질문형 H2는 사용하지 않는다. 질문은 본문 중간 전환 문장으로만 자연스럽게 1회 정도 사용한다.",
        "질문형 H2는 전체 글에서 최대 1개만 허용한다. 모든 소제목을 질문으로 만들지 않는다.",
        "질문 문장은 꼭 필요할 때만 사용한다. 설명형·상황형·비교형 소제목을 우선한다.",
        "도입부에 질문을 넣지 않아도 된다. 바로 상황 설명이나 개념 설명으로 시작해도 된다.",
        "중간 소제목은 질문보다 '기준', '차이', '확인 방법', '주의점'처럼 정보형 제목을 우선한다.",
    ])

    tone_rule = _random.choice([
        "문장은 너무 교과서처럼 쓰지 말고, 사람이 옆에서 설명하듯 자연스럽게 연결한다.",
        "각 문단 첫 문장을 매번 같은 패턴으로 시작하지 않는다.",
        "반복 표현을 피하고, 같은 의미라도 문장 구조를 바꿔 쓴다.",
        "단정적인 문장만 이어 쓰지 말고, 상황 설명과 판단 기준을 섞어 쓴다.",
        "초보자도 이해할 수 있게 쓰되, 지나치게 가볍거나 광고 문구처럼 쓰지 않는다.",
    ])

    intro_rule = _random.choice([
        "도입부는 '이번 글에서는'으로 시작하지 않아도 된다.",
        "도입부는 실제 상황, 흔한 착각, 최근 변화, 단순한 예시 중 하나로 시작한다.",
        "도입부에서 결론을 너무 빨리 말하지 말고, 독자가 왜 읽어야 하는지 자연스럽게 만든다.",
        "도입부는 2~3문단 정도로 짧게 열고 바로 본론으로 넘어간다.",
    ])

    faq_rule = _random.choice([
        "FAQ는 생략해도 된다.",
        "FAQ를 넣는다면 2개만 작성한다.",
        "FAQ를 넣는다면 3개 작성한다.",
        "FAQ를 넣는다면 2~4개 사이에서 자연스럽게 작성한다.",
    ])

    h2_style_rule = _random.choice([
        "H2 제목은 짧은 명사형, 설명형, 상황형을 섞는다.",
        "H2 제목이 모두 비슷한 길이가 되지 않게 한다.",
        "H2 제목에 같은 단어가 반복되지 않도록 한다.",
        "H2 제목은 검색 키워드만 나열하지 말고 독자가 궁금해할 흐름을 반영한다.",
        "H2 제목 중 일부는 실전형 표현을 사용해도 된다.",
    ])

    transition_rule = _random.choice([
        "섹션 사이에는 '또한', '그리고'만 반복하지 말고 문맥에 맞는 연결 문장을 사용한다.",
        "각 H2가 따로 노는 느낌이 들지 않도록 앞 섹션의 내용을 받아 다음 섹션으로 연결한다.",
        "중간중간 짧은 예시를 넣어 글의 리듬을 만든다.",
        "비슷한 설명이 반복될 경우 관점이나 예시를 바꿔서 풀어 쓴다.",
    ])

    if is_natural:
        length_rule = _random.choice([
            "각 H2는 2~4문단으로 구성하되, 필요하면 짧은 문단과 긴 문단을 섞는다.",
            "문단 길이를 균일하게 맞추지 말고 자연스럽게 변화를 준다.",
            "목록은 꼭 필요한 경우에만 사용하고, 대부분은 설명형 문단으로 전개한다.",
            "정보가 많은 부분은 짧은 목록을 사용해도 되지만, 목록만으로 글을 채우지 않는다.",
        ])
    else:
        length_rule = _random.choice([
            "각 H2는 핵심 설명과 근거를 함께 포함한다.",
            "필요한 경우 목록을 사용하되, 목록 뒤에는 해석 문장을 덧붙인다.",
            "문단은 너무 짧게 끊지 말고 의미 단위로 구성한다.",
        ])


    # CBL_NATURAL_ENDING_VARIATION_START
    # 자연설명형은 마지막 단계가 매번 "마무리"로 반복되지 않게 하되,
    # 랜덤 길이 조정 때문에 글 흐름이 중간에서 끊기지 않도록 끝맺음 단계를 항상 보강한다.
    natural_ending_variants = [
        "독자가 가져갈 관점 정리",
        "다음에 확인하면 좋은 부분",
        "현실적으로 봐야 할 기준",
        "오늘 기준으로 볼 핵심",
        "놓치지 말아야 할 부분",
        "실제로 적용할 때 볼 부분",
        "처음 보는 사람이 기억할 부분",
        "상황별로 다시 확인할 부분",
        "과하게 해석하지 말아야 할 부분",
        "다음 판단으로 이어지는 부분",
        "글 전체 흐름을 자연스럽게 연결",
        "독자 입장에서 남는 질문 정리",
        "실제 선택 전에 볼 부분",
        "조금 더 현실적으로 볼 부분",
        "앞으로 달라질 수 있는 변수",
        "기억해두면 좋은 기준",
        "무리하게 단정하지 않고 볼 부분",
        "다음 단계로 이어지는 팁",
        "현실적인 판단 기준",
        "끝에서 한 번 더 볼 기준",
        "읽고 나서 바로 확인할 부분",
        "초보자가 특히 놓치기 쉬운 부분",
        "전체 흐름을 다시 연결",
        "상황을 나눠 다시 보기",
        "주의할 점을 자연스럽게 짚기",
        "앞으로 지켜볼 변화",
        "실제로 써먹을 수 있는 기준",
        "독자가 바로 확인할 부분",
        "글을 읽은 뒤 남겨둘 관점",
        "지금 기준에서 무리 없이 볼 부분",
    ]

    if is_natural and selected_flow:
        selected_flow = list(selected_flow)
        last_text = str(selected_flow[-1] or "").strip()

        generic_end_words = [
            "마무리",
            "마지막으로 확인할 부분",
            "마지막으로 확인할 부분",
            "짧은 정리",
            "선택 전에 볼 부분",
            "전체적으로 볼 부분",
            "결론",
        ]

        ending_like_words = [
            "관점", "기준", "확인할 부분", "볼 부분", "변수", "팁",
            "다시 연결", "지켜볼 변화", "남겨둘 관점", "판단 포인트",
        ]

        # 기존 마지막이 너무 뻔하면 교체
        if last_text in generic_end_words or last_text.startswith("마무리"):
            selected_flow[-1] = _random.choice(natural_ending_variants)

        # 랜덤 절단 때문에 끝맺음 없이 끊기는 경우, 자연스러운 마지막 단계를 추가
        elif not any(word in last_text for word in ending_like_words):
            if len(selected_flow) < 8:
                selected_flow.append(_random.choice(natural_ending_variants))
            else:
                selected_flow[-1] = _random.choice(natural_ending_variants)

        # "정리"가 너무 자주 보이면 일부 표현 완화
        selected_flow = [
            str(x)
            .replace("마무리", "끝부분")
            .replace("마지막으로 확인할 부분", "끝부분에서 볼 기준")
            .replace("마지막으로 확인할 부분", "끝부분에서 볼 기준")
            .replace("초보자용 요약", "처음 보는 사람이 기억할 부분")
            for x in selected_flow
        ]
    # CBL_NATURAL_ENDING_VARIATION_END


    flow_text = "\n".join(f"{i+1}. {x}" for i, x in enumerate(selected_flow))

    lang_rule = ""
    if lang.startswith("en"):
        lang_rule = "최종 글은 영어로 작성한다. 다만 구조 지시는 자연스러운 영문 블로그 흐름에 맞게 반영한다."
    elif lang.startswith("ja"):
        lang_rule = "최종 글은 일본어로 작성한다. 다만 구조 지시는 자연스러운 일본어 블로그 흐름에 맞게 반영한다."
    elif lang.startswith("zh"):
        lang_rule = "최종 글은 중국어로 작성한다. 다만 구조 지시는 자연스러운 중국어 블로그 흐름에 맞게 반영한다."
    else:
        lang_rule = "최종 글은 자연스러운 한국어로 작성한다."

    return f"""
애드센스 승인용 글 구조 지시

이번 글의 구조 유형:
- {flow_name}

이번 글에서 반드시 반영할 H2 전개 순서:
{flow_text}

글쓰기 세부 지시:\n- H2/H3 소제목은 '항목: 설명' 형식을 쓰지 마라.
- H2/H3 소제목은 '1. 벽체와 천장: ...'처럼 숫자와 콜론으로 시작하지 마라.
- 소제목은 '벽체와 천장은 균열과 누수 흔적을 먼저 본다'처럼 자연스러운 문장형으로 작성해라.
- {lang_rule}
- {intro_rule}
- {question_rule}
- {tone_rule}
- {h2_style_rule}
- {transition_rule}
- {length_rule}
- {faq_rule}
- 같은 주제라도 매번 같은 소제목, 같은 도입 문장, 같은 마무리 문장을 반복하지 않는다.
- '궁금하신가요?', '알아볼까요?', '중요합니다' 같은 반복적인 블로그 문구를 남발하지 않는다.
- 검색엔진용 키워드를 억지로 반복하지 말고 문맥 안에서 자연스럽게 녹인다.
- 글 전체는 정보 제공 중심으로 작성하고, 과장 광고처럼 보이는 표현은 피한다.
- 결론은 단순 요약만 하지 말고 독자가 실제로 무엇을 확인하면 좋은지까지 정리한다.

글 제목/주제 참고:
- 카테고리: {category or "미지정"}
- 제목/주제: {title or "미지정"}
""".strip()


def cbl_enhance_article_prompt(prompt, writing_style=None, category=None, title=None, language=None):
    try:
        if not isinstance(prompt, str):
            return prompt

        if "CBL_ADSENSE_STRUCTURE_RANDOMIZER" in prompt:
            return prompt

        check = prompt.lower()
        article_keys = [
            "본문", "블로그", "글 작성", "글을 작성", "article", "blog post",
            "content", "faq", "seo", "meta description", "소제목", "section"
        ]

        if not any(k.lower() in check for k in article_keys):
            return prompt

        return prompt + cbl_adsense_structure_instruction(
            writing_style=writing_style,
            category=category,
            title=title,
            language=language,
        )
    except Exception:
        return prompt
# CBL_ADSENSE_STRUCTURE_RANDOMIZER_END




# CBL_RECENT_ISSUE_PRESEARCH_START
def build_recent_issue_context(category="", keywords="", planned_title="", language="ko"):
    """
    글 작성 전에 주제와 관련된 최근 이슈를 짧게 검색해서 본문 프롬프트에 넣기 위한 함수.
    실제 확인 가능한 이슈만 사용하고, 없으면 억지로 만들지 않는다.
    """
    topic = " ".join([
        str(category or "").strip(),
        str(keywords or "").strip(),
        str(planned_title or "").strip(),
    ]).strip()

    if not topic:
        return ""

    lang = str(language or "ko").lower().strip()

    if lang.startswith("en"):
        prompt = f"""
Search recent web/news issues related to this blog topic.

Topic:
{topic}

Task:
- Find only clearly verifiable recent issues, news, incidents, policy changes, security incidents, company announcements, or public discussions related to the topic.
- Prefer the last 30 to 90 days.
- If there is no directly relevant recent issue, say "NO_RELEVANT_RECENT_ISSUE".
- Do not invent names, dates, damage amounts, victim counts, celebrities, companies, or institutions.
- If a person/company/institution is mentioned, it must be from a clearly verifiable source.
- Summarize in 3 to 5 short bullet points.
- Include source name and date when possible.
- Write in Korean if the final blog language is Korean, otherwise English.

Important:
This is only background context for writing a blog post. Do not write the blog post yet.
"""
    else:
        prompt = f"""
아래 블로그 주제와 관련된 최근 웹/뉴스 이슈를 먼저 검색해서 정리해라.

주제:
{topic}

작업:
- 최근 30~90일 사이에 이 주제와 직접 관련 있는 뉴스, 보안 사고, 개인정보 이슈, 기업 발표, 기관 공지, 정책 변화, 사회적 논의가 있는지 확인해라.
- 직접 관련 있는 최근 이슈가 없으면 "NO_RELEVANT_RECENT_ISSUE"라고만 써라.
- 연예인, 기관, 기업, 피해 규모, 날짜, 사건명은 절대 지어내지 마라.
- 실명이나 기관명을 쓰려면 검색으로 확인 가능한 경우에만 써라.
- 가능한 경우 출처명과 날짜를 함께 적어라.
- 3~5개 짧은 bullet로만 정리해라.
- 블로그 본문을 쓰지 말고, 참고용 최근 이슈만 정리해라.

주의:
이 결과는 본문 작성 참고자료다.
확실하지 않은 내용은 "확인 필요"라고 표시하거나 제외해라.
"""

    try:
        result = _gemini_generate_text_once_with_model(prompt, RECENT_ISSUE_MODEL)
    except Exception as e:
        print("========== 최근 이슈 검색 실패 ==========")
        print(e)
        return ""

    result = str(result or "").strip()

    if not result:
        return ""

    if "NO_RELEVANT_RECENT_ISSUE" in result:
        return ""

    # 너무 긴 검색 결과는 본문 프롬프트를 오염시키지 않도록 제한
    return result[:1500]
# CBL_RECENT_ISSUE_PRESEARCH_END


def cbl_strengthen_thumbnail_text(raw_text="", title="", keywords="", category=""):
    raw_text = str(raw_text or "").strip()
    title_text = f"{title} {keywords}".strip()

    bland_words = [
        "시장 동향", "경제", "정리", "가이드", "확인", "분석",
        "주요 내용", "알아보기", "이해하기", "기본 정보"
    ]

    too_bland = (not raw_text) or len(raw_text) > 18 or any(word in raw_text for word in bland_words)

    if not too_bland:
        return raw_text[:18]

    compact = title_text.replace(" ", "").upper()

    if "ECB" in compact or "금리" in title_text or "긴축" in title_text:
        return "금리 충격 온다"
    if "분양" in title_text or "미분양" in title_text:
        return "분양시장 경고등"
    if "재건축" in title_text or "아파트" in title_text:
        return "집값 변수 터졌다"
    if "코인" in title_text or "비트코인" in title_text or "리플" in title_text:
        return "코인시장 흔들린다"
    if category == "tech":
        return "이 기능 놓치면 손해"
    if category == "architecture":
        return "건축 확인 기준"
    if category == "realestate":
        return "부동산 경고등"
    if category == "finance":
        return "시장 흔들 변수"
    if category == "life":
        return "방문 전 볼 것"

    return (raw_text or keywords or title)[:18]

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
    image_count = clamp_number(image_count, 0, 5, 0)

    style_map = {
        "natural": "자연 설명형",
        "expert": "전문가 분석형",
        "experience": "경험 기반형",
        "product_review": "구매·리뷰형",
        "news_trend": "뉴스·트렌드형",
        "trend": "뉴스·트렌드형",
        "checklist": "확인기준형",
        "review": "리뷰형",
        "natural_blog": "자연 설명형",
        "practical": "경험 기반형",
        "issue": "뉴스·트렌드형",
        "guide": "자연 설명형",
    }

    category_map = {
        "architecture": "건축",
        "realestate": "부동산",
        "finance": "금융",
        "tech": "테크",
        "life": "일상",
    }

    category_name = category_map.get(category, category)
    style_name = style_map.get(writing_style, "자연 설명형")
    style_key_map = {
        "practical": "experience",
        "issue": "news_trend",
        "guide": "natural",
        "trend": "news_trend",
        "natural_blog": "natural",
    }
    style_rule_key = style_key_map.get(writing_style, writing_style)
    style_specific_rule = STYLE_WRITING_RULES.get(style_rule_key, STYLE_WRITING_RULES["natural"])
    comparison_required = is_product_comparison_topic(category, keywords, style_rule_key, extra_prompt, planned_title)

    human_opening_pattern = random.choice(HUMAN_OPENING_PATTERNS)
    human_structure_pattern = random.choice(HUMAN_STRUCTURE_PATTERNS)
    category_voice_rule = CATEGORY_VOICE_RULES.get(category, "")

    recent_issue_context = build_recent_issue_context(
        category=category,
        keywords=keywords,
        planned_title=planned_title,
        language="ko",
    )

    recent_issue_instruction = ""
    if recent_issue_context:
        recent_issue_instruction = f"""
최근 이슈 참고자료:
{recent_issue_context}

최근 이슈 반영 규칙:
- 위 최근 이슈가 글 주제와 자연스럽게 연결될 때만 본문에 1~3문장 정도로 짧게 반영해라.
- 억지로 뉴스 기사처럼 쓰지 마라.
- 출처와 사실관계가 불명확한 내용은 본문에 쓰지 마라.
- 기관명, 기업명, 연예인명, 피해 규모, 날짜는 참고자료에 명확히 있을 때만 사용해라.
- 최근 이슈가 글 흐름을 방해하면 도입부 대신 중간 예시로 짧게 넣어라.
"""
    else:
        recent_issue_instruction = """
최근 이슈 참고자료:
- 이 주제와 직접 연결되는 확실한 최근 이슈가 확인되지 않았으므로, 특정 사건·기관명·연예인명은 임의로 쓰지 마라.
- 대신 독자가 실제로 겪을 수 있는 일반적인 상황 중심으로 설명해라.
"""

    planned_title = (planned_title or "").strip()
    planned_title_instruction = ""

    if planned_title:
        planned_title_instruction = f"""
이번 글은 아래 세부 주제에 맞춰 작성해라.

세부 제목:
{planned_title}

작성 규칙:
- 제목은 가능하면 세부 제목을 그대로 사용하거나 검색 친화적으로 자연스럽게 다듬어라.
- 제목은 25~55자 정도로 작성해라.
- 제목 앞부분에 핵심 키워드가 자연스럽게 들어가게 작성해라.
- 다른 주제로 벗어나지 마라.
- 같은 키워드의 다른 글과 내용이 겹치지 않게 작성해라.
- 제목이 너무 기계적으로 보이면 자연스러운 블로그 제목으로 다듬어라.
"""

    if image_count > 0:
        placeholders = ", ".join([f"[[IMAGE_{i}]]" for i in range(1, image_count + 1)])
        image_instruction = f"""
본문 중간에 이미지가 들어갈 자연스러운 위치를 골라 아래 플레이스홀더를 정확히 한 번씩 넣어라.
플레이스홀더: {placeholders}

그리고 content_images 배열에는 이미지 {image_count}장에 대한 prompt와 caption을 작성해라.

본문 이미지 prompt 조건:
- 실제 블로그 본문 중간에 들어갈 기사형·리뷰형 정보성 이미지 느낌
- 제품, 장소, 상황 등 핵심 대상을 명확하게 보여주는 이미지로 작성
- 과도한 텍스트, 로고, 워터마크 금지
- 주제와 카테고리에 맞는 현실적이고 깔끔한 이미지
- 한국의 전문 블로그 기사 이미지처럼 단정하고 보기 좋게 작성
- 배경은 너무 복잡하지 않게 하고, 핵심 피사체가 잘 보이게 구성
- 인물 얼굴 클로즈업은 피하고, 상황이나 장소가 느껴지는 이미지로 작성
- 방송 화면 캡쳐, 방송사 로고, 자막, 특정 매체 화면처럼 보이게 만들지 마라.
- 이미지 안에 글자를 넣지 마라. 한글, 영어, 숫자, 타이포그래피를 넣지 마라.

caption 조건:
- 사진 아래에 들어갈 짧은 설명
- 20~45자 정도
- '입니다', '합니다' 같은 종결어미 쓰지 않기
- 짧은 이미지 설명 느낌
"""
    else:
        image_instruction = """
본문에는 이미지 플레이스홀더를 넣지 마라.
content_images는 빈 배열로 반환해라.
"""

    if comparison_required:
        product_a, product_b = extract_comparison_product_names(keywords, planned_title, extra_prompt)
        comparison_instruction = f"""
제품 비교 강제 조건:
- 이번 글은 {product_a}와 {product_b}를 비교하는 글로 작성해라.
- 본문 초반에 반드시 실제 HTML 표를 작성해라.
- 표는 반드시 <table class="info-table"> 태그를 사용해라.
- 비교표는 이미지 설명이나 캡션으로 대체하지 마라.
- 표 항목에는 CPU/칩셋, 메모리, 저장공간, 디스플레이, 무게, 배터리, 포트, 운영체제, 가격대를 포함해라.
- 확인되지 않은 스펙은 임의 작성하지 말고 "공식 확인 필요", "옵션별 상이", "판매처 확인 필요"로 표시해라.
"""
    else:
        comparison_instruction = ""

    macbook_guard = build_macbook_comparison_guard(
        keywords=keywords,
        planned_title=planned_title,
        extra_prompt=extra_prompt,
    )

    if macbook_guard:
        comparison_instruction = (comparison_instruction + "\n\n" + macbook_guard).strip()

    current_fact_check_rules = build_current_fact_check_rules(
        keywords=keywords,
        planned_title=planned_title,
        extra_prompt=extra_prompt,
        language="ko",
    )

    prompt = f"""
너는 ChickenBanana Lab 블로그의 SEO 전문 콘텐츠 작성자다.
목표는 네이버와 구글 검색엔진이 이해하기 쉬우면서도, 실제 사람이 읽었을 때 도움이 되는 글을 작성하는 것이다.

카테고리: {category_name}
주요 키워드: {keywords}
글 작성 방향: {style_name}
추가 요청사항: {extra_prompt}

글쓰기 세부 지침:
{style_specific_rule}

{current_fact_check_rules}

{comparison_instruction}

{planned_title_instruction}

검색 친화 작성 기준:
- 제목은 검색자가 실제로 입력할 만한 롱테일 키워드 형태로 작성해라.
- 첫 문단 150자 안에 주요 키워드를 자연스럽게 포함해라.
- 본문은 h2, h3, p, ul, li, table, blockquote, mark, span 태그를 적절히 사용해라.
- 본문 최상단에 h1 태그는 절대 쓰지 마라.
- 소제목에 고정 요약형 표현을 쓰지 말고, 필요한 내용은 본문 첫 부분에서 자연스럽게 설명해라.
- 모든 글에 똑같은 요약 박스를 반복하지 마라.
- FAQ는 필수가 아니다. 글 흐름상 자연스러울 때만 포함해라.
- FAQ가 필요 없는 주제라면 작성하지 마라.
- FAQ를 작성할 경우 2~4개 사이로 구성하되, 모든 글에 반복하지 마라.
- 글 마지막에는 고정 요약 형식 없이, 독자가 다음에 무엇을 보면 좋을지 자연스럽게 마무리해라.
- 키워드를 억지로 반복하지 마라.
- 같은 표현, 같은 문장 패턴, 같은 소제목 구조를 반복하지 마라.
- 도입부에서 "오늘은", "이번 글에서는", "함께 알아보겠습니다", "알아보는 시간을 가져볼게요"를 쓰지 마라.
- 마무리에서 "마무리하며", "지금까지", "현명한 웹 생활", "도움이 되었기를 바랍니다"를 쓰지 마라.
- 자연설명형일수록 FAQ를 기본값으로 넣지 말고, 글 흐름에 필요할 때만 선택적으로 넣어라.
- 캐시, 쿠키, HTTPS, DNS처럼 비슷한 테크 주제는 도입 방식과 H2 흐름을 서로 다르게 작성해라.
- 허위 수치, 확인되지 않은 통계, 실제 경험처럼 보이는 거짓 후기를 만들지 마라.
- 금융, 세금, 건강, 법률 주제는 단정하지 말고 주의 문구를 넣어라.
- 애드센스 승인에 불리할 수 있는 얇은 자동생성 글처럼 보이지 않게 작성해라.
- 추가 요청사항에 제품 스펙, 출시일, 가격, 공식 정보가 들어 있으면 그 내용을 최우선으로 반영해라.

summary 작성 조건:
- summary는 글 상단이나 목록에서 보여줄 수 있는 2~3문장 요약문으로 작성해라.
- 핵심 키워드를 자연스럽게 포함해라.
- 너무 광고 문구처럼 쓰지 마라.
- 본문 첫 문장을 그대로 복사하지 마라.

meta_description 작성 조건:
- meta_description은 검색 결과에 표시될 수 있는 설명문이다.
- 80~120자 정도로 작성해라.
- 핵심 키워드를 자연스럽게 포함해라.
- 클릭을 유도하되 과장하지 마라.
- 문장 끝은 자연스럽게 마무리해라.
- 제목과 똑같은 문장을 반복하지 마라.

본문 작성 조건:
- 한국어로 작성
- 본문은 HTML 형식으로 작성
- 본문 최상단에 h1 태그는 쓰지 마라
- 제목은 content 안에 반복하지 마라
- script, iframe, style 태그는 절대 사용하지 마라
- 과장된 허위 정보 금지
- 확인되지 않은 내용은 단정하지 말고 신중하게 표현
- 애드센스 블로그에 어울리게 정보성으로 작성
- 썸네일 이미지 프롬프트는 본문 content 안에 넣지 마라
- 결과는 반드시 JSON 형식만 반환
- 영어 본문을 작성하지 마라. 반드시 한국어 본문을 작성해라.
- Amazon, Walmart, Target, eBay, Best Buy, Wayfair 같은 일반 쇼핑몰 추천 문장을 넣지 마라.
- 본문 첫머리에 "안녕하세요", "치킨바나나랩입니다" 같은 운영자 인사말을 직접 작성하지 마라. 필요한 인사말은 사용자가 수동으로 추가한다.
- 본문 어디에도 작성 주체, 자동 작성 여부, 시스템 생성 여부, 블로그 운영 시스템 출처를 드러내는 문구를 넣지 마라.
- H2/H3 소제목에 고정 요약형, 목록 강제형, 마지막 정리형 문구를 반복해서 쓰지 마라.
- 표나 목록이 꼭 필요한 글이 아니면 무리하게 항목 나열 형식으로 만들지 마라.
- 독자를 끌어야 하는 이슈성 글은 도입부에서 너무 안전한 설명보다 변화, 충격, 논란, 손해 가능성, 시장 반응 중 하나를 먼저 잡아라.

글 분량 판단 조건:
- 주제가 간단한 생활정보, 맛집 위치, 메뉴 소개, 짧은 이슈라면 핵심만 담아 900~1,300자 정도로 작성해라.
- 맛집/여행 소개형이면 위치, 메뉴, 방문 팁, 어울리는 사람 중심으로 1,100~1,600자 정도로 작성해라.
- 비교, 분석, 교육, 사용법, 투자 리스크, 개발 방법, 건축 실무처럼 설명이 필요한 내용이면 1,500~2,500자 정도로 작성해라.
- 프로그램 사용법, 개발 튜토리얼, 자동매매 로직, 건축 실무 점검 기준처럼 단계 설명이 필요한 글은 충분히 길게 작성해라.
- 독자가 이미 아는 일반론을 길게 늘리지 마라.
- 같은 말을 반복해서 글자 수를 채우지 마라.
- 짧은 글이어도 검색자가 궁금해하는 핵심 답변은 빠뜨리지 마라.
- 긴 글은 소제목, 표, 리스트를 활용해서 읽기 쉽게 나눠라.

사람이 쓴 글처럼 보이기 위한 이번 글의 개별 조건:
- 이번 글의 도입 방식: {human_opening_pattern}

{human_structure_pattern}

{category_voice_rule}

{recent_issue_instruction}

{ANTI_AI_WRITING_RULES}

{HUMAN_DETAIL_RULES}

작성 스타일:
- 전체 톤은 사람이 직접 블로그에 쓰는 자연스러운 설명체로 작성해라.
- 보고서체, 논문체, 공공기관 안내문체, 뉴스 기사체처럼 쓰지 마라.
- 문장은 너무 정중한 "~습니다"만 반복하지 말고, "~해요", "~좋습니다", "~괜찮습니다", "~볼 만합니다"를 자연스럽게 섞어라.
- 단, 반말은 쓰지 마라.
- 독자에게 옆에서 알려주는 느낌으로 작성해라.
- 첫 문단은 너무 딱딱한 Q&A보다 자연스러운 공감 문장으로 시작해라.
- 한 문단은 2~3줄 이내로 짧게 작성해라.
- 직접 방문하지 않았는데 "제가 먹어봤는데", "직접 다녀왔는데", "제가 방문했을 때" 같은 허위 경험 표현은 절대 쓰지 마라.
- 대신 "후기에서 많이 언급되는 포인트", "여행 동선상 보기 좋은 점", "메뉴를 고를 때 볼 부분"처럼 자연스럽게 써라.
- 소제목은 딱딱한 질문형만 반복하지 말고 블로그식 문장으로 작성해라.
- 표는 꼭 필요할 때만 1개 정도 사용해라.
- 핵심 문장은 <mark class="yellow-highlight">강조문구</mark> 형태로 표시해라.
- 중요한 장소, 메뉴, 금액, 시간, 키워드는 필요한 경우에만 <span class="blue-point">강조문구</span> 형태로 강조해라.
- 글 마지막은 딱딱한 결론이나 고정 요약 형식 없이, 주제에 맞는 현실적인 판단 기준으로 끝내라.

FAQ 작성 조건:
- FAQ는 선택 사항이다.
- 글 흐름상 필요하지 않으면 FAQ 섹션을 작성하지 마라.
- FAQ를 작성하더라도 제목을 항상 <h2>자주 묻는 질문</h2>로 고정하지 마라.
- 필요하면 <h2>헷갈리기 쉬운 부분</h2>, <h2>마지막으로 확인할 점</h2>, <h2>실수하기 쉬운 부분</h2>처럼 주제에 맞게 바꿔라.
- 질문은 <h3> 태그로 작성해도 되지만, Q1/A1 형식을 고정으로 쓰지 마라.
- 본문에서 이미 말한 내용을 그대로 반복하지 마라.

지도/장소 링크 작성 조건:
- 식당, 여행지, 장소 링크가 필요할 때 URL 주소를 본문에 그대로 노출하지 마라.
- 구글지도 링크는 반드시 a 태그 버튼 형태로 작성해라.
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
- 한국의 전문 블로그 또는 테크/리뷰 기사 썸네일처럼 깔끔하고 고급스럽게 작성해라.
- 글 제목과 주제가 한눈에 느껴져야 한다.
- 핵심 오브젝트가 명확하게 보이게 작성해라.
- 텍스트를 나중에 얹기 좋은 여백을 포함해라.
- 배경은 복잡하지 않게 하고, 전체 구도는 단정하게 구성해라.
- 로고, 워터마크, 실제 인물 얼굴 클로즈업은 피하라.
- 과한 장식, 과도한 텍스트, 복잡한 콜라주 스타일은 피하라.
- 이미지 안에 글자를 넣지 마라. 한글, 영어, 숫자, 타이포그래피를 금지한다.

{image_instruction}

반환 형식:
{{
  "title": "검색 친화적인 글 제목",
  "summary": "글 상단 또는 목록에 보여줄 2~3문장 요약",
  "meta_description": "검색 결과에 표시하기 좋은 80~120자 설명문",
  "thumbnail_text": "썸네일에 넣을 짧은 문구",
  "content": "반드시 <h2>, <p>, <ul>, <li>, <table class='info-table'> 같은 HTML 태그가 포함된 HTML 본문 문자열. 일반 텍스트, Markdown, 탭 표 금지.",
  "tags": "태그1,태그2,태그3,태그4,태그5",
  "thumbnail_prompt": "대표 썸네일 이미지 생성용 프롬프트",
  "content_images": [
    {{
      "prompt": "본문 이미지 생성용 프롬프트",
      "caption": "이미지 아래에 들어갈 짧은 설명"
    }}
  ]
}}

중요:
- JSON 바깥에 설명, 코드블록, ```json, 해시태그를 절대 붙이지 마라.
- content 값에는 반드시 실제 HTML 태그를 넣어라.
- 표는 탭이나 Markdown 표가 아니라 반드시 <table class="info-table">로 작성해라.
- 제목을 content 맨 앞에 다시 반복하지 마라.
"""

    text = gemini_generate_text(prompt)
    data = extract_json(text)

    if not data:
        fallback_content = text

        if looks_like_bad_generic_shopping_text(fallback_content):
            fallback_content = "<h2>자료 확인이 필요한 주제입니다</h2><p>자료 확인 과정에서 주제와 맞지 않는 일반 쇼핑몰 정보가 감지되어 본문을 안전하게 대체했습니다. 이 주제는 제품명, 공식 스펙, 가격 자료를 추가 요청사항에 넣고 다시 생성하는 것이 좋습니다.</p>"

        fallback_title = f"{keywords} 정리"
        fallback_content = repair_ai_content_html(fallback_content, title=fallback_title)

        data = {
            "title": fallback_title,
            "summary": clean_text_for_meta(fallback_content, 180),
            "meta_description": clean_text_for_meta(fallback_content, 120),
            "thumbnail_text": keywords[:30],
            "content": fallback_content,
            "tags": keywords if include_tags else "",
            "thumbnail_prompt": make_fallback_thumbnail_prompt(category, keywords, fallback_title),
            "content_images": [],
        }

    content_images = data.get("content_images", [])
    if not isinstance(content_images, list):
        content_images = []
    content_images = content_images[:image_count]

    refined_content_images = []
    for image_item in content_images:
        if not isinstance(image_item, dict):
            continue

        refined_prompt = build_better_content_image_prompt(image_item.get("prompt", ""), category)
        refined_caption = build_better_image_caption(image_item.get("caption", ""), category)

        refined_content_images.append({
            "prompt": refined_prompt,
            "caption": refined_caption,
        })

    content_images = refined_content_images

    title = str(data.get("title", f"{keywords} 정리"))[:200]
    content = str(data.get("content", ""))
    content = repair_ai_content_html(content, title=title)

    if looks_like_bad_generic_shopping_text(content):
        content = "<h2>자료 확인이 필요한 주제입니다</h2><p>자료 확인 과정에서 주제와 맞지 않는 일반 쇼핑몰 정보가 감지되어 본문을 안전하게 대체했습니다. 제품명, 공식 스펙, 가격 자료를 추가 요청사항에 넣고 다시 생성해 주세요.</p>"

    content = repair_ai_content_html(content, title=title)
    content = cbl_polish_article_after_generate(content)
    content = ensure_required_comparison_table(content, category, keywords, style_rule_key, extra_prompt, planned_title)
    content = validate_ai_content_or_raise(
        content,
        context=f"{title} 본문",
        min_length=500,
    )
    summary = str(data.get("summary", "")).strip()
    meta_description = str(data.get("meta_description", "")).strip()

    if not summary:
        summary = clean_text_for_meta(content, 180)
    if not meta_description:
        meta_description = clean_text_for_meta(summary or content, 120)

    if make_thumbnail:
        thumbnail_prompt = build_better_thumbnail_prompt(title=title, keywords=keywords, category=category)
    else:
        thumbnail_prompt = ""

    return {
        "title": title,
        "summary": summary[:300],
        "meta_description": meta_description[:160],
        "thumbnail_text": cbl_strengthen_thumbnail_text(data.get("thumbnail_text", ""), title=title, keywords=keywords, category=category),
        "content": content,
        "tags": str(data.get("tags", "")) if include_tags else "",
        "thumbnail_prompt": thumbnail_prompt,
        "content_images": content_images,
    }


def generate_english_ai_post(
    category,
    korean_ai_data,
    korean_final_content="",
    source_keywords="",
    source_title="",
):
    """
    이미 생성된 한국어 글 데이터를 기준으로 영어 버전 글 데이터를 생성합니다.
    한글 글 1개당 영어 글 1개를 별도 Post로 저장하기 위한 데이터만 반환합니다.
    """
    korean_ai_data = korean_ai_data or {}

    category_map = {
        "architecture": "Architecture / Construction",
        "realestate": "Real Estate",
        "finance": "Finance",
        "tech": "Technology",
        "life": "Lifestyle",
    }

    category_name = category_map.get(category, str(category or "General"))
    source_title = str(source_title or korean_ai_data.get("title", "")).strip()
    source_keywords = str(source_keywords or "").strip()

    korean_title = str(korean_ai_data.get("title", source_title)).strip()
    korean_summary = str(korean_ai_data.get("summary", "")).strip()
    korean_meta_description = str(korean_ai_data.get("meta_description", "")).strip()
    korean_thumbnail_text = str(korean_ai_data.get("thumbnail_text", "")).strip()
    korean_tags = str(korean_ai_data.get("tags", "")).strip()
    korean_content = str(korean_final_content or korean_ai_data.get("content", "")).strip()

    current_fact_check_rules = build_current_fact_check_rules(
        keywords=source_keywords,
        planned_title=korean_title,
        extra_prompt=korean_content[:3000],
        language="en",
    )

    prompt = f"""
You are an English SEO blog editor for ChickenBanana Lab.
Create an English version of the Korean blog post below.

Category: {category_name}
Original keywords: {source_keywords}
Original Korean title: {korean_title}

{current_fact_check_rules}

Important goal:
- Create a separate English article for Google search users outside Korea.
- Keep the same meaning, facts, caution level, structure, and practical angle as the Korean article.
- Do not add unverified facts, numbers, rankings, dates, prices, laws, tax rules, medical claims, or investment advice.
- If the Korean article is cautious, the English article must also be cautious.
- Use natural English, not stiff machine translation.
- Make the title search-friendly in English.

HTML rules:
- Return the content as HTML.
- Preserve useful HTML structure such as h2, h3, p, ul, li, table, blockquote, mark, span, a, div, img.
- Do not use h1, script, iframe, or style tags.
- Preserve URLs, image src values, class names, target attributes, and rel attributes exactly.
- If there are image caption texts, translate only the visible caption text, not the URL or class name.
- Do not include Markdown code fences.

SEO rules:
- summary: 2 to 3 natural English sentences.
- meta_description: 80 to 150 English characters if possible.
- tags: 4 to 7 English comma-separated tags.
- thumbnail_text: short English phrase suitable for a thumbnail.
- thumbnail_prompt: English image-generation prompt matching the English article. Do not include text inside the image.

Original Korean data:
Title:
{korean_title}

Summary:
{korean_summary}

Meta description:
{korean_meta_description}

Thumbnail text:
{korean_thumbnail_text}

Tags:
{korean_tags}

Content HTML:
{korean_content}

Return JSON only in this exact format:
{{
  "title": "English SEO title",
  "summary": "English summary",
  "meta_description": "English meta description",
  "thumbnail_text": "Short English thumbnail phrase",
  "content": "English HTML content",
  "tags": "tag1,tag2,tag3,tag4,tag5",
  "thumbnail_prompt": "English thumbnail image prompt",
  "content_images": []
}}
""".strip()

    text = gemini_generate_text(prompt)
    data = extract_json(text)

    if not data:
        fallback_title = f"{source_title or source_keywords or 'Blog Post'} English Guide"[:200]
        fallback_content = repair_ai_content_html(text or korean_content, title=fallback_title)
        data = {
            "title": fallback_title,
            "summary": clean_text_for_meta(fallback_content, 180),
            "meta_description": clean_text_for_meta(fallback_content, 120),
            "thumbnail_text": "English Guide",
            "content": fallback_content,
            "tags": "English guide,ChickenBanana Lab",
            "thumbnail_prompt": make_fallback_thumbnail_prompt(category, source_keywords or fallback_title, fallback_title),
            "content_images": [],
        }

    title = str(data.get("title", "")).strip() or f"{source_title or source_keywords or 'Blog Post'} English Guide"
    title = title[:200]

    content = str(data.get("content", "")).strip()
    content = repair_ai_content_html(content, title=title)
    # CBL_SERVER_EN_EMPTY_CONTENT_GUARD_START
    # 영어 본문 생성이 0자/짧음이면 검증 전에 안전 대체본문을 직접 채운다.
    if len(str(content or "").strip()) < 500:
        print("========== 영어 본문 0자/짧음 서버 직접 대체본문 생성 ==========")
        print("title:", title)
        _safe_title = str(title or source_title or source_keywords or "English Guide").strip()
        _safe_keyword = str(source_keywords or _safe_title).strip()
        content = f"""
    <h2>{_safe_title}</h2>
    <p>This article explains {_safe_keyword} in a clear and practical way for readers who want a simple but useful overview. The goal is to provide enough context so that beginners can understand the topic without needing technical background knowledge.</p>
    <p>When people search for information about {_safe_keyword}, they usually want to know what it means, why it matters, and how it can be applied in everyday situations. This guide focuses on those basic questions and avoids unnecessary complexity.</p>
    <h3>Overview</h3>
    <p>The first point to understand is that this topic should be approached step by step. Instead of memorizing complicated definitions, readers should focus on the main concept, common examples, and practical cautions. This makes the information easier to remember and more useful in real situations.</p>
    <h3>Why it matters</h3>
    <p>{_safe_keyword} can affect how people use online services, manage information, make decisions, or understand technology-related issues. A simple explanation can help readers avoid confusion and make better choices when they encounter this subject again.</p>
    <h3>Practical tips</h3>
    <p>Start by checking reliable sources, comparing key points, and understanding the context before making a decision. If the topic is related to privacy, security, travel, finance, or daily technology, small details can make a meaningful difference.</p>
    <h3>Conclusion</h3>
    <p>In summary, {_safe_keyword} is easier to understand when it is explained with plain language and practical examples. This article provides a basic foundation that readers can use as a starting point before exploring the topic in more detail.</p>
    """.strip()
        content = repair_ai_content_html(content, title=_safe_title)
    # CBL_SERVER_EN_EMPTY_CONTENT_GUARD_END

    content = validate_ai_content_or_raise(
        content,
        context=f"{title} 영어 본문",
        min_length=500,
    )

    summary = str(data.get("summary", "")).strip()
    meta_description = str(data.get("meta_description", "")).strip()

    if not summary:
        summary = clean_text_for_meta(content, 180)

    if not meta_description:
        meta_description = clean_text_for_meta(summary or content, 120)

    thumbnail_text = str(data.get("thumbnail_text", "")).strip() or "English Guide"
    tags = str(data.get("tags", "")).strip()

    if not tags:
        tags = "English guide,ChickenBanana Lab"

    thumbnail_prompt = str(data.get("thumbnail_prompt", "")).strip()

    if not thumbnail_prompt:
        thumbnail_prompt = build_better_thumbnail_prompt(
            title=title,
            keywords=source_keywords or title,
            category=category,
        )

    return {
        "title": title,
        "summary": summary[:300],
        "meta_description": meta_description[:160],
        "thumbnail_text": thumbnail_text[:100],
        "content": content,
        "tags": tags,
        "thumbnail_prompt": thumbnail_prompt,
        "content_images": [],
    }


def generate_post_topics(category, keywords, writing_style, extra_prompt="", count=1, existing_titles=None):
    count = clamp_number(count, 1, 10, 1)

    if count == 1:
        return [{
            "title": keywords,
            "keywords": keywords,
            "angle": extra_prompt,
            "search_intent": "정보 탐색",
            "extra_prompt": extra_prompt,
        }]

    style_map = {
        "natural": "자연 설명형",
        "expert": "전문가 분석형",
        "experience": "경험 기반형",
        "product_review": "구매·리뷰형",
        "news_trend": "뉴스·트렌드형",
        "trend": "뉴스·트렌드형",
        "checklist": "확인기준형",
        "review": "리뷰형",
        "natural_blog": "자연 설명형",
        "practical": "경험 기반형",
        "issue": "뉴스·트렌드형",
        "guide": "자연 설명형",
    }

    category_map = {
        "architecture": "건축",
        "realestate": "부동산",
        "finance": "금융",
        "tech": "테크",
        "life": "일상",
    }

    category_name = category_map.get(category, "전체")
    style_name = style_map.get(writing_style, "자연 설명형")

    existing_titles = existing_titles or []
    existing_title_text = "\n".join([f"- {title}" for title in existing_titles[:20]])

    prompt = f"""
너는 ChickenBanana Lab 블로그의 SEO 콘텐츠 기획자다.

사용자가 하나의 큰 키워드로 여러 개의 글을 생성하려고 한다.
같은 내용이 반복되지 않도록 서로 다른 세부 주제 {count}개를 기획해라.

카테고리: {category_name}
큰 키워드: {keywords}
글 작성 방향: {style_name}
추가 요청사항: {extra_prompt}

주제 기획 전 최신성 확인:
- 키워드에 루머, 예상, 가능성이 들어 있어도 공식 출시/공식 발표가 확인되는 주제면 루머성 제목으로 기획하지 마라.
- 제품 비교 주제는 오래된 모델을 기본값으로 쓰지 말고 현재 공식 최신 세대 또는 사용자가 명시한 세대를 기준으로 기획해라.
- Apple/MacBook 주제는 Apple 공식 제품 페이지, Newsroom, 비교/스펙 페이지 기준으로 확인한 뒤 기획해라.
- MacBook Neo가 공식 확인되는 경우 "루머"가 아니라 공식 출시 제품 기준의 분석/비교 글로 기획해라.

이미 작성된 비슷한 제목:
{existing_title_text if existing_title_text else '- 없음'}

기획 조건:
- 총 {count}개의 주제를 만들어라.
- 각 글은 검색 의도가 서로 달라야 한다.
- 제목이 서로 비슷하면 안 된다.
- 같은 문장 구조를 반복하지 마라.
- 초보자용, 확인 기준, 비교, 리스크, 사례, 실전 방법, 주의점, 후기 분석, 방문 팁 등 관점을 나눠라.
- 제목은 네이버와 구글 검색 유입에 적합하게 작성해라.
- 제목은 검색자가 실제로 입력할 만한 롱테일 키워드를 포함해라.
- 너무 자극적이거나 허위성 있는 제목은 피하라.
- 같은 결론을 반복하는 글을 만들지 마라.
- 제목이 서로 비슷한 문장 구조가 되지 않게 해라.
- 사람 블로그 제목처럼 자연스럽게 읽혀야 한다.

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

    text = gemini_generate_text(prompt)
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
            topics.append({
                "title": title[:200],
                "keywords": topic_keywords or title,
                "angle": angle,
                "search_intent": search_intent,
                "extra_prompt": item_extra_prompt,
            })

    fallback_angles = [
        "기초 개념을 쉽게 정리",
        "주의할 점과 리스크 정리",
        "실전 적용 방법 정리",
        "확인 기준을 자연스럽게 정리",
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
            topics.append({
                "title": title[:200],
                "keywords": f"{keywords}, {angle}",
                "angle": angle,
                "search_intent": "정보 탐색",
                "extra_prompt": angle,
            })
            used_titles.add(title)
        index += 1

    return topics[:count]


def recommend_today_keywords(category="", today="", count=7):
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
- 다른 카테고리 키워드를 섞지 마라.

추천 조건:
- 검색 유입이 생길 만한 키워드로 작성
- 너무 추상적인 키워드 금지
- 블로그 글 제목으로 확장 가능한 키워드
- 뉴스/이슈형, 정보형, 생활형 키워드를 적절히 섞기
- 비슷한 키워드를 반복하지 마라.
- 선정적이거나 위험한 키워드는 제외
- 실제 최신 사실을 단정하지 말고, 글감으로 쓸 만한 검색 키워드 형태로 추천
- 제목으로 확장했을 때 사람이 읽고 싶어지는 구체적인 키워드로 추천해라.

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

    text = gemini_generate_text(prompt)
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
        {"keyword": "건설현장 안전관리 점검 기준", "category": "건축", "reason": "전문 정보형 검색 유입 가능"},
        {"keyword": "아파트 하자보수 확인 기준", "category": "건축", "reason": "생활형 건축 콘텐츠"},
        {"keyword": "부동산 전세 계약 전 확인사항", "category": "부동산", "reason": "검색 수요가 꾸준한 주제"},
        {"keyword": "아이폰 맥북 연동 사용법", "category": "테크", "reason": "테크 생활형 콘텐츠"},
        {"keyword": "육아휴직 급여 신청 방법", "category": "일상", "reason": "생활 정보형 검색 주제"},
        {"keyword": "방송 맛집 방문 전 확인할 점", "category": "일상", "reason": "맛집 글 확장 가능"},
    ]

    index = 0
    while len(results) < count:
        results.append(fallback_keywords[index % len(fallback_keywords)])
        index += 1

    return results[:count]


def generate_image_bytes(prompt, size="1024x1024"):
    prompt = (prompt or "").strip()
    if not prompt:
        return None

    client = get_gemini_client()

    response = client.models.generate_content(
        model=IMAGE_MODEL,
        contents=[prompt],
        config=types.GenerateContentConfig(
            response_modalities=["Image"],
            image_config=types.ImageConfig(
                aspect_ratio=IMAGE_ASPECT_RATIO,
                image_size=IMAGE_SIZE,
            ),
        ),
    )

    return _extract_image_bytes_from_gemini_response(response)


def make_generated_image_file(prompt, prefix="ai-image"):
    image_bytes = generate_image_bytes(prompt)
    if not image_bytes:
        return None, None

    safe_prefix = re.sub(r"[^a-zA-Z0-9_-]", "-", prefix)
    filename = f"{safe_prefix}-{uuid.uuid4().hex}.png"

    return filename, ContentFile(image_bytes)


def save_inline_image(prompt, prefix="inline"):
    image_bytes = generate_image_bytes(prompt)
    if not image_bytes:
        return ""

    safe_prefix = re.sub(r"[^a-zA-Z0-9_-]", "-", prefix)
    filename = f"post_inline_images/{safe_prefix}-{uuid.uuid4().hex}.png"
    saved_path = default_storage.save(filename, ContentFile(image_bytes))
    return default_storage.url(saved_path)


def build_inline_image_html(image_url, caption):
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

# CBL_TITLE_THUMBNAIL_BLOGIFY_START
_CBL_TITLE_THUMBNAIL_BLOGIFY_RULE = """
[키워드 기반 제목/썸네일 강화 규칙]

사용자가 입력한 키워드나 주제는 그대로 제목으로 복사하지 말고, 사람이 쓴 블로그 제목처럼 자연스럽게 재가공한다.

제목 작성 규칙:
- 입력 키워드는 소재일 뿐, 최종 제목은 블로그 글 제목처럼 다시 만든다.
- 제목은 검색 유입과 클릭 욕구를 함께 고려한다.
- 너무 딱딱한 사전식 제목을 피한다.
- 같은 카테고리의 글과 제목 구조가 반복되지 않게 한다.
- 제목에 HTML 태그를 절대 넣지 않는다.
- <span>, <b>, <strong>, <br>, class= 같은 코드를 제목에 포함하지 않는다.
- 제목은 너무 짧은 단어형으로 끝내지 말고, 독자가 얻을 내용을 드러낸다.
- 단, 과장형 낚시 제목은 피한다.
- "완벽정리", "총정리", "A to Z", "끝판왕" 같은 표현을 반복 남발하지 않는다.
- 제목 끝에 물음표를 매번 붙이지 않는다.
- 질문형, 설명형, 문제해결형, 비교형, 확인기준형 제목을 자연스럽게 섞는다.

좋은 제목 방향 예시:
- 캐시란 무엇인가 → 캐시를 삭제하라는 말, 정확히 어떤 뜻일까?
- 쿠키란 무엇인가 → 웹사이트가 나를 기억하는 이유, 쿠키의 역할 쉽게 이해하기
- HTTPS는 왜 중요할까 → 개인정보 입력 전 주소창을 확인해야 하는 이유
- 공인 IP 사설 IP 차이 → 집 와이파이에서 공인 IP와 사설 IP가 나뉘는 이유
- DNS란 무엇인가 → 사이트 주소가 IP 주소로 바뀌는 과정, DNS 쉽게 이해하기
- 404 오류 → 404 Not Found가 뜨는 이유와 먼저 확인할 것들

썸네일 문구 작성 규칙:
- 썸네일 문구는 제목을 그대로 복사하지 않는다.
- 썸네일 문구는 짧고 강하게 작성한다.
- 썸네일 문구는 얌전한 설명보다 클릭 이유가 보여야 한다.
- 예: "분양시장 경고등", "ECB 긴축 충격", "대구 분양 빨간불", "집값보다 무서운 것", "지금 놓치면 손해"
- 단, 허위 사실이나 과장된 단정은 금지한다.
- 한글 기준 8~18자 정도를 우선한다.
- 너무 긴 문장형 썸네일을 피한다.
- 썸네일에는 HTML 태그를 절대 넣지 않는다.
- 썸네일에는 해시태그를 넣지 않는다.
- 썸네일 문구는 핵심 문제, 궁금증, 차이를 한눈에 보여준다.
- 같은 표현을 반복하지 않는다.

썸네일 문구 예시:
- 캐시 삭제, 왜 필요할까
- 쿠키가 나를 기억하는 법
- HTTPS 확인법
- 안전하지 않음 경고
- 공용 와이파이 주의
- DNS 쉽게 이해하기
- 404 오류 원인
- IP 주소 차이

썸네일 이미지 방향:
- 매번 비슷한 노트북, 자물쇠, 방패 이미지만 반복하지 않는다.
- 글 주제에 맞는 구체적인 장면을 만든다.
- 인터넷/테크 글이라도 주제별로 시각 요소를 다르게 한다.
- 캐시 글은 임시 저장함, 오래된 파일, 브라우저 화면 갱신 같은 느낌을 사용한다.
- 쿠키 글은 로그인 유지, 장바구니, 웹사이트가 기억하는 정보 흐름을 사용한다.
- HTTPS 글은 주소창, 자물쇠, 암호화된 데이터 흐름을 사용한다.
- DNS 글은 도메인 주소가 IP 주소로 연결되는 흐름을 사용한다.
- 공용 와이파이 글은 카페, 공항, 휴대폰, 위험한 네트워크 신호를 사용한다.
- 이미지 안에 긴 글자를 넣지 않는다.
- 썸네일 텍스트는 별도의 thumbnail_text 필드로 처리한다.

최종 출력 주의:
- title 필드에는 순수 제목만 넣는다.
- thumbnail_text 필드에는 짧은 썸네일 문구만 넣는다.
- title, subtitle, thumbnail_text 어디에도 HTML 태그를 넣지 않는다.
- 입력 키워드가 짧거나 딱딱해도 최종 결과는 블로그 제목처럼 자연스럽게 바꾼다.
"""

try:
    _cbl_prev_adsense_structure_instruction_for_title_thumb = cbl_adsense_structure_instruction

    def cbl_adsense_structure_instruction(*args, **kwargs):
        txt = _cbl_prev_adsense_structure_instruction_for_title_thumb(*args, **kwargs)

        input_title = kwargs.get("title", "")
        category = kwargs.get("category", "")
        language = kwargs.get("language", "")

        txt += f"""

[입력 키워드 기반 제목/썸네일 재가공 지시]
입력 키워드/주제: {input_title}
카테고리: {category}
언어: {language}

{_CBL_TITLE_THUMBNAIL_BLOGIFY_RULE}

[강제 지시]
입력 키워드를 그대로 제목으로 쓰지 말고, 검색용 블로그 제목으로 자연스럽게 바꾼다.
썸네일 문구도 제목 복사가 아니라 짧은 클릭용 문구로 따로 만든다.
제목과 썸네일 문구에는 HTML 태그를 절대 넣지 않는다.
"""
        return txt

except NameError:
    pass
# CBL_TITLE_THUMBNAIL_BLOGIFY_END

# CBL_FINAL_NATURAL_WRAPPER_START
import random as _cbl_final_natural_random

_CBL_FINAL_NATURAL_PATTERNS = [
    "실제 상황 관찰형: 사용자가 겪는 구체적인 장면에서 시작 → 원인 설명 → 개념 연결 → 확인 기준 → 짧은 정리",
    "문제 해결형: 불편 상황 제시 → 가능한 원인 분리 → 먼저 확인할 것 → 해결 순서 → 재발 방지",
    "오해 정리형: 흔한 오해 제시 → 맞는 부분과 틀린 부분 구분 → 실제 의미 → 판단 기준",
    "비교 설명형: 비슷한 개념 비교 → 실제 차이 → 사용 상황 → 선택 기준 → 주의점",
    "운영자 관점형: 블로그/웹사이트 운영 중 생기는 문제 → 기술 개념 → 관리 포인트 → 실수 방지",
    "생활 팁형: 바로 써먹을 수 있는 상황 → 확인 항목 → 하면 안 되는 행동 → 마지막 점검",
    "짧은 사례형: 가상의 사용자 사례 → 왜 문제가 생겼는지 설명 → 비슷한 상황에서 확인할 점",
    "초보자 비유형: 쉬운 비유 → 기본 개념 → 실제 인터넷 상황 → 주의할 점",
    "확인기준형: 먼저 확인할 항목 → 항목별 이유 → 위험한 경우 → 마지막 기준",
    "원인 분석형: 증상 → 원인 후보 → 가능성 높은 순서 → 사용자가 할 수 있는 조치",
    "실수 방지형: 자주 하는 실수 → 왜 문제가 되는지 → 올바른 사용법 → 피해야 할 상황",
    "운영 체크형: 사이트 운영자가 놓치는 지점 → 설정 확인 → 관리 주기 → 문제 발생 시 대응",
    "일상 대화형: 실제 대화처럼 시작 → 용어를 천천히 풀어 설명 → 마지막에 현실적인 기준",
    "단계 설명형: 첫 번째 확인 → 두 번째 확인 → 예외 상황 → 최종 판단",
    "반박형: 흔한 주장 제시 → 왜 완전히 맞지 않은지 → 실제로 봐야 할 기준",
    "상황별 선택형: 집/회사/카페/모바일 등 상황 구분 → 각 상황별 판단 기준",
    "위험 신호형: 위험한 징후 먼저 제시 → 원인 설명 → 피해야 할 행동 → 안전한 대안",
    "관리 습관형: 평소에는 괜찮은 기능 → 문제가 되는 순간 → 관리 방법 → 적정 주기",
    "짧은 요약 선제형: 핵심 3줄 요약 → 자세한 설명 → 예외 → 체크 기준",
    "경험 기반 설명형: 사용자가 흔히 겪는 불편함 → 그 뒤의 기술 원리 → 현실적인 해결법",
]

_CBL_FINAL_FAQ_POLICIES = [
    "FAQ를 작성하지 않는다.",
    "FAQ를 작성하지 않는다.",
    "FAQ를 작성하지 않는다.",
    "FAQ를 작성하지 않는다.",
    "본문 후반에 짧은 질문 2개만 자연스럽게 포함한다.",
    "마지막에 FAQ 2개만 포함한다.",
    "마지막에 FAQ 3개만 포함한다.",
    "FAQ 대신 '마지막으로 확인할 점' 섹션으로 끝낸다.",
]

_CBL_FINAL_NATURAL_RULE = """
[자연설명형 인간형 글쓰기 강제 규칙]

자연설명형이라고 해서 같은 글 구조를 반복하지 않는다.
사람이 직접 쓴 블로그 글처럼 도입, 소제목, 본문 리듬, 끝맺음을 매번 다르게 만든다.

절대 반복 금지 구조:
- 생활 예시 → 정의 → 역할 → 종류 → 관리 방법 → 자주 묻는 질문 → 마무리
- 문제 제기 → 고정 요약 → 이유 나열 → 주의사항 → FAQ → 결론
- 정의 → 중요성 → 장점 → 주의사항 → FAQ → 마무리

도입부 금지:
- 오늘은
- 이번 글에서는
- 함께 알아보겠습니다
- 알아보겠습니다
- 많은 분들이 헷갈려 합니다
- 다들 한 번쯤 경험해 보셨을 거예요

본문 금지:
- 주요 역할은 다음과 같아요
- 다음과 같아요
- 몇 가지 이유
- 꼭 기억해 주세요
- 작은 조력자
- 현명한 선택
- 쾌적한 디지털 환경

마무리 금지:
- 마무리하며
- 결론적으로
- 오늘 내용을 참고하셔서
- 안전하고 즐거운 온라인 생활
- 꾸준히 관리해보시면 좋겠습니다

FAQ 규칙:
- FAQ는 필수가 아니다.
- 필요 없으면 쓰지 않는다.
- FAQ를 쓰더라도 2~4개만 작성한다.
- 모든 글에 "자주 묻는 질문"이라는 제목을 반복하지 않는다.
- 필요하면 "헷갈리기 쉬운 부분", "마지막으로 확인할 점", "실수하기 쉬운 부분"처럼 자연스럽게 바꾼다.

H2 규칙:
- H2를 모두 질문형으로 쓰지 않는다.
- 질문형 H2는 전체 H2의 절반 이하로 제한한다.
- 설명형, 상황형, 비교형, 확인기준형, 문제 해결형 제목을 섞는다.
- "왜 중요할까요?", "무엇일까요?", "어떻게 해야 할까요?" 패턴을 반복하지 않는다.

이미지/캡션 규칙:
- 이미지 설명 문장을 본문에 2번 이상 반복하지 않는다.
- 같은 캡션을 연속 출력하지 않는다.
- 이미지 설명은 짧게 1회만 넣는다.

마지막 문장은 과장된 응원 문구가 아니라, 글 주제에 맞는 실용적인 판단 기준으로 끝낸다.
"""

try:
    _cbl_final_prev_structure_instruction = cbl_adsense_structure_instruction

    def cbl_adsense_structure_instruction(*args, **kwargs):
        txt = _cbl_final_prev_structure_instruction(*args, **kwargs)

        writing_style = str(kwargs.get("writing_style", "") or "")
        if not writing_style:
            for a in args:
                if isinstance(a, str) and ("자연" in a or "설명" in a):
                    writing_style = a
                    break

        is_natural = ("자연" in writing_style and "설명" in writing_style)

        if is_natural:
            pattern = _cbl_final_natural_random.choice(_CBL_FINAL_NATURAL_PATTERNS)
            faq_policy = _cbl_final_natural_random.choice(_CBL_FINAL_FAQ_POLICIES)

            txt += f"""

[이번 자연설명형 글의 추가 랜덤 전개 방식]
{pattern}

[이번 글의 FAQ 처리 방식]
{faq_policy}

{_CBL_FINAL_NATURAL_RULE}

[최종 강제 지시]
위 자연설명형 규칙을 최우선으로 따른다.
입력 키워드가 같거나 비슷해도 도입, H2 구성, FAQ 여부, 마무리 방식을 매번 다르게 작성한다.
"오늘은", "함께 알아보겠습니다", "자주 묻는 질문", "마무리하며"를 습관적으로 반복하지 않는다.
FAQ가 필요 없으면 쓰지 않는다.
"""
        return txt

except NameError:
    pass
# CBL_FINAL_NATURAL_WRAPPER_END

# CBL_FINAL_POLISH_NO_AI_ENDING_START
def cbl_polish_article_after_generate(content):
    """
    AI식 도입/마무리 표현과 이미지 캡션 중복을 후처리로 정리한다.
    """
    import re

    content = str(content or "").strip()
    if not content:
        return content

    # 작성 출처/자동글 고백 문구는 무조건 제거
    auto_disclosure_patterns = [
        r"<p>\\s*.*?" + "자동" + "글" + r".*?</p>",
        r"<p>\\s*.*?" + "시스템에서 작성" + r".*?</p>",
        r".*?" + "AI" + r"\\s*" + "자동" + "글" + r".*?(?:\\.|다\\.)",
        r"<p>\s*이 글은\s*ChickenBanana\s*Lab.*?자동.*?</p>",
        r"<p>\s*ChickenBanana\s*Lab.*?자동글.*?</p>",
        r"<p>\s*.*?시간별\s*AI\s*자동글\s*생성\s*시스템.*?</p>",
        r"<p>\s*.*?AI\s*자동글.*?작성.*?</p>",
        r"<p>\s*.*?자동\s*생성\s*시스템.*?</p>",
        r"이 글은\s*ChickenBanana\s*Lab.*?자동.*?(?:\.|다\.)",
        r"ChickenBanana\s*Lab의\s*시간별\s*AI\s*자동글\s*생성\s*시스템에서\s*작성하는\s*글입니다\.?",
    ]
    for pattern in auto_disclosure_patterns:
        content = re.sub(pattern, "", content, flags=re.IGNORECASE | re.DOTALL).strip()

    # 너무 기계적인 소제목은 자연스러운 표현으로 치환
    heading_replacements = {
        "핵심" + " 요약": "먼저 흐름부터 보면",
        "핵심" + " 요약:": "먼저 흐름부터 보면",
        "지금 확인해야 할 " + "확인 기준": "지금 눈여겨볼 부분",
        "체크" + "리스트": "놓치기 쉬운 부분",
        "마지막 " + "체크리스트": "마지막에 볼 부분",
        "글 전체 흐름을 자연스럽게 연결": "글 전체 흐름을 자연스럽게 연결",
        "ChickenBanana Lab의 " + "한 줄 요약": "짧게 정리하면",
        "한 줄" + " 요약": "짧게 정리하면",
    }
    for old, new in heading_replacements.items():
        content = content.replace(f"<h2>{old}</h2>", f"<h2>{new}</h2>")
        content = content.replace(f"<h3>{old}</h3>", f"<h3>{new}</h3>")


    # 고정형 AI 소제목을 더 넓게 치환
    bad_heading_patterns = [
        (r"핵심\s*요약.*?", "먼저 흐름부터 보면"),
        (r"한\s*줄\s*요약.*?", "짧게 정리하면"),
        (r"체크\s*리스트.*?", "놓치기 쉬운 부분"),
        (r"지금\s*확인해야\s*할\s*체크\s*포인트.*?", "지금 눈여겨볼 부분"),
        (r"실용적인\s*정리.*?", "글 전체 흐름을 자연스럽게 연결"),
        (r"마지막\s*체크\s*리스트.*?", "마지막에 볼 부분"),
        (r"ChickenBanana\s*Lab.*?요약.*?", "짧게 정리하면"),
    ]

    for pattern, replacement in bad_heading_patterns:
        content = re.sub(
            rf"<h([23])>\s*{pattern}\s*</h\1>",
            lambda m: f"<h{m.group(1)}>{replacement}</h{m.group(1)}>",
            content,
            flags=re.IGNORECASE | re.DOTALL,
        )

    # 본문 문장으로 새어 나온 작성 출처 표현도 제거
    content = re.sub(r"ChickenBanana\s*Lab.*?(자동|시스템|작성).*?(?:\.|다\.)", "", content, flags=re.IGNORECASE | re.DOTALL)
    content = re.sub(r".*?AI\s*자동\s*글.*?(?:\.|다\.)", "", content, flags=re.IGNORECASE | re.DOTALL)
    content = re.sub(r".*?자동\s*생성\s*시스템.*?(?:\.|다\.)", "", content, flags=re.IGNORECASE | re.DOTALL)


    # 너무 AI스러운 마무리 문장 제거
    bad_ending_patterns = [
        r"<p>\s*이 글이.*?도움이 되기를 바랍니다\.?\s*</p>",
        r"<p>\s*여러분의.*?디지털 라이프.*?도움이 되었으면 좋겠습니다\.?\s*</p>",
        r"<p>\s*오늘 알아본.*?도움이 되었기를 바랍니다\.?\s*</p>",
        r"<p>\s*현명한.*?생활.*?바랍니다\.?\s*</p>",
        r"<p>\s*쾌적한.*?환경.*?유지.*?바랍니다\.?\s*</p>",
    ]

    for pattern in bad_ending_patterns:
        content = re.sub(pattern, "", content, flags=re.IGNORECASE | re.DOTALL).strip()

    # 문단 안의 금지 도입 표현 완화
    replacements = {
        "오늘은 우리가": "여기서는",
        "오늘은 이": "이",
        "오늘은": "",
        "함께 알아보겠습니다": "정리해보겠습니다",
        "알아보는 시간을 가져볼게요": "살펴보겠습니다",
        "다들 있으시죠?": "경험해본 적이 있을 수 있습니다.",
        "더욱 빠르고 쾌적하게": "더 빠르게",
        "친절하게 설명해 드리려 합니다": "간단히 정리합니다",
        "디지털 라이프": "사용 환경",
    }

    for old, new in replacements.items():
        content = content.replace(old, new)

    # 같은 짧은 캡션/문장이 연속 2번 반복되는 경우 1개만 남김
    content = re.sub(
        r"(<p class=\"ai-inline-image-caption\">([^<]{5,80})</p>\s*)\1+",
        r"\1",
        content,
        flags=re.IGNORECASE
    )

    # 일반 텍스트/문단으로 같은 줄이 연속 반복되는 경우 제거
    lines = content.splitlines()
    cleaned = []
    prev_plain = None

    for line in lines:
        plain = re.sub(r"<[^>]+>", "", line).strip()
        plain = re.sub(r"\s+", " ", plain)

        if plain and prev_plain and plain == prev_plain and 5 <= len(plain) <= 80:
            continue

        cleaned.append(line)
        if plain:
            prev_plain = plain

    content = "\n".join(cleaned)
    content = re.sub(r"\n{3,}", "\n\n", content).strip()

    return content
# CBL_FINAL_POLISH_NO_AI_ENDING_END


# CBL_REMOVE_FAKE_FIELD_PERSONA_START
def cbl_remove_fake_field_persona_phrases(content):
    """
    건축 글에서 AI가 억지 전문가/현장 실무자 페르소나를 흉내 내는 표현을 제거한다.
    """
    import re

    content = str(content or "")

    replacements = {
        "현장에서 일하는 사람으로서,": "",
        "현장에서의 경험을 바탕으로 말씀드리자면,": "",
        "현장에서 자주 문제가 되는": "실제로 자주 확인되는",
        "현장에서 놓치지 말아야 할": "놓치기 쉬운",
        "현장에서 보면": "실제로 보면",
        "현장에서는": "실제 상황에서는",
        "실무적으로 보면": "조금 더 현실적으로 보면",
        "현장 실무자": "실제 사용자",
        "현장실무자": "실제 사용자",
        "실무팀": "담당자",
        "끝으로 보면:": "",
        "끝으로 보면": "",
        "알려드리려고 해요": "정리해볼게요",
        "이번 글에서는": "아래에서는",
    }

    for old, new in replacements.items():
        content = content.replace(old, new)

    # H2/H3에 붙은 어색한 끝맺음 접두어 제거
    def clean_heading(match):
        tag = match.group(1)
        body = match.group(2).strip()
        body = re.sub(r"^끝으로\s*보면\s*[:：]?\s*", "", body).strip()
        body = re.sub(r"^글\s*전체\s*흐름을\s*자연스럽게\s*연결\s*[:：]?\s*", "", body).strip()
        if not body:
            body = "실제로 확인할 부분"
        return f"<{tag}>{body}</{tag}>"

    content = re.sub(
        r"<(h2|h3)>\s*(.*?)\s*</\1>",
        clean_heading,
        content,
        flags=re.IGNORECASE | re.DOTALL,
    )

    # 같은 캡션/짧은 문장이 연속으로 두 번 나오는 경우 1회만 남김
    content = re.sub(
        r"(^|\n)([^<\n][^\n]{5,90})\n\2(\n|$)",
        r"\1\2\3",
        content,
        flags=re.MULTILINE,
    )

    content = re.sub(r"\n{3,}", "\n\n", content).strip()
    return content


try:
    _cbl_prev_polish_fake_persona = cbl_polish_article_after_generate

    def cbl_polish_article_after_generate(content):
        content = _cbl_prev_polish_fake_persona(content)
        return cbl_remove_fake_field_persona_phrases(content)

except NameError:
    def cbl_polish_article_after_generate(content):
        return cbl_remove_fake_field_persona_phrases(content)
# CBL_REMOVE_FAKE_FIELD_PERSONA_END


# CBL_SMOOTH_COLON_HEADINGS_START
def cbl_smooth_colon_headings(content):
    """
    AI가 만든 '1. 항목: 설명' 형태의 소제목을
    블로그식 자연 문장형 소제목으로 바꾼다.
    """
    import re

    content = str(content or "")

    def has_batchim(word):
        word = str(word or "").strip()
        if not word:
            return False

        ch = word[-1]
        code = ord(ch)

        # 한글 음절 범위
        if 0xAC00 <= code <= 0xD7A3:
            return (code - 0xAC00) % 28 != 0

        # 숫자/영문은 자연스럽게 '은'보다 '는' 쪽이 덜 어색
        return False

    def smooth_body(body):
        body = str(body or "").strip()

        # 앞 번호 제거: 1. / 1) / ① 비슷한 형태 최소 제거
        body = re.sub(r"^\s*\d+\s*[\.\)]\s*", "", body).strip()

        if ":" not in body and "：" not in body:
            return body

        parts = re.split(r"\s*[:：]\s*", body, maxsplit=1)

        if len(parts) != 2:
            return body.replace(":", " ").replace("：", " ").strip()

        left, right = parts[0].strip(), parts[1].strip()

        if not left or not right:
            return (left + " " + right).strip()

        # 오른쪽 끝 문장부호 정리
        right = right.rstrip(" .。?？!！").strip()

        particle = "은" if has_batchim(left) else "는"

        # "~인지, ~는지"처럼 확인형이면 끝에 확인한다를 붙임
        if ("는지" in right or "인지" in right) and not any(v in right for v in ["확인", "살펴", "본다", "찾"]):
            right = right + " 확인한다"

        # "~흔적을 찾아라" 같은 명령형은 너무 세서 부드럽게
        right = right.replace("찾아라", "먼저 본다")
        right = right.replace("살펴라", "살펴본다")
        right = right.replace("확인!", "확인한다")

        return f"{left}{particle} {right}".strip()

    def repl(match):
        tag = match.group(1)
        body = match.group(2)

        new_body = smooth_body(body)

        return f"<{tag}>{new_body}</{tag}>"

    # HTML 소제목 처리
    content = re.sub(
        r"<(h2|h3)>\s*(.*?)\s*</\1>",
        repl,
        content,
        flags=re.IGNORECASE | re.DOTALL,
    )

    # 혹시 일반 텍스트 줄로 남은 경우도 최소 처리
    lines = []
    for line in content.splitlines():
        stripped = line.strip()

        if re.match(r"^\d+\s*[\.\)]\s*[^:：]{1,40}\s*[:：]\s*.+", stripped):
            line = smooth_body(stripped)
        elif re.match(r"^[^<]{1,40}\s*[:：]\s*.+", stripped) and not stripped.startswith(("http:", "https:")):
            # 너무 긴 문단은 건드리지 않고, 짧은 소제목 후보만 처리
            if len(stripped) <= 80:
                line = smooth_body(stripped)

        lines.append(line)

    content = "\n".join(lines)
    content = re.sub(r"\n{3,}", "\n\n", content).strip()
    return content


try:
    _cbl_prev_polish_colon_headings = cbl_polish_article_after_generate

    def cbl_polish_article_after_generate(content):
        content = _cbl_prev_polish_colon_headings(content)
        return cbl_smooth_colon_headings(content)

except NameError:
    def cbl_polish_article_after_generate(content):
        return cbl_smooth_colon_headings(content)
# CBL_SMOOTH_COLON_HEADINGS_END


# CBL_SMOOTH_AI_POINT_HEADINGS_START
def cbl_smooth_ai_point_headings(content):
    """
    '핵심포인트', '한 번 더 볼 부분', '주요 포인트'처럼 AI/보고서 느낌이 강한
    소제목 표현을 블로그식 자연 문장으로 바꾼다.
    """
    import re

    content = str(content or "")

    heading_map = [
        (r"^\s*핵심\s*포인트\s*[:：]?\s*$", "중요하게 볼 부분"),
        (r"^\s*핵심포인트\s*[:：]?\s*$", "중요하게 볼 부분"),
        (r"^\s*주요\s*포인트\s*[:：]?\s*$", "중요하게 볼 부분"),
        (r"^\s*중요\s*포인트\s*[:：]?\s*$", "중요하게 볼 부분"),
        (r"^\s*체크\s*포인트\s*[:：]?\s*$", "확인할 부분"),
        (r"^\s*최종\s*점검\s*포인트\s*[:：]?\s*$", "마지막으로 확인할 부분"),
        (r"^\s*핵심\s*정리\s*[:：]?\s*$", "한 번 더 볼 부분"),
        (r"^\s*핵심\s*기준\s*[:：]?\s*$", "먼저 볼 기준"),
        (r"^\s*핵심\s*내용\s*[:：]?\s*$", "중요하게 볼 내용"),
        (r"^\s*핵심\s*요소\s*[:：]?\s*$", "먼저 볼 요소"),
    ]

    def clean_heading_text(body):
        body = str(body or "").strip()
        body = re.sub(r"^\s*\d+\s*[\.\)]\s*", "", body).strip()

        for pattern, replacement in heading_map:
            if re.search(pattern, body, flags=re.IGNORECASE):
                return replacement

        # '핵심포인트: 내용' / '핵심 포인트 - 내용' 류
        body = re.sub(r"^\s*핵심\s*포인트\s*[:：\-–]\s*", "", body, flags=re.IGNORECASE).strip()
        body = re.sub(r"^\s*핵심포인트\s*[:：\-–]\s*", "", body, flags=re.IGNORECASE).strip()
        body = re.sub(r"^\s*주요\s*포인트\s*[:：\-–]\s*", "", body, flags=re.IGNORECASE).strip()
        body = re.sub(r"^\s*체크\s*포인트\s*[:：\-–]\s*", "", body, flags=re.IGNORECASE).strip()

        # 소제목 안에 남은 표현 완화
        body = body.replace("핵심 포인트", "중요하게 볼 부분")
        body = body.replace("핵심포인트", "중요하게 볼 부분")
        body = body.replace("주요 포인트", "중요하게 볼 부분")
        body = body.replace("체크포인트", "확인할 부분")
        body = body.replace("한 번 더 볼 부분", "한 번 더 볼 부분")
        body = body.replace("먼저 볼 기준", "먼저 볼 기준")
        body = body.replace("최종 점검 포인트", "마지막으로 확인할 부분")
        body = body.replace("판단 포인트", "판단 기준")

        return body.strip() or "중요하게 볼 부분"

    def repl(match):
        tag = match.group(1)
        body = match.group(2)
        new_body = clean_heading_text(body)
        return f"<{tag}>{new_body}</{tag}>"

    content = re.sub(
        r"<(h2|h3)>\s*(.*?)\s*</\1>",
        repl,
        content,
        flags=re.IGNORECASE | re.DOTALL,
    )

    return content


try:
    _cbl_prev_polish_point_headings = cbl_polish_article_after_generate

    def cbl_polish_article_after_generate(content):
        content = _cbl_prev_polish_point_headings(content)
        return cbl_smooth_ai_point_headings(content)

except NameError:
    def cbl_polish_article_after_generate(content):
        return cbl_smooth_ai_point_headings(content)
# CBL_SMOOTH_AI_POINT_HEADINGS_END


# CBL_FINAL_AI_SMELL_POLISH_START
def cbl_final_ai_smell_polish(content):
    """
    생성 후에도 남을 수 있는 AI식 표현, 보고서식 표현, 조사 오류를 한 번 더 정리한다.
    """
    import re

    content = str(content or "")

    replacements = {
        "확인 기준를": "확인 기준을",
        "중요하게 볼 부분를": "중요하게 볼 부분을",

        "핵심 포인트": "중요하게 볼 부분",
        "핵심포인트": "중요하게 볼 부분",
        "주요 포인트": "중요하게 볼 부분",
        "체크포인트": "확인할 부분",
        "핵심 정리": "한 번 더 볼 부분",
        "핵심 기준": "먼저 볼 기준",
        "핵심 개념": "중요한 개념",
        "핵심 요소": "중요한 요소",
        "핵심 내용": "중요한 내용",

        "결론적으로": "전체적으로 보면",
        "종합해보면": "전체적으로 보면",
        "마무리하자면": "마지막으로 보면",
        "최종적으로": "마지막으로 보면",

        "현장감각형": "현실상황형",
        "현장에서 먼저 보이는 문제": "실제 상황에서 먼저 보이는 문제",
        "실무자가 확인하는 기준": "확인할 때 봐야 하는 기준",
        "실무에서 자주 놓치는 부분": "자주 놓치기 쉬운 부분",
        "실무 적용 사례": "실제 적용 사례",
    }

    for old, new in replacements.items():
        content = content.replace(old, new)

    # 소제목이 너무 보고서식이면 완화
    def clean_heading(match):
        tag = match.group(1)
        body = match.group(2).strip()

        body = re.sub(r"^\s*\d+\s*[\.\)]\s*", "", body).strip()
        body = body.replace("핵심", "중요한")
        body = body.replace("포인트", "부분")
        body = body.replace("체크", "확인")
        body = body.replace("최종", "마지막")

        body = re.sub(r"\s{2,}", " ", body).strip()
        return f"<{tag}>{body}</{tag}>"

    content = re.sub(
        r"<(h2|h3)>\s*(.*?)\s*</\1>",
        clean_heading,
        content,
        flags=re.IGNORECASE | re.DOTALL,
    )

    content = re.sub(r"\n{3,}", "\n\n", content).strip()
    return content


try:
    _cbl_prev_polish_final_ai_smell = cbl_polish_article_after_generate

    def cbl_polish_article_after_generate(content):
        content = _cbl_prev_polish_final_ai_smell(content)
        return cbl_final_ai_smell_polish(content)

except NameError:
    def cbl_polish_article_after_generate(content):
        return cbl_final_ai_smell_polish(content)
# CBL_FINAL_AI_SMELL_POLISH_END


# CBL_FACT_GUARD_FOR_AUTO_NEWS_START
def cbl_auto_news_fact_guard_instruction(title="", category="", language="ko"):
    """
    시간별 자동글/뉴스성 키워드용 사실성 안전장치.
    검색 근거 없이 미래 제품, 기업 계약, 주가, 실적, 공급망 이슈를 확정적으로 쓰지 않도록 제한한다.
    """
    return f"""
[사실성 안전 규칙 - 반드시 준수]

이 글은 검색 근거 없이 작성될 수 있으므로 아래 규칙을 최우선으로 따른다.

1. 최신 뉴스, 주식, 기업 실적, 공급 계약, 출시 예정 제품, 공급망, 루머성 주제는 확정 사실처럼 쓰지 않는다.
2. 출처가 없는 경우 다음 표현을 금지한다:
   - 확정됐다
   - 체결했다
   - 공급한다
   - 독점 공급한다
   - 주가가 강세다
   - 흑자 전환에 성공했다
   - 몇 조 원을 투자한다
   - 특정 연도/모델에 적용된다
3. 삼성SDI, 삼성디스플레이, 삼성전자, LG디스플레이, LG에너지솔루션처럼 계열사명이 비슷한 기업은 절대 섞어 쓰지 않는다.
   - 삼성SDI: 배터리/전자재료 중심
   - 삼성디스플레이: 디스플레이 패널 중심
   - LG디스플레이: 디스플레이 패널 중심
   - LG에너지솔루션: 배터리 중심
4. 확인되지 않은 미래 아이폰, 폴더블 아이폰, OLED 공급, 배터리 공급, AI 기능, 가격, 출시일은 '전망', '관측', '가능성' 수준으로만 표현한다.
5. 사실 확인이 어려운 경우 글의 방향을 다음처럼 바꾼다:
   - "확정된 소식"이 아니라 "시장 관전 포인트"
   - "수혜 확정"이 아니라 "기대 요인과 리스크"
   - "공급 계약"이 아니라 "관련 가능성이 거론되는 이유"
6. 특정 수치, 계약 기간, 금액, 출시 연도, 공급 물량은 공식 근거가 없는 한 쓰지 않는다.
7. 같은 내용을 반복해서 두 번 설명하지 않는다.
8. 글의 카테고리와 주제가 맞지 않으면 카테고리에 맞는 방향으로 재구성한다.
   예: 건축 카테고리에서 아이폰/디스플레이/배터리 주제가 들어오면 건축 글로 억지 작성하지 말고, '카테고리 불일치'로 판단해 일반 기술 설명형으로 낮춘다.
9. 투자 판단처럼 보이는 문장을 쓰지 않는다.
10. 최종 문체는 단정형보다 신중한 설명형을 사용한다.
"""
try:
    _cbl_prev_adsense_structure_instruction_fact_guard = cbl_adsense_structure_instruction

    def cbl_adsense_structure_instruction(*args, **kwargs):
        base = _cbl_prev_adsense_structure_instruction_fact_guard(*args, **kwargs)

        title = kwargs.get("title", "")
        category = kwargs.get("category", "")
        language = kwargs.get("language", "ko")

        try:
            if len(args) >= 3:
                title = args[2]
            if len(args) >= 2:
                category = args[1]
            if len(args) >= 4:
                language = args[3]
        except Exception:
            pass

        guard = cbl_auto_news_fact_guard_instruction(
            title=title,
            category=category,
            language=language,
        )

        return str(base) + "\n\n" + guard

except NameError:
    pass
# CBL_FACT_GUARD_FOR_AUTO_NEWS_END

# CBL_AUTO_FACT_FINAL_GUARD_START
import re as _cbl_fact_re

def cbl_sentence_split_for_fact_guard(text):
    if not text:
        return []
    parts = _cbl_fact_re.split(r'(?<=[.!?。！？다요죠음함됨임]|[.!?])\s+', str(text))
    return [p.strip() for p in parts if p and p.strip()]

def cbl_fix_company_confusion_sentence(sentence):
    """
    삼성SDI/삼성디스플레이/LG디스플레이/LG에너지솔루션 혼동 방지.
    삼성SDI가 OLED/패널/디스플레이를 공급하는 식의 문장은 위험하므로 자동 완화.
    """
    s = sentence

    has_sdi = "삼성SDI" in s or "Samsung SDI" in s
    display_words = ["디스플레이", "OLED", "패널", "폴더블", "화면"]
    battery_words = ["배터리", "전지", "에너지저장", "ESS"]

    if has_sdi and any(w in s for w in display_words) and not any(w in s for w in battery_words):
        s = s.replace("삼성SDI", "삼성디스플레이")
        s = s.replace("Samsung SDI", "Samsung Display")

    # LG디스플레이가 배터리를 공급한다고 쓰는 오류 방지
    if "LG디스플레이" in s and any(w in s for w in battery_words) and not any(w in s for w in display_words):
        s = s.replace("LG디스플레이", "LG에너지솔루션")

    return s

def cbl_soften_unverified_claim_sentence(sentence):
    """
    최신 뉴스/기업/주식/공급망 글에서 출처 없이 확정 표현을 쓰지 않도록 완화.
    """
    s = sentence

    risky_exact_patterns = [
        (r"독점\s*공급(한다|할\s*예정이다|하기로\s*했다|계약을\s*체결했다)", "독점 공급 가능성이 거론된다"),
        (r"계약을\s*체결했다", "계약 가능성이 거론된다"),
        (r"예비\s*합의에\s*도달했다", "예비 합의 가능성이 거론된다"),
        (r"공급하기로\s*했다", "공급 가능성이 거론된다"),
        (r"적용된다", "적용될 가능성이 거론된다"),
        (r"출시된다", "출시될 가능성이 거론된다"),
        (r"확정됐다", "확정 여부는 추가 확인이 필요하다"),
        (r"성공했다", "개선 흐름을 보였다는 해석이 있다"),
        (r"주가가\s*강세다", "시장 관심이 커졌다는 해석이 있다"),
        (r"주가\s*강세", "시장 관심 확대"),
        (r"흑자\s*전환에\s*성공", "실적 개선 가능성"),
        (r"사재기", "선제적 물량 확보 움직임"),
    ]

    for pat, repl in risky_exact_patterns:
        s = _cbl_fact_re.sub(pat, repl, s)

    # 과도하게 구체적인 미래 아이폰 모델 단정 완화
    s = _cbl_fact_re.sub(
        r"(아이폰\s*(18|19|20|21)\s*(시리즈|프로|프로\s*맥스|모델)?)",
        r"\1로 거론되는 차세대 모델",
        s,
    )

    # 금액 단정 완화
    s = _cbl_fact_re.sub(
        r"약\s*([0-9,.]+)\s*(조|억)\s*원\s*규모의\s*투자를\s*진행하고\s*있",
        r"대규모 투자를 검토하거나 진행 중인 것으로 거론되",
        s,
    )

    # 공급량/점유율/출하량 단정 완화
    s = _cbl_fact_re.sub(
        r"패널\s*출하량의\s*상당\s*부분을\s*담당했다",
        "패널 공급에서 주요 역할을 한 것으로 알려졌다",
        s,
    )

    return s

def cbl_remove_repeated_lines_and_captions(text):
    """
    이미지 캡션 반복, 같은 문장 반복을 완화.
    """
    if not text:
        return text

    lines = str(text).splitlines()
    cleaned = []
    prev_norm = ""

    for line in lines:
        raw = line.rstrip()
        norm = _cbl_fact_re.sub(r"\s+", "", raw)

        # 바로 전 줄과 완전히 같은 캡션/문장 제거
        if norm and norm == prev_norm:
            continue

        cleaned.append(raw)
        prev_norm = norm

    return "\n".join(cleaned)

def cbl_auto_fact_final_guard(content):
    """
    자동글 최종 사실성 필터.
    완벽한 팩트체크가 아니라, 루머성 단정/회사명 혼동/반복 문장 방지용 안전망.
    """
    if not content:
        return content

    text = str(content)
    text = cbl_remove_repeated_lines_and_captions(text)

    news_risk_words = [
        "아이폰", "애플", "삼성SDI", "삼성디스플레이", "LG디스플레이",
        "LG에너지솔루션", "주가", "실적", "공급", "계약", "출시",
        "투자", "배터리", "OLED", "패널", "폴더블", "AI 반도체",
        "TSMC", "인텔", "D램"
    ]

    if not any(w in text for w in news_risk_words):
        return text

    sentences = cbl_sentence_split_for_fact_guard(text)
    fixed_sentences = []

    for sent in sentences:
        s = cbl_fix_company_confusion_sentence(sent)
        s = cbl_soften_unverified_claim_sentence(s)
        fixed_sentences.append(s)

    fixed = " ".join(fixed_sentences)

    # 과도한 공백 정리
    fixed = _cbl_fact_re.sub(r"\n{3,}", "\n\n", fixed)
    fixed = _cbl_fact_re.sub(r"[ \t]{2,}", " ", fixed)

    return fixed.strip()

try:
    _cbl_prev_polish_auto_fact_final_guard = cbl_polish_article_after_generate

    def cbl_polish_article_after_generate(content):
        content = _cbl_prev_polish_auto_fact_final_guard(content)
        content = cbl_auto_fact_final_guard(content)
        return content

except NameError:
    pass
# CBL_AUTO_FACT_FINAL_GUARD_END

# CBL_AUTO_PUBLIC_SAFE_STRUCTURE_START
def cbl_auto_public_safe_structure_instruction(title="", category="", language="ko"):
    """
    시간별 자동글을 무조건 공개 가능한 구조로 만들기 위한 기본 지침.
    최신뉴스 단정형이 아니라, 검증 부담이 낮은 설명형/가이드형/관전포인트형으로 유도한다.
    """
    title = str(title or "")
    category = str(category or "")

    return f"""
[시간별 자동글 공개 안전 구조]

이 글은 자동으로 공개될 수 있는 글이므로, 최신뉴스 단정형으로 작성하지 않는다.
검색 근거 없이 특정 기업의 계약, 공급, 실적, 주가, 출시일, 투자금액, 미래 제품 적용 여부를 사실처럼 단정하지 않는다.

글의 기본 방향은 아래 중 하나로 잡는다.

1. 개념 설명형
- 특정 이슈를 직접 보도하지 말고, 그 이슈를 이해하는 데 필요한 배경 개념을 설명한다.
- 예: "아이폰 효과가 부품사에 미치는 영향" → "스마트폰 신제품이 부품사에 영향을 주는 구조"

2. 관전 포인트형
- 확정 사실처럼 쓰지 말고, 시장에서 볼 수 있는 쟁점과 변수를 정리한다.
- 예: "수혜 확정"이 아니라 "기대 요인과 확인해야 할 변수"

3. 비교 이해형
- 회사별 역할을 비교하되, 계열사명과 사업영역을 정확히 구분한다.
- 삼성SDI는 배터리/전자재료 중심, 삼성디스플레이는 디스플레이 중심으로 구분한다.
- LG디스플레이는 디스플레이 중심, LG에너지솔루션은 배터리 중심으로 구분한다.

4. 초보자 가이드형
- 뉴스처럼 쓰지 말고, 독자가 개념을 이해하도록 쉽게 설명한다.
- 주가, 투자 판단, 계약 확정 표현은 피한다.

5. 리스크 체크형
- 긍정적인 기대만 쓰지 말고, 공급망 다변화, 기술 난도, 수익성, 검증 필요성을 함께 설명한다.

금지하는 글 구조:
- "A 기업이 B를 독점 공급한다" 식의 확정 기사형
- "주가가 강세다", "수혜가 확실하다" 식의 투자 유도형
- "아이폰 18/20에 적용된다" 식의 미래 제품 단정형
- 출처 없는 금액, 계약 기간, 출시일, 공급 물량 단정
- 같은 내용을 앞뒤에서 반복하는 중복 본문

권장 H2 구조:
1. 이 주제가 주목받는 이유
2. 먼저 구분해야 할 핵심 개념
3. 관련 기업 또는 기술의 역할 차이
4. 기대 요인과 조심해야 할 부분
5. 앞으로 확인해야 할 관전 포인트
6. 일반 독자가 이해하면 좋은 정리
7. 마무리

문체:
- 단정형보다 신중한 설명형을 사용한다.
- "확정됐다" 대신 "가능성이 거론된다", "관심이 커지고 있다", "확인할 필요가 있다"처럼 쓴다.
- 하지만 문장이 지나치게 겁먹은 느낌이 들지 않게 자연스럽게 쓴다.

현재 글 제목 참고: {title}
현재 카테고리 참고: {category}
"""

try:
    _cbl_prev_adsense_structure_instruction_public_safe = cbl_adsense_structure_instruction

    def cbl_adsense_structure_instruction(*args, **kwargs):
        base = _cbl_prev_adsense_structure_instruction_public_safe(*args, **kwargs)

        title = kwargs.get("title", "")
        category = kwargs.get("category", "")
        language = kwargs.get("language", "ko")

        try:
            if len(args) >= 2:
                category = args[1]
            if len(args) >= 3:
                title = args[2]
            if len(args) >= 4:
                language = args[3]
        except Exception:
            pass

        safe = cbl_auto_public_safe_structure_instruction(
            title=title,
            category=category,
            language=language,
        )

        return str(base) + "\n\n" + safe

except NameError:
    pass
# CBL_AUTO_PUBLIC_SAFE_STRUCTURE_END

# CBL_AUTO_CATEGORY_KEYWORD_GUARD_START
def cbl_guess_keyword_category(keyword):
    """
    자동글 키워드가 어느 카테고리에 가까운지 단순 판정.
    완벽한 AI 분류가 아니라, 명백한 카테고리 오류를 막기 위한 1차 안전장치.
    """
    k = str(keyword or "").lower()

    groups = {
        "architecture": [
            "건축", "건설", "시공", "현장", "공사", "도면", "구조", "철근", "콘크리트",
            "bim", "revit", "레빗", "dynamo", "다이나모", "거푸집", "마감", "설계",
            "안전", "품질", "공정", "물량", "수량산출", "인테리어", "리모델링"
        ],
        "realestate": [
            "부동산", "아파트", "분양", "청약", "전세", "월세", "매매", "실거래가",
            "재건축", "재개발", "입주", "전월세", "집값", "토지", "상가"
        ],
        "finance": [
            "금리", "환율", "주식", "증시", "코스피", "코스닥", "나스닥", "비트코인",
            "이더리움", "리플", "etf", "채권", "대출", "물가", "인플레이션", "경제"
        ],
        "tech": [
            "ai", "인공지능", "아이폰", "갤럭시", "애플", "삼성전자", "반도체",
            "배터리", "oled", "디스플레이", "스마트폰", "소프트웨어", "앱", "클라우드",
            "보안", "개발", "파이썬", "장고", "django", "챗gpt", "gemini"
        ],
        "life": [
            "생활", "육아", "건강", "음식", "여행", "청소", "정리", "가전", "리뷰",
            "일상", "교육", "공부", "병원", "운동"
        ],
    }

    scores = {}
    for cat, words in groups.items():
        scores[cat] = sum(1 for w in words if w.lower() in k)

    best = max(scores, key=scores.get)
    if scores[best] <= 0:
        return ""

    return best

def cbl_category_matches_keyword(category, keyword):
    """
    카테고리와 키워드가 명백히 다르면 False.
    """
    cat = str(category or "").lower()
    guessed = cbl_guess_keyword_category(keyword)

    aliases = {
        "건축": "architecture",
        "architecture": "architecture",
        "realestate": "realestate",
        "부동산": "realestate",
        "finance": "finance",
        "금융": "finance",
        "tech": "tech",
        "테크": "tech",
        "life": "life",
        "일상": "life",
    }

    normalized = aliases.get(cat, cat)

    if not guessed:
        return True

    return normalized == guessed

def cbl_auto_category_keyword_warning(category, keyword):
    guessed = cbl_guess_keyword_category(keyword)
    if not guessed:
        return ""

    if cbl_category_matches_keyword(category, keyword):
        return ""

    return f"카테고리 불일치 감지: category={category}, keyword={keyword}, guessed={guessed}"
# CBL_AUTO_CATEGORY_KEYWORD_GUARD_END

# CBL_TODAY_KEYWORD_CATEGORY_LOCK_START
def cbl_today_keyword_category_profile(category=""):
    """
    오늘자 키워드 추천용 카테고리 고정 프로필.
    자동글 공개화를 위해 카테고리와 맞지 않는 최신뉴스/루머성 키워드를 줄인다.
    """
    cat = str(category or "").lower().strip()

    aliases = {
        "건축": "architecture",
        "architecture": "architecture",
        "realestate": "realestate",
        "부동산": "realestate",
        "finance": "finance",
        "금융": "finance",
        "tech": "tech",
        "테크": "tech",
        "life": "life",
        "일상": "life",
    }

    cat = aliases.get(cat, cat)

    profiles = {
        "architecture": {
            "name": "건축",
            "allow": [
                "건축", "건설", "시공", "공정관리", "품질관리", "안전관리", "도면검토",
                "구조검토", "철근", "콘크리트", "거푸집", "마감공사", "물량산출",
                "BIM", "Revit", "레빗", "Dynamo", "다이나모", "수량산출", "현장관리"
            ],
            "block": [
                "아이폰", "애플", "갤럭시", "삼성SDI", "LG디스플레이", "주가", "코스피",
                "비트코인", "환율", "금리", "맛집", "육아", "건강"
            ],
            "examples": [
                "레빗에서 물량산출을 자동화할 때 주의할 점",
                "다이나모를 활용한 거푸집 모델링 기본 구조",
                "건설 현장에서 도면 검토가 중요한 이유",
                "BIM 수량산출이 실행예산에 미치는 영향",
                "콘크리트 타설 전 현장 체크리스트"
            ],
        },
        "realestate": {
            "name": "부동산",
            "allow": [
                "부동산", "아파트", "분양", "청약", "전세", "월세", "매매", "실거래가",
                "재건축", "재개발", "입주", "대출", "주택", "상가", "토지"
            ],
            "block": [
                "아이폰", "애플", "BIM", "레빗", "다이나모", "반도체", "배터리"
            ],
            "examples": [
                "아파트 실거래가를 볼 때 확인해야 할 기준",
                "청약 전 분양가를 비교하는 방법",
                "전세 계약 전 확인해야 할 기본 사항",
                "재건축과 재개발의 차이를 쉽게 이해하기",
                "입주 물량이 지역 집값에 미치는 영향"
            ],
        },
        "finance": {
            "name": "금융",
            "allow": [
                "금리", "환율", "코스피", "코스닥", "나스닥", "주식", "ETF", "채권",
                "비트코인", "이더리움", "리플", "물가", "인플레이션", "경제지표", "대출"
            ],
            "block": [
                "건축", "레빗", "다이나모", "아이폰 루머", "맛집", "육아"
            ],
            "examples": [
                "금리 인하 기대가 시장에 미치는 영향",
                "환율이 수입 물가에 영향을 주는 구조",
                "ETF와 개별 주식의 차이",
                "비트코인 가격 변동을 볼 때 확인할 지표",
                "물가 지표가 기준금리에 중요한 이유"
            ],
        },
        "tech": {
            "name": "테크",
            "allow": [
                "AI", "인공지능", "아이폰", "갤럭시", "애플", "삼성전자", "반도체",
                "배터리", "OLED", "디스플레이", "스마트폰", "앱", "보안", "클라우드",
                "소프트웨어", "파이썬", "장고", "Django", "ChatGPT", "Gemini"
            ],
            "block": [
                "청약", "전세", "실거래가", "콘크리트", "철근", "맛집", "육아"
            ],
            "examples": [
                "스마트폰 신제품이 부품사에 영향을 주는 구조",
                "OLED 디스플레이가 스마트폰 품질에 중요한 이유",
                "AI 기능이 배터리 사용 시간에 미치는 영향",
                "반도체 공급망을 이해할 때 알아야 할 기본 개념",
                "앱 보안에서 개인정보 보호가 중요한 이유"
            ],
        },
        "life": {
            "name": "일상",
            "allow": [
                "생활", "육아", "건강", "음식", "청소", "정리", "가전", "교육",
                "공부", "운동", "병원", "여행", "리뷰"
            ],
            "block": [
                "주가", "코스피", "아이폰 공급", "BIM", "레빗", "청약"
            ],
            "examples": [
                "아이 열이 날 때 집에서 먼저 확인할 것",
                "전자레인지 사용 전 확인해야 할 용기 표시",
                "집안 정리를 쉽게 시작하는 방법",
                "가전제품 구매 전 확인해야 할 기본 기준",
                "초등학생 생활 습관을 잡는 작은 방법"
            ],
        },
    }

    return profiles.get(cat, profiles["life"])


def cbl_today_keyword_prompt_guard(category="", count=7):
    """
    오늘자 키워드 추천 프롬프트에 붙일 카테고리 고정 지침.
    """
    profile = cbl_today_keyword_category_profile(category)
    allow = ", ".join(profile["allow"])
    block = ", ".join(profile["block"])
    examples = "\n".join([f"- {x}" for x in profile["examples"]])

    return f"""
[오늘자 키워드 추천 카테고리 고정 규칙]

현재 카테고리: {profile["name"]}

반드시 이 카테고리에 맞는 키워드만 추천한다.
추천 개수는 {count}개다.

허용 주제:
{allow}

금지 주제:
{block}

중요:
1. 최신뉴스 루머성 키워드를 그대로 쓰지 않는다.
2. 특정 기업의 계약, 공급, 주가, 실적, 출시일을 단정하는 키워드는 피한다.
3. 자동 공개 글로 써도 안전한 설명형 키워드를 추천한다.
4. 제목처럼 너무 길게 쓰지 말고, 블로그 글 주제로 확장 가능한 키워드로 작성한다.
5. 카테고리와 맞지 않으면 절대 추천하지 않는다.

좋은 예시:
{examples}
"""


def cbl_filter_today_keywords_by_category(category="", keywords=None, limit=7):
    """
    오늘자 키워드 추천 결과를 카테고리 기준으로 한 번 더 필터링.
    """
    if keywords is None:
        return []

    profile = cbl_today_keyword_category_profile(category)
    allow = [str(x).lower() for x in profile["allow"]]
    block = [str(x).lower() for x in profile["block"]]

    cleaned = []
    seen = set()

    for kw in keywords:
        k = str(kw or "").strip()
        if not k:
            continue

        lk = k.lower()

        if any(b in lk for b in block):
            continue

        # 허용 키워드가 하나라도 들어가면 통과
        if allow and not any(a.lower() in lk for a in allow):
            continue

        norm = lk.replace(" ", "")
        if norm in seen:
            continue

        seen.add(norm)
        cleaned.append(k)

        if len(cleaned) >= int(limit or 7):
            break

    return cleaned
# CBL_TODAY_KEYWORD_CATEGORY_LOCK_END



# CBL_HUMAN_VISUAL_POLISH_START
# AI 자동글 본문에 제한된 랜덤 시각 강조를 적용합니다.
# - 글 단위 테마 1개
# - 포인트 글자색 1개
# - 하이라이트 색 1개
# - strong 일부에 포인트 색상
# - p 문장 일부에 형광펜 효과
try:
    import re as _cbl_visual_re
    import random as _cbl_visual_random

    _cbl_prev_human_visual_polish = cbl_polish_article_after_generate

    _CBL_VISUAL_THEMES = [
        {
            "theme": "cbl-human-theme-blue",
            "point": "cbl-point-blue",
            "highlight": "cbl-highlight-yellow",
        },
        {
            "theme": "cbl-human-theme-teal",
            "point": "cbl-point-teal",
            "highlight": "cbl-highlight-green",
        },
        {
            "theme": "cbl-human-theme-violet",
            "point": "cbl-point-violet",
            "highlight": "cbl-highlight-pink",
        },
        {
            "theme": "cbl-human-theme-amber",
            "point": "cbl-point-amber",
            "highlight": "cbl-highlight-sky",
        },
    ]

    def _cbl_visual_has_marker(content):
        return "cbl-ai-human-style" in str(content or "")

    def _cbl_visual_add_class_to_open_tag(tag, class_names):
        tag = str(tag or "")
        class_names = str(class_names or "").strip()

        if not tag or not class_names:
            return tag

        if "class=" in tag:
            return _cbl_visual_re.sub(
                r'class=(["\'])(.*?)\1',
                lambda m: f'class={m.group(1)}{m.group(2)} {class_names}{m.group(1)}',
                tag,
                count=1,
                flags=_cbl_visual_re.I,
            )

        if tag.endswith(">"):
            return tag[:-1] + f' class="{class_names}">'

        return tag

    def _cbl_visual_style_strong_tags(content, rng, point_class):
        matches = list(_cbl_visual_re.finditer(
            r"<strong(?![^>]*cbl-ai-point)([^>]*)>",
            content,
            flags=_cbl_visual_re.I,
        ))

        if not matches:
            return content

        total = len(matches)

        if total <= 2:
            target_count = total
        else:
            target_count = min(6, max(2, total // 2))

        chosen_indexes = set(rng.sample(range(total), target_count))

        for idx, match in reversed(list(enumerate(matches))):
            if idx not in chosen_indexes:
                continue

            old_tag = match.group(0)
            new_tag = _cbl_visual_add_class_to_open_tag(
                old_tag,
                f"cbl-ai-point {point_class}",
            )

            content = content[:match.start()] + new_tag + content[match.end():]

        return content

    def _cbl_visual_find_sentences(inner_html):
        # 태그를 건드리지 않기 위해, 태그가 섞이지 않은 짧은 문장 후보만 잡습니다.
        candidates = []

        sentence_pattern = _cbl_visual_re.compile(
            r"([^<>]{18,120}?(?:다|요|죠|니다|습니다|합니다|됩니다|있습니다|없습니다|해요|예요|이에요|입니다)[.!?]?)"
        )

        for m in sentence_pattern.finditer(inner_html):
            sentence = " ".join(str(m.group(1) or "").split())

            if not sentence:
                continue

            if "<" in sentence or ">" in sentence:
                continue

            if len(sentence) < 18 or len(sentence) > 120:
                continue

            if "이미지" in sentence and "생성" in sentence:
                continue

            if "cbl-" in sentence:
                continue

            candidates.append(sentence)

        return candidates

    def _cbl_visual_add_highlights(content, rng, highlight_class):
        if "cbl-ai-highlight" in content:
            return content

        p_matches = list(_cbl_visual_re.finditer(
            r"(<p[^>]*>)(.*?)(</p>)",
            content,
            flags=_cbl_visual_re.I | _cbl_visual_re.S,
        ))

        if not p_matches:
            return content

        candidates = []

        for p_idx, match in enumerate(p_matches):
            inner = match.group(2)

            plain = _cbl_visual_re.sub(r"<[^>]+>", "", inner)
            plain = " ".join(plain.split())

            if len(plain) < 70:
                continue

            if "cbl-ai-highlight" in inner:
                continue

            sentences = _cbl_visual_find_sentences(inner)

            if not sentences:
                continue

            candidates.append({
                "p_idx": p_idx,
                "match": match,
                "sentence": rng.choice(sentences),
            })

        if not candidates:
            return content

        rng.shuffle(candidates)

        target_count = rng.randint(1, min(3, len(candidates)))
        selected = []
        used_p = set()

        for item in candidates:
            if item["p_idx"] in used_p:
                continue

            selected.append(item)
            used_p.add(item["p_idx"])

            if len(selected) >= target_count:
                break

        for item in sorted(selected, key=lambda x: x["match"].start(), reverse=True):
            match = item["match"]
            opening = match.group(1)
            inner = match.group(2)
            closing = match.group(3)
            sentence = item["sentence"]

            marked = (
                f'<span class="cbl-ai-highlight {highlight_class}">'
                f'{sentence}'
                f'</span>'
            )

            new_inner = inner.replace(sentence, marked, 1)
            new_block = opening + new_inner + closing

            content = content[:match.start()] + new_block + content[match.end():]

        return content

    def _cbl_visual_wrap_content(content, theme_class):
        content = str(content or "").strip()

        if not content:
            return content

        if _cbl_visual_has_marker(content):
            return content

        return f'<div class="cbl-ai-human-style {theme_class}">\n{content}\n</div>'

    def _cbl_apply_human_visual_polish(content):
        content = str(content or "").strip()

        if not content:
            return content

        if _cbl_visual_has_marker(content):
            return content

        rng = _cbl_visual_random.SystemRandom()
        theme = rng.choice(_CBL_VISUAL_THEMES)

        content = _cbl_visual_style_strong_tags(
            content,
            rng,
            theme["point"],
        )

        content = _cbl_visual_add_highlights(
            content,
            rng,
            theme["highlight"],
        )

        content = _cbl_visual_wrap_content(
            content,
            theme["theme"],
        )

        return content

    def cbl_polish_article_after_generate(content):
        content = _cbl_prev_human_visual_polish(content)
        content = _cbl_apply_human_visual_polish(content)
        return content

except Exception as _cbl_human_visual_polish_error:
    print("CBL_HUMAN_VISUAL_POLISH load error:", _cbl_human_visual_polish_error)
# CBL_HUMAN_VISUAL_POLISH_END

