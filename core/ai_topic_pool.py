import json
import os
import re

from django.contrib import messages
from django.contrib.auth.decorators import user_passes_test
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from google.genai import types

from .ai_writer import RECENT_ISSUE_MODEL, get_gemini_client
from .models import AIFallbackTopic, Post


CATEGORY_CHOICES = [
    ("construction_work", "건설실무"),
    ("construction_tech", "건설기술"),
    ("construction_real", "건설부동산"),
    ("bim", "REVIT/BIM"),
    ("dynamo_automation", "Dynamo/자동화"),
    ("four_d_five_d", "4D/5D"),
    ("tech_ai_development", "AI·개발"),
    ("tech_data_security", "데이터·보안"),
    ("tech_server_software", "인터넷·서버·소프트"),
    ("program", "업무용 프로그램"),
    ("tool_recommend", "툴소개/툴추천"),
]

CATEGORY_LABELS = dict(CATEGORY_CHOICES)

CATEGORY_GUIDES = {
    "construction_work": "시공, 공정, 원가, 품질, 안전, 도면검토, 물량산출 등 건설 현장 실무",
    "construction_tech": "스마트건설, 건설 AI, 로봇, 드론, 디지털트윈, 모듈러, 신공법",
    "construction_real": "분양, 청약, 재건축·재개발, 공사비, 주택공급, 건설부동산",
    "bim": "Revit, BIM 모델링, 패밀리, 템플릿, 협업, 물량산출, 간섭검토, IFC",
    "dynamo_automation": "Dynamo, Revit API, Python, 파라미터, 엑셀 연동, BIM 반복업무 자동화",
    "four_d_five_d": "4D·5D BIM, Navisworks, 공정 시뮬레이션, 수량·원가·공정 연동",
    "tech_ai_development": "생성형 AI, Python, Django, API, 소프트웨어 개발, AI 에이전트",
    "tech_data_security": "데이터 보안, 개인정보, 백업, 인증, 취약점, 랜섬웨어, 권한관리",
    "tech_server_software": "서버, 클라우드, 네트워크, DNS, SSL, IPv4·IPv6, 리눅스, 웹서비스",
    "program": "업무용 프로그램, PDF·파일 관리, 문서 자동화, 화면녹화, 협업 소프트웨어",
    "tool_recommend": "AI·생산성·협업·개발·노코드 도구의 비교, 활용법, 추천 기준",
}

FORMAT_LABELS = {
    "workflow": "실무 절차",
    "checklist": "체크리스트",
    "troubleshooting": "문제 해결",
    "comparison": "비교·선택",
    "automation": "자동화·생산성",
    "case": "사례·트렌드",
}

DIFFICULTY_LABELS = {
    "beginner": "입문",
    "practical": "실무",
    "advanced": "심화",
}

TOPIC_MODEL = (
    os.getenv("GEMINI_FALLBACK_TOPIC_MODEL", "").strip()
    or RECENT_ISSUE_MODEL
)


def _admin_required(user):
    return bool(
        user
        and user.is_authenticated
        and user.is_active
        and (user.is_staff or user.is_superuser)
    )


def _normalize_title(value):
    value = re.sub(r"\s+", " ", str(value or "")).strip()
    return re.sub(r"[^0-9a-z가-힣]+", "", value.lower())


def _extract_response_text(response):
    text = str(getattr(response, "text", "") or "").strip()
    if text:
        return text

    chunks = []
    for candidate in getattr(response, "candidates", []) or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", []) or []:
            part_text = getattr(part, "text", None)
            if part_text:
                chunks.append(str(part_text))
    return "\n".join(chunks).strip()


def _parse_topic_payload(raw_text):
    text = str(raw_text or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text)

    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("Gemini 응답에서 JSON을 찾지 못했습니다.")
        payload = json.loads(text[start:end + 1])

    topics = payload.get("topics", []) if isinstance(payload, dict) else payload
    if not isinstance(topics, list):
        raise ValueError("Gemini 응답의 topics가 목록이 아닙니다.")
    return topics


def _existing_title_samples(category, limit=120):
    post_titles = list(
        Post.objects.filter(category=category)
        .order_by("-created_at")
        .values_list("title", flat=True)[:limit]
    )
    pool_titles = list(
        AIFallbackTopic.objects.filter(category=category)
        .order_by("-created_at")
        .values_list("title", flat=True)[:limit]
    )
    return post_titles + pool_titles


