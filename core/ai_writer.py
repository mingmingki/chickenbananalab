import os
import re
import json
import html
import uuid
import base64
import random

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv(override=True)


TEXT_MODEL = os.getenv("GEMINI_TEXT_MODEL", "gemini-2.5-pro")
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
- 너무 깊은 분석보다 핵심 기준, 확인 순서, 주의할 점을 자연스럽게 정리해라.
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
- 건축, 현장, 업무, 육아, 여행, 생활 노하우처럼 실제 판단에 도움이 되는 체크포인트를 넣어라.
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
- 독자가 지금 확인해야 할 체크포인트와 앞으로 볼 변수를 정리해라.
- 짧은 뉴스 요약이 아니라 블로그 독자가 이해하기 쉬운 맥락 설명을 포함해라.
""",
    "checklist": """
체크리스트형 작성 규칙:
- 독자가 바로 확인할 수 있는 순서와 항목 중심으로 작성해라.
- 각 체크 항목은 이유와 확인 방법을 함께 설명해라.
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
    "검색자가 이미 궁금해하는 상황을 먼저 짚고, 그다음 핵심 정보를 자연스럽게 설명하는 방식",
    "개인 블로그처럼 가볍게 문제 상황을 던진 뒤, 바로 실용적인 기준을 알려주는 방식",
    "처음부터 정의를 내리지 말고, 사람들이 헷갈리는 지점부터 풀어가는 방식",
    "정보를 나열하기보다 실제로 선택하거나 판단해야 하는 상황을 먼저 보여주는 방식",
    "짧은 공감 문장으로 시작한 뒤, 바로 체크포인트로 이어가는 방식",
    "검색자가 겪을 만한 작은 불편이나 의문을 먼저 꺼내고, 그걸 풀어주는 방식",
    "딱딱한 설명보다 실제 검색자가 머릿속으로 떠올리는 질문에서 출발하는 방식",
]

HUMAN_STRUCTURE_PATTERNS = [
    """
이번 글 구조는 아래 흐름을 우선 사용해라.
- 짧은 도입
- 바로 핵심 요약
- 사람들이 가장 헷갈리는 부분
- 체크리스트
- FAQ
- 마무리
""",
    """
이번 글 구조는 아래 흐름을 우선 사용해라.
- 검색자가 궁금해할 질문으로 시작
- 핵심 답변 먼저 제시
- 세부 설명
- 주의할 점
- 이런 사람에게 맞는지 정리
- 한 줄 요약
""",
    """
이번 글 구조는 아래 흐름을 우선 사용해라.
- 상황 설명
- 왜 중요한지
- 실제로 봐야 할 기준
- 표 또는 리스트
- 놓치기 쉬운 부분
- FAQ
- 마무리
""",
    """
이번 글 구조는 아래 흐름을 우선 사용해라.
- 짧은 문제 제기
- 핵심 기준 3가지
- 세부 설명
- 실수하기 쉬운 부분
- 마지막 체크리스트
- 자연스러운 마무리
""",
    """
이번 글 구조는 아래 흐름을 우선 사용해라.
- 독자가 궁금해할 핵심부터 답변
- 상황별로 다르게 봐야 할 부분
- 실제 확인 순서
- 주의할 점
- FAQ
- 짧은 정리
""",
]