def generate_ai_fallback_topics(category, count, user=None):
    """관리자 요청 시에만 Gemini를 호출하여 검토대기 주제를 저장한다."""
    if category not in CATEGORY_LABELS:
        raise ValueError("지원하지 않는 카테고리입니다.")

    count = max(1, min(int(count or 10), 30))
    label = CATEGORY_LABELS[category]
    guide = CATEGORY_GUIDES[category]
    existing_titles = _existing_title_samples(category)
    existing_text = "\n".join(
        f"- {title}" for title in existing_titles[:160]
    ) or "- 없음"

    prompt = f"""
ChickenBananaLab의 '{label}' 카테고리에 사용할 고품질 상시형 글감 {count}개를 작성하세요.

카테고리 범위:
{guide}

이미 작성되었거나 저장된 제목:
{existing_text}

필수 조건:
1. 기존 제목과 같거나 의미가 거의 같은 주제를 만들지 마세요.
2. 실제 업무자가 검색할 만한 구체적인 한국어 제목을 작성하세요.
3. 확인되지 않은 최신 사건, 수치, 제품 가격, 특정 날짜를 제목에 넣지 마세요.
4. 낚시성 표현, 과장, 광고 문구를 사용하지 마세요.
5. 다음 6개 형식을 균형 있게 포함하세요:
   workflow, checklist, troubleshooting, comparison, automation, case
6. 난이도는 beginner, practical, advanced를 균형 있게 포함하되 practical을 가장 많이 배치하세요.
7. 제목 길이는 18~70자 범위로 작성하세요.

반드시 아래 JSON 형식만 반환하세요.
{{
  "topics": [
    {{
      "title": "구체적인 한국어 제목",
      "format": "workflow",
      "difficulty": "practical",
      "note": "이 주제가 유용한 이유를 한 문장으로 설명"
    }}
  ]
}}
""".strip()

    client = get_gemini_client()
    if client is None:
        raise RuntimeError(
            "GEMINI_API_KEY가 설정되지 않아 기본글감을 생성할 수 없습니다."
        )
    response = client.models.generate_content(
        model=TOPIC_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.65,
            max_output_tokens=8192,
        ),
    )
    topics = _parse_topic_payload(_extract_response_text(response))

    known = {
        _normalize_title(title)
        for title in existing_titles
        if _normalize_title(title)
    }
    created = []

    with transaction.atomic():
        for item in topics:
            if not isinstance(item, dict):
                continue

            title = re.sub(
                r"\s+", " ", str(item.get("title") or "")
            ).strip()
            normalized = _normalize_title(title)
            if not title or len(title) > 220 or not normalized:
                continue
            if normalized in known:
                continue

            content_format = str(
                item.get("format") or "workflow"
            ).strip()
            if content_format not in FORMAT_LABELS:
                content_format = "workflow"

            difficulty = str(
                item.get("difficulty") or "practical"
            ).strip()
            if difficulty not in DIFFICULTY_LABELS:
                difficulty = "practical"

            topic, was_created = AIFallbackTopic.objects.get_or_create(
                category=category,
                normalized_title=normalized,
                defaults={
                    "title": title,
                    "content_format": content_format,
                    "difficulty": difficulty,
                    "status": AIFallbackTopic.STATUS_PENDING,
                    "note": str(item.get("note") or "")[:500],
                    "source_model": TOPIC_MODEL[:160],
                    "created_by": user,
                },
            )
            if was_created:
                known.add(normalized)
                created.append(topic)

    return created