CATEGORY_VOICE_RULES = {
    "architecture": """
건축 글 말투:
- 현장 실무자가 후배에게 알려주는 느낌으로 작성해라.
- 공정, 하자, 비용, 안전, 시공성 기준을 자연스럽게 포함해라.
- 너무 이론적으로 쓰지 말고 실제 현장에서 확인할 만한 기준을 넣어라.
- 도면, 시공, 원가, 공정, 품질 관점이 필요한 경우 자연스럽게 섞어라.
""",
    "realestate": """
부동산 글 말투:
- 계약 전 확인사항을 알려주는 생활형 블로그 느낌으로 작성해라.
- 단정적인 투자 조언은 피하고, 확인 순서와 리스크 중심으로 작성해라.
- 초보자가 헷갈리는 용어는 쉽게 풀어라.
- 매수, 전세, 청약, 분양 글은 실제 행동 전 체크해야 할 항목을 중심으로 작성해라.
""",
    "finance": """
금융 글 말투:
- 투자 권유처럼 보이지 않게 작성해라.
- 상승과 하락을 단정하지 말고 시나리오와 리스크를 같이 설명해라.
- 초보자가 감정적으로 매수하지 않도록 체크포인트 중심으로 작성해라.
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
- 독자가 바로 사용할 수 있는 판단 기준, 순서, 체크포인트를 넣어라.
- 너무 뻔한 일반론은 줄이고, 실제로 헷갈릴 만한 부분을 풀어라.
- 문단 중간에 자연스러운 전환 문장을 넣어라.
- 목록을 만들 때 모든 항목 길이를 똑같이 맞추지 마라.
- FAQ는 본문에서 이미 충분히 설명한 내용을 그대로 반복하지 마라.
- 글이 길어질 경우 중간에 읽는 사람이 숨을 고를 수 있는 짧은 문단을 넣어라.
- 실제 경험을 하지 않은 경우 '제가 직접 해보니', '제가 가보니', '먹어보니' 같은 표현은 쓰지 마라.
- 대신 '후기에서 자주 보이는 부분', '선택할 때 많이 보는 기준', '처음 확인할 부분'처럼 자연스럽게 표현해라.
"""


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
        "정리", "비교", "차이", "포인트", "체크", "질문", "FAQ",
        "장점", "단점", "스펙", "가격", "출시일", "성능", "구성",
        "주의", "방법", "대상", "어울립니다", "핵심", "요약",
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
- clean editorial cover image
- visually strong main subject
- realistic or polished editorial illustration style
- premium blog article feel
- simple and well-organized composition
- soft lighting and subtle shadows
- background should be clean and uncluttered
- leave enough empty space for title text overlay later
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
- no messy collage
- no excessive decorative elements
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
        "finance": "핵심 내용을 이해하기 위한 참고 이미지",
        "architecture": "현장·도면 개념을 돕는 참고 이미지",
        "realestate": "주요 포인트를 보여주는 참고 이미지",
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


def gemini_generate_text(prompt):
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
        "checklist": "체크리스트형",
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

    prompt = f"""
너는 ChickenBanana Lab 블로그의 한국어 SEO 전문 콘텐츠 작성자다.
목표는 네이버와 구글 검색엔진이 이해하기 쉬우면서도, 실제 사람이 읽었을 때 도움이 되는 글을 작성하는 것이다.

카테고리: {category_name}
주요 키워드: {keywords}
글 작성 방향: {style_name}
추가 요청사항: {extra_prompt}

글쓰기 세부 지침:
{style_specific_rule}

최신 사실 확인 규칙:
- 최신 제품, 출시일, 가격, 스펙, 뉴스, 정책, 일정, 금리, 코인, 주식, 법률, 세금처럼 시간이 지나면 바뀌는 정보는 반드시 최신 자료 기준으로 확인해라.
- Google Search grounding을 사용할 수 있으면 공식 홈페이지, 제조사 뉴스룸, 공신력 있는 매체, 판매처 정보를 우선 확인해라.
- 공식 출시된 제품을 루머로 쓰지 마라.
- 루머와 공식 정보를 혼동하지 마라.
- 공식 페이지나 제조사 발표가 확인되면 "루머", "예상", "가능성" 같은 표현을 남발하지 마라.
- 검색 결과와 사용자가 추가 요청사항에 준 정보가 충돌하면 공식 출처를 우선하고, 불확실한 부분은 "확인 필요"로 표시해라.
- MacBook Neo처럼 공식 제품 페이지 또는 제조사 뉴스룸에서 확인되는 제품은 공식 출시 제품 기준으로 작성해라.

{comparison_instruction}

{planned_title_instruction}

핵심 SEO 작성 원칙:
- 제목은 검색자가 실제로 입력할 만한 롱테일 키워드 형태로 작성해라.
- 첫 문단 150자 안에 핵심 키워드를 자연스럽게 포함해라.
- 본문은 h2, h3, p, ul, li, table, blockquote, mark, span 태그를 적절히 사용해라.
- 본문 최상단에 h1 태그는 절대 쓰지 마라.
- 핵심 요약은 필요할 때만 글 초반 또는 중간에 자연스럽게 넣어라.
- 모든 글에 똑같은 요약 박스를 반복하지 마라.
- 검색자가 궁금해할 질문과 답변을 FAQ 형태로 3개 이상 포함해라.
- 글 마지막에는 자연스러운 마무리와 한 줄 요약을 넣어라.
- 키워드를 억지로 반복하지 마라.
- 같은 표현, 같은 문장 패턴, 같은 소제목 구조를 반복하지 마라.
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

글 분량 판단 조건:
- 주제가 간단한 생활정보, 맛집 위치, 메뉴 소개, 짧은 이슈라면 핵심만 담아 900~1,300자 정도로 작성해라.
- 맛집/여행 소개형이면 위치, 메뉴, 방문 팁, 어울리는 사람 중심으로 1,100~1,600자 정도로 작성해라.
- 비교, 분석, 교육, 사용법, 투자 리스크, 개발 방법, 건축 실무처럼 설명이 필요한 내용이면 1,500~2,500자 정도로 작성해라.
- 프로그램 사용법, 개발 튜토리얼, 자동매매 로직, 건축 실무 체크리스트처럼 단계 설명이 필요한 글은 충분히 길게 작성해라.
- 독자가 이미 아는 일반론을 길게 늘리지 마라.
- 같은 말을 반복해서 글자 수를 채우지 마라.
- 짧은 글이어도 검색자가 궁금해하는 핵심 답변은 빠뜨리지 마라.
- 긴 글은 소제목, 표, 리스트를 활용해서 읽기 쉽게 나눠라.

사람이 쓴 글처럼 보이기 위한 이번 글의 개별 조건:
- 이번 글의 도입 방식: {human_opening_pattern}

{human_structure_pattern}

{category_voice_rule}

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
- 중요한 장소, 메뉴, 금액, 시간, 키워드는 <span class="blue-point">강조문구</span> 형태로 강조해라.
- 글 마지막은 딱딱한 결론보다 "이런 분들에게 어울립니다" 식으로 자연스럽게 정리해라.

FAQ 작성 조건:
- FAQ 소제목은 <h2>자주 묻는 질문</h2>로 작성해라.
- 질문은 <h3> 태그로 작성해라.
- 답변은 <p> 태그로 작성해라.
- 실제 검색자가 물어볼 만한 질문으로 작성해라.
- 너무 뻔한 질문만 넣지 마라.
- 본문에서 이미 말한 내용을 그대로 복붙하지 마라.

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
            fallback_content = "<h2>자료 확인이 필요한 주제입니다</h2><p>자동 글 생성 과정에서 주제와 맞지 않는 일반 쇼핑몰 정보가 감지되어 본문을 안전하게 대체했습니다. 이 주제는 제품명, 공식 스펙, 가격 자료를 추가 요청사항에 넣고 다시 생성하는 것이 좋습니다.</p>"

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
        content = "<h2>자료 확인이 필요한 주제입니다</h2><p>자동 글 생성 과정에서 주제와 맞지 않는 일반 쇼핑몰 정보가 감지되어 본문을 안전하게 대체했습니다. 제품명, 공식 스펙, 가격 자료를 추가 요청사항에 넣고 다시 생성해 주세요.</p>"

    content = repair_ai_content_html(content, title=title)
    content = ensure_required_comparison_table(content, category, keywords, style_rule_key, extra_prompt, planned_title)
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
        "thumbnail_text": str(data.get("thumbnail_text", keywords[:30]))[:100],
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

    prompt = f"""
You are an English SEO blog editor for ChickenBanana Lab.
Create an English version of the Korean blog post below.

Category: {category_name}
Original keywords: {source_keywords}
Original Korean title: {korean_title}

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
        "checklist": "체크리스트형",
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

이미 작성된 비슷한 제목:
{existing_title_text if existing_title_text else '- 없음'}

기획 조건:
- 총 {count}개의 주제를 만들어라.
- 각 글은 검색 의도가 서로 달라야 한다.
- 제목이 서로 비슷하면 안 된다.
- 같은 문장 구조를 반복하지 마라.
- 초보자용, 체크리스트, 비교, 리스크, 사례, 실전 방법, 주의점, 후기 분석, 방문 팁 등 관점을 나눠라.
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
        {"keyword": "건설현장 안전관리 체크리스트", "category": "건축", "reason": "전문 정보형 검색 유입 가능"},
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