@user_passes_test(_admin_required)
def ai_fallback_topic_manage(request):
    if request.method == "POST":
        action = str(request.POST.get("action") or "").strip()

        if action == "generate":
            category = str(request.POST.get("category") or "").strip()
            try:
                count = max(
                    1, min(int(request.POST.get("count") or 10), 30)
                )
            except (TypeError, ValueError):
                count = 10

            categories = (
                list(CATEGORY_LABELS)
                if category == "all"
                else [category]
            )
            if not categories or any(
                item not in CATEGORY_LABELS for item in categories
            ):
                messages.error(request, "카테고리를 확인해 주세요.")
                return redirect("ai_fallback_topic_manage")

            total_created = 0
            failed = []
            for item_category in categories:
                try:
                    created = generate_ai_fallback_topics(
                        item_category, count, request.user
                    )
                    total_created += len(created)
                except Exception as error:
                    failed.append(
                        f"{CATEGORY_LABELS[item_category]}: {str(error)[:100]}"
                    )

            if total_created:
                messages.success(
                    request,
                    f"AI 기본글감 {total_created}개를 검토대기로 저장했습니다.",
                )
            if failed:
                messages.error(
                    request,
                    "일부 생성 실패 · " + " / ".join(failed[:4]),
                )
            return redirect("ai_fallback_topic_manage")

        if action in {"approve", "pending", "reject"}:
            topic = get_object_or_404(
                AIFallbackTopic,
                pk=request.POST.get("topic_id"),
            )
            topic.status = {
                "approve": AIFallbackTopic.STATUS_APPROVED,
                "pending": AIFallbackTopic.STATUS_PENDING,
                "reject": AIFallbackTopic.STATUS_REJECTED,
            }[action]
            topic.approved_at = (
                timezone.now()
                if topic.status == AIFallbackTopic.STATUS_APPROVED
                else None
            )
            topic.save(update_fields=[
                "status", "approved_at", "updated_at"
            ])
            messages.success(request, "글감 상태를 변경했습니다.")
            return redirect("ai_fallback_topic_manage")

        if action == "delete":
            topic = get_object_or_404(
                AIFallbackTopic,
                pk=request.POST.get("topic_id"),
            )
            topic.delete()
            messages.success(request, "글감을 삭제했습니다.")
            return redirect("ai_fallback_topic_manage")

        if action == "bulk_approve":
            category = str(request.POST.get("category") or "").strip()
            queryset = AIFallbackTopic.objects.filter(
                status=AIFallbackTopic.STATUS_PENDING
            )
            if category in CATEGORY_LABELS:
                queryset = queryset.filter(category=category)
            count = queryset.update(
                status=AIFallbackTopic.STATUS_APPROVED,
                approved_at=timezone.now(),
            )
            messages.success(request, f"검토대기 글감 {count}개를 승인했습니다.")
            return redirect("ai_fallback_topic_manage")

    selected_category = str(
        request.GET.get("category") or "all"
    ).strip()
    selected_status = str(
        request.GET.get("status") or "all"
    ).strip()

    queryset = AIFallbackTopic.objects.all().order_by(
        "-created_at", "-id"
    )
    if selected_category in CATEGORY_LABELS:
        queryset = queryset.filter(category=selected_category)
    if selected_status in {
        AIFallbackTopic.STATUS_PENDING,
        AIFallbackTopic.STATUS_APPROVED,
        AIFallbackTopic.STATUS_REJECTED,
    }:
        queryset = queryset.filter(status=selected_status)

    paginator = Paginator(queryset, 50)
    page_obj = paginator.get_page(request.GET.get("page") or 1)

    aggregate = AIFallbackTopic.objects.aggregate(
        total=Count("id"),
        pending=Count(
            "id", filter=Q(status=AIFallbackTopic.STATUS_PENDING)
        ),
        approved=Count(
            "id", filter=Q(status=AIFallbackTopic.STATUS_APPROVED)
        ),
        rejected=Count(
            "id", filter=Q(status=AIFallbackTopic.STATUS_REJECTED)
        ),
    )

    category_stats = []
    for category, label in CATEGORY_CHOICES:
        category_stats.append({
            "slug": category,
            "label": label,
            "approved": AIFallbackTopic.objects.filter(
                category=category,
                status=AIFallbackTopic.STATUS_APPROVED,
            ).count(),
            "pending": AIFallbackTopic.objects.filter(
                category=category,
                status=AIFallbackTopic.STATUS_PENDING,
            ).count(),
        })

    return render(request, "core/ai_fallback_topic_manage.html", {
        "page_obj": page_obj,
        "aggregate": aggregate,
        "category_choices": CATEGORY_CHOICES,
        "category_stats": category_stats,
        "selected_category": selected_category,
        "selected_status": selected_status,
        "topic_model": TOPIC_MODEL,
    })
